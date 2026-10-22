#!/usr/bin/env python3
"""
Layered path discovery for the client-side tool permission gate (ADR-0007).

The gate needs to know which filesystem paths a tool call would touch before
it dispatches.  Declared ``checkpaths`` alone only cover Klea-authored tools,
so discovery is layered and tool-agnostic:

* ``declared`` -- the tool's declared ``checkpaths`` argument values;
* ``expected`` -- paths the tools picker declared for the call;
* ``guess`` -- heuristic: argument names that look path-ish, plus path-like
  values (a shell command string is split with :func:`shlex.split` first).

Only paths that resolve outside the permitted roots (``project_root`` plus any
session-approved ``allowed_dirs``) become :class:`PathRequest` objects.  This
is advisory: a tool can ignore its arguments and touch any path at runtime;
only OS sandboxing confines it (ADR-0007).

See ``devdocs/system/mcp-permissions.md``.

File: klea_utils/mcp/path_detect.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel, Field

from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.sensitive import is_sensitive
from klea_utils.mcp.tool_impls.permission import path_is_allowed, permitted_roots
from klea_utils.tool_args import is_path_argument_name

logger = logging.getLogger(__name__)

#: Confidence that a discovered string really is a path, strongest first.
Confidence = Literal["declared", "expected", "guess"]

#: Display caps for permission prompts.  The full path and approval key stay in
#: the request; only the rendered labels are bounded so a pathological guessed
#: string cannot flood the ask.
MAX_PATH_DISPLAY_CHARS = 240
MAX_RAW_DISPLAY_CHARS = 160
MAX_ARGUMENT_PREVIEW_CHARS = 160


def truncate_middle(text: str, max_chars: int = MAX_PATH_DISPLAY_CHARS) -> str:
    """Truncate *text* in the middle, keeping its start and end.

    Used for display only.  The start (root/directory) and the end (filename)
    are the useful parts of a path; the omitted span is replaced with an
    explicit marker so a pathological guessed value is recognisable.

    :param text: The text to bound.
    :param max_chars: Maximum number of visible characters to keep.
    :returns: The text unchanged when short, otherwise a middle-truncated
        preview with an omission marker.
    """
    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    head = (max_chars * 2) // 3
    tail = max_chars - head
    omitted = len(text) - head - tail
    return f"{text[:head]}...[{omitted} chars omitted]...{text[-tail:]}"


def _preview_context(
    value: str, token: str, max_chars: int = MAX_ARGUMENT_PREVIEW_CHARS
) -> str:
    """Return a bounded window of *value* around *token*.

    Keeps the detected token in view when it occurs in a long command or file
    content string, so the user can tell whether it is a path or ordinary
    text.  Falls back to middle truncation when the token cannot be located.

    :param value: The full argument value.
    :param token: The detected path-like token within it.
    :param max_chars: Maximum number of visible characters to keep.
    :returns: The bounded preview.
    """
    if max_chars <= 0:
        return ""
    text = value.strip()
    if len(text) <= max_chars:
        return text
    index = text.find(token) if token else -1
    if index < 0:
        return truncate_middle(text, max_chars)
    window = min(len(token), max_chars)
    before = (max_chars - window) // 2
    start = max(0, index - before)
    end = min(len(text), start + max_chars)
    prefix = "... " if start > 0 else ""
    suffix = " ..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


class PathRequest(BaseModel):
    """One path a tool call would touch that needs approval.

    :attr:`raw` is the string as discovered; :attr:`path` is its resolved
    location; :attr:`directory` is the directory to approve (the path itself
    when it is a directory, else its parent).  :attr:`confidence` and
    :attr:`source` describe how it was found, so the prompt can show a guessed
    path differently from a declared one.

    :attr:`kind` is ``outside`` (the path is outside the permitted roots) or
    ``sensitive`` (a credential-bearing file inside them).  ``outside`` is
    approved per directory, ``sensitive`` per file (:attr:`approval_key`).

    :attr:`argument_preview` is a bounded, token-centred preview of the full
    argument value, used by the approval UI.  :attr:`path` and
    :attr:`directory` remain full values for decisions and auditing; use the
    display helpers when rendering them.

    :attr:`covered_paths` lists every distinct resolved path that maps to this
    request's :attr:`approval_key` (all files in an approved directory, or the
    single sensitive file).  It lets the ask show what one session approval
    covers.
    """

    tool: str
    argument: str
    raw: str
    path: str
    directory: str
    confidence: Confidence
    source: str
    kind: Literal["outside", "sensitive"] = "outside"
    argument_preview: str = ""
    covered_paths: list[str] = Field(default_factory=list)

    @property
    def approval_key(self) -> str:
        """Return the key a decision for this request is matched on.

        The resolved file for a ``sensitive`` request, the resolved directory
        for an ``outside`` one.
        """
        return self.path if self.kind == "sensitive" else self.directory

    def path_display(self) -> str:
        """Return a bounded display label for the resolved path."""
        return truncate_middle(self.path)

    def directory_display(self) -> str:
        """Return a bounded display label for the approval directory."""
        return truncate_middle(self.directory)

    def raw_preview(self) -> str:
        """Return a bounded display label for the discovered token."""
        return truncate_middle(self.raw, MAX_RAW_DISPLAY_CHARS)


def declared_checkpaths(tool_info: ToolInfo | None) -> list[str]:
    """Return the tool's declared path-argument names.

    Reads ``ToolInfo.checkpaths`` or the folded ``ToolInfo.meta['checkpaths']``
    (set by ``register_tools``); empty when the tool declares none.

    :param tool_info: Tool metadata, or ``None``.
    :returns: Declared path-argument names.
    """
    if tool_info is None:
        return []
    if tool_info.checkpaths:
        return list(tool_info.checkpaths)
    if tool_info.meta:
        folded = tool_info.meta.get("checkpaths")
        if isinstance(folded, list):
            return [str(name) for name in folded]
    return []


def _strip_file_uri(token: str) -> str | None:
    """Return the filesystem path of a ``file:`` URI, or ``None``.

    Only ``file:`` URIs name a local path; other schemes (``http``, ...) are
    network references.  A remote-host ``file://host/share`` URI is unusual on
    POSIX but its path component is still returned (conservatively checked).
    """
    if not token.lower().startswith("file:"):
        return None
    parsed = urlparse(token)
    path = unquote(parsed.path)
    return path or None


def _safe_expanduser(value: str | os.PathLike) -> Path | None:
    """Expand ``~`` without letting a missing home directory abort detection.

    ``Path.expanduser`` raises ``RuntimeError`` when the home directory cannot
    be determined.  Path discovery is advisory: a guessed token (for example a
    ``~nonsense`` fragment inside a code string) must be skipped, not crash the
    tool dispatch.

    :param value: The raw path-like value.
    :returns: The expanded path, or ``None`` when it cannot be expanded.
    """
    try:
        return Path(value).expanduser()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(f"Could not expand path {value!r}: {exc}; skipping")
        return None


def _safe_resolved(value: str | os.PathLike) -> Path | None:
    """Return a resolved path, or ``None`` when resolution fails.

    Used for the boundary itself and session approvals, where a crash would
    abort every tool call.

    :param value: The path to resolve.
    :returns: The resolved path, or ``None``.
    """
    path = _safe_expanduser(value)
    if path is None:
        return None
    try:
        return path.resolve()
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(f"Could not resolve path {value!r}: {exc}; skipping")
        return None


def _is_nonfile_url(token: str) -> bool:
    """Return whether a token is a URL other than a ``file:`` URI."""
    if "://" not in token:
        return False
    scheme = token.split("://", 1)[0].lower()
    return scheme != "file"


def _looks_pathlike(token: str) -> bool:
    """Return whether a cleaned shell/argument token looks like a path."""
    if not token:
        return False
    if token.startswith("-"):
        # A flag; an ``--opt=value`` form is unwrapped before this call.
        return False
    if _is_nonfile_url(token):
        # A network URL, not a filesystem path (``file:`` URIs are unwrapped
        # before this call).
        return False
    if token[0] in "*?[":
        # A pure glob/regex fragment with no location.
        return False
    if token in (".", ".."):
        return False
    if token.startswith(("/", "~", "./", "../")):
        return True
    return "/" in token or "\\" in token


def _normalize_token(token: str) -> str:
    """Strip redirections, assignments and shell operators from a token."""
    # ``2>/tmp/x`` / ``>/tmp/x``: keep the part after the redirection.
    if ">" in token:
        token = token.rsplit(">", 1)[1]
    if "<" in token:
        token = token.rsplit("<", 1)[1]
    token = token.strip()
    # Unquoted shell operators are separate tokens (see split_shell_tokens),
    # but a quoted command substitution such as ``"$(wc -l < /tmp/x);"``
    # still carries its closing ``)`` and a trailing literal ``;``.
    token = token.removeprefix("$(")
    token = token.strip("(){}")
    while token and token[-1] in ";|&)":
        token = token[:-1]
    token = token.strip()
    if "=" in token:
        left, right = token.split("=", 1)
        # ``FOO=/etc`` env assignment or ``--out=/etc`` long option value.
        if left and left.lstrip("-").replace("_", "").isalnum():
            token = right
    return token


def platform_is_windows() -> bool:
    """Return whether arguments should be split with Windows quoting rules.

    Backslashes are literal path separators on Windows, so POSIX ``shlex``
    escape processing (``a\\ b``) must not be applied there.  Kept as a function
    so tests can pin either behaviour on any platform.

    :returns: ``True`` when running on Windows.
    """
    return os.name == "nt"


def split_arguments(text: str, *, windows: bool | None = None) -> list[str]:
    """Split *text* into shell-like argument tokens for the current platform.

    On POSIX this is :func:`shlex.split` unchanged: quotes group tokens,
    backslash escapes are honoured, and unbalanced quotes raise
    :class:`ValueError`.  On Windows the split is non-POSIX, so backslashes stay
    literal (``C:\\Users\\me\\a.txt`` is a single token); one enclosing quote
    pair is stripped from each token, and unbalanced quotes still raise
    :class:`ValueError`.

    :param text: The argument string to split.
    :param windows: Force Windows (``True``) or POSIX (``False``) rules;
        defaults to :func:`platform_is_windows`.
    :returns: The argument tokens.
    """
    if windows is None:
        windows = platform_is_windows()
    if not windows:
        return shlex.split(text)
    tokens = shlex.split(text, posix=False)
    stripped: list[str] = []
    for token in tokens:
        if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
            token = token[1:-1]
        stripped.append(token)
    return stripped


def split_shell_tokens(text: str, *, windows: bool | None = None) -> list[str]:
    """Split a shell command into tokens, separating control operators.

    Unlike :func:`split_arguments` (used for ``/command`` arguments, where a
    ``;`` may be part of a value), this uses :mod:`shlex`'s punctuation mode so
    ``;``, ``|``, ``&``, ``<`` and ``>`` are separate tokens instead of
    clinging to a path.  Quotes still group a token, so a quoted path with
    spaces or shell characters stays intact.  Comments are disabled (as in
    :func:`shlex.split`): ``#`` is a literal character, not a comment marker.
    ``whitespace_split`` is enabled so ``=``, ``:`` and ``#`` do not split a
    token; only whitespace and the punctuation characters do.

    :param text: The shell command string.
    :param windows: Force Windows (``True``) or POSIX (``False``) rules;
        defaults to :func:`platform_is_windows`.
    :returns: The argument and operator tokens.
    """
    if windows is None:
        windows = platform_is_windows()
    lexer = shlex.shlex(
        text,
        posix=not windows,
        punctuation_chars="();<>|&",
    )
    lexer.whitespace_split = True
    lexer.commenters = ""
    tokens = list(lexer)
    if windows:
        stripped: list[str] = []
        for token in tokens:
            if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
                token = token[1:-1]
            stripped.append(token)
        return stripped
    return tokens


def _tokens_for_value(value: str) -> list[str]:
    """Split a string value into candidate tokens.

    A whitespace-containing value is treated as a shell command string and
    split with :func:`split_shell_tokens` (falling back to ``str.split`` on a
    quoting error); a single-token value (a plain path) is returned as-is.
    Windows quoting keeps backslash separators literal.
    """
    text = value.strip()
    if not text:
        return []
    if not any(ch.isspace() for ch in text):
        return [text]
    try:
        return split_shell_tokens(text)
    except ValueError:
        logger.debug("Could not split value; falling back to whitespace")
        return text.split()


def _resolve_against(text: str, root: Path) -> Path | None:
    """Resolve *text* against *root* (for relative inputs), or ``None``.

    Non-strict: the target need not exist.  A ``file:`` URI is unwrapped to
    its filesystem path first; a symlink loop is returned as ``None`` so it is
    not turned into a misleading approval request.
    """
    if not text.strip():
        return None
    try:
        text = _strip_file_uri(text) or text
        candidate = _safe_expanduser(text)
        if candidate is None:
            return None
        if not candidate.is_absolute():
            candidate = root / candidate
        elif not candidate.drive and Path(root.anchor).drive:
            # Windows: a drive-less rooted token (``/``, ``/etc/x``) borrows the
            # process working directory's drive in resolve(); anchor it to the
            # project root's drive (the session boundary) instead.
            candidate = Path(root.anchor) / candidate.relative_to(candidate.anchor)
        return candidate.resolve(strict=False)
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(f"Could not resolve guessed path {text!r}: {exc}; skipping")
        return None


def detect_path_requests(
    tool: str,
    arguments: Mapping[str, Any],
    tool_info: ToolInfo | None = None,
    *,
    project_root: str | os.PathLike | None = None,
    allowed_dirs: Sequence[str] | None = None,
    allowed_files: Sequence[str] | None = None,
    picker_paths: Sequence[str] | None = None,
    include_sensitive: bool = False,
) -> list[PathRequest]:
    """Return the paths a tool call would touch that need approval.

    Layered discovery (see the module docstring) unions declared
    ``checkpaths`` values, picker-declared paths, and heuristics.  Two rounds
    become requests: paths outside the permitted roots (``kind="outside"``,
    approved per directory) and, when *include_sensitive* is set, sensitive
    files inside them (``kind="sensitive"``, approved per file).  Requests are
    de-duplicated by :attr:`PathRequest.approval_key`, keeping the strongest
    confidence tier.

    :param tool: Tool name (recorded on each request).
    :param arguments: The arguments the call would pass.
    :param tool_info: Tool metadata (for ``checkpaths``), or ``None``.
    :param project_root: Boundary directory; defaults to the current directory.
    :param allowed_dirs: Additional permitted directories (session approvals).
    :param allowed_files: Sensitive files already approved for the session.
    :param picker_paths: Paths the picker declared for this call, or ``None``.
    :param include_sensitive: Also request sensitive files inside the roots
        (the permission second round).
    :returns: Approval requests, sorted by approval key.
    """
    logger.debug(
        f"Detecting path requests\n{tool = }\n{arguments = }\n"
        f"{project_root = }\n{allowed_dirs = }\n{allowed_files = }\n"
        f"{picker_paths = }\n{include_sensitive = }"
    )
    root = _safe_resolved(project_root) if project_root else Path.cwd().resolve()
    if root is None:
        logger.warning("Could not resolve project root; using the current directory")
        root = Path.cwd().resolve()
    roots = permitted_roots(project_root, allowed_dirs)
    approved_files = {
        str(resolved)
        for entry in (allowed_files or [])
        if str(entry).strip() and (resolved := _safe_resolved(entry)) is not None
    }
    requests: dict[str, PathRequest] = {}

    def consider(
        raw: Any,
        argument: str,
        confidence: Confidence,
        source: str,
        *,
        argument_value: str,
    ) -> None:
        resolved = _resolve_against(str(raw), root)
        if resolved is None:
            return
        if path_is_allowed(resolved, roots):
            # Inside a permitted root: only a sensitive file needs approval,
            # and not when it was already approved for the session.
            if (
                not include_sensitive
                or not is_sensitive(resolved)
                or str(resolved) in approved_files
            ):
                return
            kind: Literal["outside", "sensitive"] = "sensitive"
            key = str(resolved)
            directory = resolved.parent
        else:
            kind = "outside"
            directory = resolved if resolved.is_dir() else resolved.parent
            key = str(directory)
        path_str = str(resolved)
        if key in requests:
            # A stronger tier (encountered first) already covers this directory;
            # keep every distinct path so the ask can show its coverage.
            existing = requests[key]
            if path_str not in existing.covered_paths:
                existing.covered_paths.append(path_str)
            return
        requests[key] = PathRequest(
            tool=tool,
            argument=argument,
            raw=os.path.normpath(str(raw)),
            path=os.path.normpath(str(resolved)),
            directory=str(directory),
            confidence=confidence,
            source=source,
            kind=kind,
            argument_preview=_preview_context(str(argument_value), str(raw)),
            covered_paths=[path_str],
        )

    declared = declared_checkpaths(tool_info)
    declared_values = {name.lower() for name in declared}

    # 1. Declared checkpaths: the authoritative, deterministic tier.
    for name in declared:
        value = arguments.get(name)
        if isinstance(value, (str, os.PathLike)):
            consider(
                value,
                name,
                "declared",
                f"checkpaths:{name}",
                argument_value=str(value),
            )

    # 2. Paths the picker declared for the call.
    for raw in picker_paths or []:
        if isinstance(raw, str) and raw.strip():
            consider(
                raw,
                "paths",
                "expected",
                "picker",
                argument_value=raw,
            )

    # 3. Argument names that look path-ish.
    for name, value in arguments.items():
        if name.lower() in declared_values or not isinstance(value, str):
            continue
        if is_path_argument_name(name):
            consider(
                value,
                name,
                "guess",
                f"name:{name}",
                argument_value=value,
            )

    # 4. Path-like values, including tokens of shell command strings.  A bare
    #    sensitive filename (``cat .env``) is not path-like but is considered.
    for name, value in arguments.items():
        if not isinstance(value, str):
            continue
        for token in _tokens_for_value(value):
            candidate = _normalize_token(token)
            if _looks_pathlike(candidate) or (
                include_sensitive and is_sensitive(candidate)
            ):
                consider(
                    candidate,
                    name,
                    "guess",
                    f"value:{name}",
                    argument_value=value,
                )

    logger.debug(f"Detected path requests\n{len(requests) = }\n{list(requests) = }")
    return sorted(requests.values(), key=lambda request: request.approval_key)


def _resolve_relative_value(value: str, root: Path) -> str | None:
    """Return the absolute form of a single relative path *value*, or ``None``.

    ``None`` means "leave the value as-is": empty, a URL (non-``file:``),
    a ``file:`` URI, a flag, a glob fragment, or already absolute.  A ``file:``
    URI is left untouched because it is not a bare filesystem path argument.
    """
    if not value.strip():
        return None
    text = value.strip()
    if _strip_file_uri(text) is not None or _is_nonfile_url(text):
        return None
    if text.startswith("-") or text[0] in "*?[":
        return None
    try:
        candidate = _safe_expanduser(text)
        if candidate is None:
            return None
        if candidate.is_absolute():
            return None
        return str((root / candidate).resolve())
    except (OSError, RuntimeError, ValueError) as exc:
        logger.debug(f"Could not resolve path value {text!r}: {exc}; leaving as-is")
        return None


def resolve_path_arguments(
    arguments: Mapping[str, Any],
    tool_info: ToolInfo | None,
    *,
    project_root: str | os.PathLike | None = None,
) -> dict[str, Any]:
    """Return *arguments* with relative filesystem paths made absolute.

    The tools run in the MCP subprocess, whose working directory is fixed at
    spawn, so a per-session project root cannot reach them via ``chdir``.
    Resolving relative path arguments to absolute against the session root here
    lets any local tool operate under the root regardless of its own cwd, and
    works for third-party tools too (it does not depend on the tool being
    Klea-authored).

    Rules:

    * a declared ``checkpaths`` argument is always resolved (string or
      ``list[str]`` value);
    * other arguments are resolved heuristically when the argument **name** is
      path-ish (:func:`klea_utils.tool_args.is_path_argument_name`) and the
      value is a **single-token**, non-URL/non-flag/non-glob string
      (multi-token shell command strings are never rewritten);
    * ``working_directory`` (the ``run_command`` convention) defaults to the
      session root when unset;
    * absolute paths and URLs are left unchanged.

    :param arguments: The call's bound arguments.
    :param tool_info: The tool's metadata (for ``checkpaths``), or ``None``.
    :param project_root: Session root; defaults to the process working
        directory.
    :returns: A copy of *arguments* with relative path values made absolute.
    """
    if not arguments:
        return dict(arguments)
    root = (
        Path(project_root).expanduser().resolve()
        if project_root
        else Path.cwd().resolve()
    )
    declared = {name.lower() for name in declared_checkpaths(tool_info)}
    result = dict(arguments)
    for name, value in arguments.items():
        name_lower = name.lower()
        is_declared = name_lower in declared
        # ``run_command`` runs in the project root when no directory is given.
        if name_lower == "working_directory" and not value:
            result[name] = str(root)
            continue
        if isinstance(value, list):
            if not is_declared:
                continue
            resolved_items = [
                _resolve_relative_value(item, root) or item
                for item in value
                if isinstance(item, str)
            ]
            if len(resolved_items) == len(value):
                result[name] = resolved_items
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        heuristic = is_path_argument_name(name) and not any(
            ch.isspace() for ch in value.strip()
        )
        if not (is_declared or heuristic):
            continue
        resolved = _resolve_relative_value(value, root)
        if resolved is not None:
            result[name] = resolved
    # ``run_command``'s working directory may be omitted entirely; default it to
    # the session root when the tool declares it.
    if "working_directory" in declared:
        key = next(
            (name for name in arguments if name.lower() == "working_directory"),
            "working_directory",
        )
        if not result.get(key):
            result[key] = str(root)
    logger.debug(f"Resolved path arguments\n{arguments = }\n{result = }\n{root = }")
    return result
