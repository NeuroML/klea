#!/usr/bin/env python3
"""
ripgrep backend for the read-only search tools.

File: klea_utils/mcp/tool_impls/rg_backend.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import asyncio
import functools
import json
import logging
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access
from klea_utils.mcp.tool_impls.run_command import _terminate_process_group
from klea_utils.mcp.tool_impls.walk import SKIP_DIRECTORIES

logger = logging.getLogger(__name__)

#: Distribution that provides a pinned ripgrep binary.  Built and published
#: by Astral (https://astral.sh) from a pinned upstream ripgrep commit; see
#: the ``[search]`` extra in ``setup.cfg``.  We deliberately do not probe
#: ``PATH``: only this known, pinned artifact is used, so the read-only
#: guarantee of the search tools does not depend on the host environment.
RG_DISTRIBUTION = "astral-dev-toolchain-ripgrep"

#: Filenames the ripgrep executable can have, per platform.
_RG_EXECUTABLE_NAMES = frozenset({"rg", "rg.exe"})

#: Default wall-clock cap for one ripgrep invocation.
DEFAULT_SEARCH_TIMEOUT_SECONDS = 30.0

#: Maximum size of a single ``--json`` record accepted from ripgrep.
_MAX_RECORD_BYTES = 64 * 1024

#: Maximum bytes of ripgrep stderr kept for error reporting.
_RG_STDERR_BYTES = 8 * 1024

#: Longest match line ripgrep emits (further characters are dropped by rg).
_MAX_COLUMNS = 500


@functools.lru_cache(maxsize=1)
def resolve_rg() -> str | None:
    """Return the path to the pinned ripgrep executable, or ``None``.

    Locates the binary via the installed :data:`RG_DISTRIBUTION`
    distribution's ``RECORD`` (``importlib.metadata``), so it works whether
    the wheel places the executable in the environment's scripts directory
    or ships it as package data.  No ``PATH`` lookup is performed.

    The result is cached for the process lifetime: the installed set of
    distributions does not change while the server runs.  Tests can reset it
    with ``resolve_rg.cache_clear()``.

    :returns: Absolute path to the executable when the distribution is
        installed and provides an ``rg``/``rg.exe`` file; ``None`` otherwise.
    """
    try:
        dist = distribution(RG_DISTRIBUTION)
    except PackageNotFoundError:
        logger.debug(
            f"{RG_DISTRIBUTION} is not installed; "
            "search tools will use the in-house walker"
        )
        return None

    for record in dist.files or []:
        if Path(str(record)).name.lower() not in _RG_EXECUTABLE_NAMES:
            continue
        path = Path(str(dist.locate_file(record)))
        if path.is_file():
            logger.debug(f"Resolved ripgrep executable: {path}")
            return str(path.resolve())
        logger.warning(f"ripgrep record {record} does not exist at {path}")

    logger.warning(
        f"{RG_DISTRIBUTION} {dist.version} is installed but provides no "
        "rg executable; search tools will use the in-house walker"
    )
    return None


def _skip_dir_globs() -> list[str]:
    """Build the ``--glob`` exclusions for :data:`SKIP_DIRECTORIES`.

    :returns: A flat list of ``--glob``/pattern argument pairs.
    """
    globs: list[str] = []
    for name in sorted(SKIP_DIRECTORIES):
        globs += ["--glob", f"!**/{name}/**"]
    return globs


def _normalize_path(text: str) -> str:
    """Normalize a ripgrep-reported path to a root-relative POSIX path.

    :param text: Path as emitted by ripgrep (may carry a leading ``./`` or
        backslash separators on Windows).
    :returns: The path with ``/`` separators and no leading ``./`` or ``/``.
    """
    text = text.replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    return text.lstrip("/")


def _base_command(rg: str, *, include_ignored: bool) -> list[str]:
    """Build the ripgrep argument prefix shared by grep and file listing.

    :param rg: Path to the ripgrep executable.
    :param include_ignored: When ``True``, add ``--no-ignore`` so
        ``.gitignore``-ignored files are searched too.
    :returns: Command prefix ending with hidden/skip-dir/ignore handling.
    """
    args = [rg, "--no-config", "--hidden", "--no-follow", "--no-messages"]
    args += _skip_dir_globs()
    if include_ignored:
        args.append("--no-ignore")
    return args


async def _run_rg(
    args: list[str],
    *,
    cwd: str,
    timeout_seconds: float,
    max_records: int,
) -> tuple[list[bytes], str, int | None, bool, str]:
    """Run ripgrep and collect up to *max_records* stdout lines.

    stdout is read incrementally and capped so a broad pattern cannot buffer
    unbounded output; once the cap is reached the process group is
    terminated.  stderr is drained concurrently so a full pipe cannot
    deadlock the child.

    :param args: Full argv (executable first).
    :param cwd: Directory to run in and search root.
    :param timeout_seconds: Wall-clock cap for the whole invocation.
    :param max_records: Maximum stdout lines to collect before stopping.
    :returns: ``(lines, stderr, returncode, truncated, error)`` where
        *error* is non-empty only for a start failure or timeout.
    """
    logger.debug(f"Running ripgrep\n{args = }\n{cwd = }")
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning(f"Could not start ripgrep: {exc}")
        return [], "", None, False, f"Could not start ripgrep: {exc}"

    assert process.stdout is not None  # PIPE was requested
    assert process.stderr is not None  # PIPE was requested
    stdout = process.stdout
    stderr_stream = process.stderr

    async def _drain_stderr() -> bytes:
        data = b""
        while True:
            chunk = await stderr_stream.read(_RG_STDERR_BYTES)
            if not chunk:
                break
            if len(data) < _RG_STDERR_BYTES:
                data += chunk[: _RG_STDERR_BYTES - len(data)]
        return data

    async def _collect_stdout() -> list[bytes]:
        lines: list[bytes] = []
        while True:
            raw = await stdout.readline()
            if not raw:
                break
            lines.append(raw)
            if len(lines) >= max_records + 1:
                break
        return lines

    stderr_task = asyncio.create_task(_drain_stderr())
    stdout_task = asyncio.create_task(_collect_stdout())
    try:
        await asyncio.wait_for(
            asyncio.gather(stdout_task, stderr_task), timeout=timeout_seconds
        )
    except TimeoutError:
        logger.warning(f"ripgrep timed out after {timeout_seconds:g} s")
        await _terminate_process_group(process)
        return (
            [],
            "",
            None,
            False,
            f"Search timed out after {timeout_seconds:g} s",
        )

    lines = stdout_task.result()
    stderr = stderr_task.result().decode("utf-8", errors="replace").strip()
    truncated = len(lines) > max_records
    if truncated:
        lines = lines[:max_records]
        await _terminate_process_group(process)
    returncode = await process.wait()
    return lines, stderr, returncode, truncated, ""


def _parse_match_line(raw: bytes) -> dict[str, Any] | None:
    """Parse one ``rg --json`` line into a match dict, or ``None``.

    :param raw: A single raw stdout line from ripgrep.
    :returns: ``{path, line_number, line}`` for a ``match`` record; ``None``
        for other record types, oversized, or malformed lines.
    """
    if len(raw) > _MAX_RECORD_BYTES:
        logger.warning(f"Skipping oversized ripgrep record ({len(raw)} bytes)")
        return None
    try:
        record = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        logger.warning("Skipping unparseable ripgrep record")
        return None
    if not isinstance(record, dict) or record.get("type") != "match":
        return None
    data = record.get("data")
    if not isinstance(data, dict):
        return None
    try:
        path = _normalize_path(data["path"]["text"])
        line = data["lines"]["text"]
        line_number = int(data["line_number"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Skipping malformed ripgrep match record")
        return None
    return {
        "path": path,
        "line_number": line_number,
        "line": line.rstrip("\r\n"),
    }


def _grep_result(
    pattern: str,
    path: str,
    *,
    matches: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    error: str = "",
) -> dict[str, Any]:
    """Build a grep result dict with the same shape as the in-house tool.

    :param pattern: The pattern searched for.
    :param path: The directory searched.
    :param matches: Matching lines.
    :param truncated: Whether results were cut short by a cap.
    :param error: Empty on success; a message otherwise.
    :returns: The result dict.
    """
    return {
        "pattern": pattern,
        "path": path,
        "matches": matches or [],
        "truncated": truncated,
        "files_scanned": None,
        "error": error,
    }


async def rg_grep(
    rg: str,
    pattern: str,
    path: str = ".",
    include: str | None = None,
    case_sensitive: bool = True,
    include_ignored: bool = False,
    max_results: int = 100,
    timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Search file contents with ripgrep, returning grep-shaped results.

    :param rg: Resolved ripgrep executable (see :func:`resolve_rg`).
    :param pattern: Regular expression to search for.
    :param path: Directory to search; must resolve inside *project_root*.
    :param include: Space separated glob patterns restricting files searched.
    :param case_sensitive: Whether matching is case sensitive.
    :param include_ignored: When ``True``, search ``.gitignore``-ignored files.
    :param max_results: Maximum number of matching lines to return.
    :param timeout_seconds: Wall-clock cap for the invocation.
    :param project_root: Boundary directory for the permission check.
    :returns: dict with pattern, path, matches, truncated, files_scanned,
        error.
    """
    logger.debug(
        f"ripgrep search\n"
        f"{pattern = }\n"
        f"{path = }\n"
        f"{include = }\n"
        f"{case_sensitive = }\n"
        f"{include_ignored = }\n"
        f"{max_results = }"
    )

    if not pattern:
        return _grep_result(pattern, path, error="Empty pattern is not allowed.")

    the_path = Path(path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return _grep_result(pattern, path, error=str(exc))
    if not the_path.is_dir():
        logger.warning(f"Not a directory: {path}")
        return _grep_result(pattern, path, error=f"Not a directory: {path}")

    args = _base_command(rg, include_ignored=include_ignored)
    args += ["--json", "--max-columns", str(_MAX_COLUMNS)]
    args.append("--case-sensitive" if case_sensitive else "-i")
    if include:
        for glob in include.split():
            args += ["--glob", glob]
    args += ["-e", pattern, "."]

    lines, stderr, returncode, truncated, run_error = await _run_rg(
        args,
        cwd=str(the_path),
        timeout_seconds=timeout_seconds,
        max_records=max_results,
    )
    if run_error:
        return _grep_result(pattern, path, error=run_error)
    if not truncated and returncode != 0:
        # rg exit codes: 0 = matches, 1 = no matches, 2 = error.
        if returncode == 1:
            return _grep_result(pattern, path)
        message = stderr or f"ripgrep exited with status {returncode}"
        logger.warning(f"ripgrep failed: {message}")
        return _grep_result(pattern, path, error=message)

    matches = [match for match in map(_parse_match_line, lines) if match]
    logger.debug(f"ripgrep search done\n{len(matches) = }\n{truncated = }")
    return _grep_result(pattern, path, matches=matches, truncated=truncated)


async def rg_files(
    rg: str,
    pattern: str = "*",
    path: str = ".",
    include_ignored: bool = False,
    max_results: int = 100,
    timeout_seconds: float = DEFAULT_SEARCH_TIMEOUT_SECONDS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """List files matching a glob with ripgrep (``rg --files``).

    :param rg: Resolved ripgrep executable (see :func:`resolve_rg`).
    :param pattern: Glob pattern matched against file paths; ``"*"`` lists all.
    :param path: Directory to search; must resolve inside *project_root*.
    :param include_ignored: When ``True``, list ``.gitignore``-ignored files.
    :param max_results: Maximum number of file paths to return.
    :param timeout_seconds: Wall-clock cap for the invocation.
    :param project_root: Boundary directory for the permission check.
    :returns: dict with files, truncated, error.
    """
    logger.debug(
        f"ripgrep file listing\n"
        f"{pattern = }\n"
        f"{path = }\n"
        f"{include_ignored = }\n"
        f"{max_results = }"
    )

    the_path = Path(path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return {"files": [], "truncated": False, "error": str(exc)}
    if not the_path.is_dir():
        logger.warning(f"Not a directory: {path}")
        return {"files": [], "truncated": False, "error": f"Not a directory: {path}"}

    args = _base_command(rg, include_ignored=include_ignored)
    args.append("--files")
    if pattern and pattern != "*":
        args += ["--glob", pattern]
    args.append(".")

    lines, stderr, returncode, truncated, run_error = await _run_rg(
        args,
        cwd=str(the_path),
        timeout_seconds=timeout_seconds,
        max_records=max_results,
    )
    if run_error:
        return {"files": [], "truncated": False, "error": run_error}
    if not truncated and returncode != 0:
        if returncode == 1:
            return {"files": [], "truncated": False, "error": ""}
        message = stderr or f"ripgrep exited with status {returncode}"
        logger.warning(f"ripgrep failed: {message}")
        return {"files": [], "truncated": False, "error": message}

    files = [
        _normalize_path(line.decode("utf-8", errors="replace").strip())
        for line in lines
        if line.strip()
    ]
    logger.debug(f"ripgrep listing done\n{len(files) = }\n{truncated = }")
    return {"files": files, "truncated": truncated, "error": ""}
