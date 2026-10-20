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
import re
import shlex
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlparse

from pydantic import BaseModel

from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.sensitive import is_sensitive
from klea_utils.mcp.tool_impls.permission import path_is_allowed, permitted_roots

logger = logging.getLogger(__name__)

#: Confidence that a discovered string really is a path, strongest first.
Confidence = Literal["declared", "expected", "guess"]

#: Argument-name words that mark a value as path-like (snake/kebab-case parts).
_PATHY_NAME_WORDS = frozenset(
    {
        "path",
        "paths",
        "file",
        "files",
        "filename",
        "filepath",
        "dir",
        "dirs",
        "folder",
        "directory",
        "root",
        "cwd",
        "workdir",
    }
)


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
    """

    tool: str
    argument: str
    raw: str
    path: str
    directory: str
    confidence: Confidence
    source: str
    kind: Literal["outside", "sensitive"] = "outside"

    @property
    def approval_key(self) -> str:
        """Return the key a decision for this request is matched on.

        The resolved file for a ``sensitive`` request, the resolved directory
        for an ``outside`` one.
        """
        return self.path if self.kind == "sensitive" else self.directory


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


def _name_is_pathy(name: str) -> bool:
    """Return whether an argument *name* looks like it holds a path."""
    return any(
        part in _PATHY_NAME_WORDS for part in re.split(r"[_\-\s]+", name.lower())
    )


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
    """Strip redirections and ``name=value`` assignments from a token."""
    # ``2>/tmp/x`` / ``>/tmp/x``: keep the part after the redirection.
    if ">" in token:
        token = token.rsplit(">", 1)[1]
    if "<" in token:
        token = token.rsplit("<", 1)[1]
    token = token.strip("(){}")
    if "=" in token:
        left, right = token.split("=", 1)
        # ``FOO=/etc`` env assignment or ``--out=/etc`` long option value.
        if left and left.lstrip("-").replace("_", "").isalnum():
            token = right
    return token


def _tokens_for_value(value: str) -> list[str]:
    """Split a string value into candidate tokens.

    A whitespace-containing value is treated as a shell command string and
    split with :func:`shlex.split` (falling back to ``str.split`` on a quoting
    error); a single-token value (a plain path) is returned as-is.
    """
    text = value.strip()
    if not text:
        return []
    if not any(ch.isspace() for ch in text):
        return [text]
    try:
        return shlex.split(text)
    except ValueError:
        logger.debug("Could not shlex-split value; falling back to whitespace")
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

    def consider(raw: Any, argument: str, confidence: Confidence, source: str) -> None:
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
        if key in requests:
            # A stronger tier (encountered first) already covers this path.
            return
        requests[key] = PathRequest(
            tool=tool,
            argument=argument,
            raw=str(raw),
            path=str(resolved),
            directory=str(directory),
            confidence=confidence,
            source=source,
            kind=kind,
        )

    declared = declared_checkpaths(tool_info)
    declared_values = {name.lower() for name in declared}

    # 1. Declared checkpaths: the authoritative, deterministic tier.
    for name in declared:
        value = arguments.get(name)
        if isinstance(value, (str, os.PathLike)):
            consider(value, name, "declared", f"checkpaths:{name}")

    # 2. Paths the picker declared for the call.
    for raw in picker_paths or []:
        if isinstance(raw, str) and raw.strip():
            consider(raw, "paths", "expected", "picker")

    # 3. Argument names that look path-ish.
    for name, value in arguments.items():
        if name.lower() in declared_values or not isinstance(value, str):
            continue
        if _name_is_pathy(name):
            consider(value, name, "guess", f"name:{name}")

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
                consider(candidate, name, "guess", f"value:{name}")

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
      path-ish (:func:`_name_is_pathy`) and the value is a **single-token**,
      non-URL/non-flag/non-glob string (multi-token shell command strings are
      never rewritten);
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
        heuristic = _name_is_pathy(name) and not any(
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
