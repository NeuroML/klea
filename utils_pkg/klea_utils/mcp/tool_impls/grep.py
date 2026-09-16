#!/usr/bin/env python3
"""
Regular-expression content search for Klea MCP tools.

File: klea_utils/mcp/tool_impls/grep.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import fnmatch
import logging
import re
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access
from klea_utils.mcp.tool_impls.rg_backend import resolve_rg, rg_grep
from klea_utils.mcp.tool_impls.walk import iter_files

logger = logging.getLogger(__name__)

#: Default cap on the number of matching lines returned.
DEFAULT_MAX_RESULTS = 100

#: Default cap on the number of files scanned (bounds worst-case runtime).
DEFAULT_MAX_FILES = 5000

#: Default cap on the size of an individual file considered for searching.
DEFAULT_MAX_FILE_BYTES = 5_000_000

#: Default cap on the total characters of matching lines returned.
DEFAULT_MAX_CHARS = 100_000


def _result(
    pattern: str,
    path: str,
    *,
    matches: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    files_scanned: int = 0,
    error: str = "",
) -> dict[str, Any]:
    """Build the standard grep result dict.

    :param pattern: The pattern that was searched for.
    :param path: The directory that was searched.
    :param matches: Matching lines, each a ``{path, line_number, line}`` dict.
    :param truncated: Whether scanning stopped before covering everything.
    :param files_scanned: Number of files that were searched.
    :param error: Empty on success; a message otherwise.
    :returns: The result dict returned to the MCP wrapper.
    """
    return {
        "pattern": pattern,
        "path": path,
        "matches": matches or [],
        "truncated": truncated,
        "files_scanned": files_scanned,
        "error": error,
    }


def grep_inhouse(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    case_sensitive: bool = True,
    max_results: int = DEFAULT_MAX_RESULTS,
    max_files: int = DEFAULT_MAX_FILES,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_chars: int = DEFAULT_MAX_CHARS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Search files using the in-house walker (no external tools).

    This is the fallback used when the pinned ripgrep binary is not
    installed.  It has no ``.gitignore`` support: only the fixed
    ``SKIP_DIRECTORIES`` set is excluded, so gitignored files are searched.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap :func:`grep` in an MCP tool (see
    ``klea_utils.mcp.server.bundled_tools``).

    The walk skips version control internals, virtual environments, and
    tool caches, and never follows symlinks (see
    ``klea_utils.mcp.tool_impls.walk``).  Binary files (those containing a
    NUL byte) and files larger than *max_file_bytes* are skipped.

    :param pattern: Regular expression to search for.
    :param path: Directory to search, relative to *project_root*.
    :param include: Optional space separated glob patterns; when given, only
        files whose path (relative to *path*) matches one are searched.
    :param case_sensitive: Whether matching is case sensitive.
    :param max_results: Maximum number of matching lines to return.
    :param max_files: Maximum number of files to scan.
    :param max_file_bytes: Maximum size of a file considered for searching.
    :param max_chars: Maximum total characters of matching lines to return.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with pattern, path, matches, truncated, files_scanned,
        error.
    """
    logger.debug(
        f"Searching files\n"
        f"{pattern = }\n"
        f"{path = }\n"
        f"{include = }\n"
        f"{case_sensitive = }\n"
        f"{max_results = }\n"
        f"{max_files = }\n"
        f"{max_file_bytes = }\n"
        f"{max_chars = }\n"
        f"{project_root = }"
    )

    if not pattern:
        logger.warning("Empty search pattern rejected")
        return _result(pattern, path, error="Empty pattern is not allowed.")

    if not case_sensitive:
        flags = re.IGNORECASE
    else:
        flags = re.NOFLAG
    try:
        regex = re.compile(pattern, flags=flags)
    except re.error as exc:
        logger.warning(f"Invalid regular expression {pattern!r}: {exc}")
        return _result(pattern, path, error=f"Invalid regular expression: {exc}")

    the_path = Path(path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return _result(pattern, path, error=str(exc))

    if not the_path.is_dir():
        logger.warning(f"Not a directory: {path}")
        return _result(pattern, path, error=f"Not a directory: {path}")

    include_patterns = include.split() if include else []

    matches: list[dict[str, Any]] = []
    truncated = False
    files_scanned = 0
    chars = 0

    try:
        for candidate in iter_files(the_path, project_root=project_root):
            if files_scanned >= max_files:
                truncated = True
                break
            files_scanned += 1

            rel = candidate.relative_to(the_path).as_posix()
            if include_patterns and not any(
                fnmatch.fnmatch(rel, p) for p in include_patterns
            ):
                continue

            try:
                size = candidate.stat().st_size
            except OSError as exc:
                logger.warning(f"Could not stat {candidate}: {exc}")
                continue
            if size > max_file_bytes:
                logger.debug(f"Skipping large file {candidate} ({size} bytes)")
                continue

            try:
                data = candidate.read_bytes()
            except OSError as exc:
                logger.warning(f"Could not read {candidate}: {exc}")
                continue
            if b"\x00" in data:
                logger.debug(f"Skipping binary file {candidate}")
                continue

            text = data.decode("utf-8", errors="replace")
            for line_number, line in enumerate(text.splitlines(), start=1):
                if not regex.search(line):
                    continue
                display = line
                remaining = max_chars - chars
                if len(display) > remaining:
                    display = display[: max(remaining, 0)]
                    truncated = True
                matches.append(
                    {
                        "path": rel,
                        "line_number": line_number,
                        "line": display,
                    }
                )
                chars += len(display)
                if len(matches) >= max_results or chars >= max_chars:
                    truncated = True
                    break
            if len(matches) >= max_results or chars >= max_chars:
                break
    except PermissionDeniedError as exc:  # pragma: no cover - checked above
        return _result(pattern, path, error=str(exc))
    except OSError as exc:
        logger.warning(f"Could not search {path}: {exc}")
        return _result(pattern, path, error=f"Could not search {path}: {exc}")

    logger.debug(
        f"Searched files\n{len(matches) = }\n{files_scanned = }\n{truncated = }"
    )
    return _result(
        pattern,
        path,
        matches=matches,
        truncated=truncated,
        files_scanned=files_scanned,
    )


async def grep(
    pattern: str,
    path: str = ".",
    include: str | None = None,
    case_sensitive: bool = True,
    include_ignored: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Search files under a directory for lines matching a regular expression.

    Uses the pinned ripgrep binary when it is installed (the ``[search]``
    extra), otherwise falls back to :func:`grep_inhouse`.  With ripgrep the
    default honours ``.gitignore``; pass *include_ignored* to search ignored
    files too.  The in-house fallback has no ``.gitignore`` support, so
    *include_ignored* has no effect there (ignored files are searched
    regardless) and only the fixed skip-directory set is excluded.

    :param pattern: Regular expression to search for.
    :param path: Directory to search, relative to *project_root*.
    :param include: Optional space separated glob patterns; when given, only
        files whose path (relative to *path*) matches one are searched.
    :param case_sensitive: Whether matching is case sensitive.
    :param include_ignored: When ``True``, also search ``.gitignore``-ignored
        files (ripgrep backend only).
    :param max_results: Maximum number of matching lines to return.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with pattern, path, matches, truncated, files_scanned,
        error.
    """
    rg = resolve_rg()
    if rg is not None:
        logger.debug(f"Searching with ripgrep\n{rg = }")
        return await rg_grep(
            rg,
            pattern,
            path=path,
            include=include,
            case_sensitive=case_sensitive,
            include_ignored=include_ignored,
            max_results=max_results,
            project_root=project_root,
        )
    logger.debug("ripgrep unavailable; searching with the in-house walker")
    return grep_inhouse(
        pattern,
        path=path,
        include=include,
        case_sensitive=case_sensitive,
        max_results=max_results,
        project_root=project_root,
    )
