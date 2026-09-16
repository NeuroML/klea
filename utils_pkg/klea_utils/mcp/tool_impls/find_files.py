#!/usr/bin/env python3
"""
File-name search for Klea MCP tools.

File: klea_utils/mcp/tool_impls/find_files.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import fnmatch
import logging
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access
from klea_utils.mcp.tool_impls.rg_backend import resolve_rg, rg_files
from klea_utils.mcp.tool_impls.walk import iter_files

logger = logging.getLogger(__name__)

#: Default cap on the number of file paths returned.
DEFAULT_MAX_RESULTS = 100


def _result(
    *,
    files: list[str] | None = None,
    truncated: bool = False,
    error: str = "",
) -> dict[str, Any]:
    """Build the standard find_files result dict.

    :param files: Matching paths, relative to the search root.
    :param truncated: Whether the result was cut short by the cap.
    :param error: Empty on success; a message otherwise.
    :returns: The result dict returned to the MCP wrapper.
    """
    return {"files": files or [], "truncated": truncated, "error": error}


def find_files_inhouse(
    pattern: str = "*",
    path: str = ".",
    max_results: int = DEFAULT_MAX_RESULTS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """List files under a directory whose path matches a glob pattern.

    This is the fallback used when the pinned ripgrep binary is not
    installed.  It has no ``.gitignore`` support: only the fixed
    ``SKIP_DIRECTORIES`` set is excluded, so gitignored files are listed.

    The *pattern* is matched against each file's path relative to *path*
    (``fnmatch``, where ``*`` also crosses directory separators, so
    ``*.py`` matches at any depth).  Results are in traversal order and
    not sorted.

    :param pattern: Glob pattern to match against file paths; ``"*"`` lists
        every file.
    :param path: Directory to search, relative to *project_root*.
    :param max_results: Maximum number of file paths to return.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with files, truncated, error.
    """
    logger.debug(
        f"Finding files\n{pattern = }\n{path = }\n{max_results = }\n{project_root = }"
    )

    the_path = Path(path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return _result(error=str(exc))

    if not the_path.is_dir():
        logger.warning(f"Not a directory: {path}")
        return _result(error=f"Not a directory: {path}")

    match_all = not pattern or pattern == "*"
    files: list[str] = []
    try:
        for candidate in iter_files(the_path, project_root=project_root):
            rel = candidate.relative_to(the_path).as_posix()
            if not match_all and not fnmatch.fnmatch(rel, pattern):
                continue
            files.append(rel)
            if len(files) > max_results:
                break
    except PermissionDeniedError as exc:  # pragma: no cover - checked above
        return _result(error=str(exc))
    except OSError as exc:
        logger.warning(f"Could not search {path}: {exc}")
        return _result(error=f"Could not search {path}: {exc}")

    truncated = len(files) > max_results
    files = files[:max_results]
    logger.debug(f"Found files\n{len(files) = }\n{truncated = }")
    return _result(files=files, truncated=truncated)


async def find_files(
    pattern: str = "*",
    path: str = ".",
    include_ignored: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """List files under a directory whose path matches a glob pattern.

    Uses the pinned ripgrep binary when it is installed (the ``[search]``
    extra), otherwise falls back to :func:`find_files_inhouse`.  With
    ripgrep the default honours ``.gitignore``; pass *include_ignored* to
    list ignored files too.  The in-house fallback has no ``.gitignore``
    support, so *include_ignored* has no effect there.

    :param pattern: Glob pattern matched against file paths; ``"*"`` lists
        every file.
    :param path: Directory to search, relative to *project_root*.
    :param include_ignored: When ``True``, also list ``.gitignore``-ignored
        files (ripgrep backend only).
    :param max_results: Maximum number of file paths to return.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with files, truncated, error.
    """
    rg = resolve_rg()
    if rg is not None:
        logger.debug(f"Finding files with ripgrep\n{rg = }")
        return await rg_files(
            rg,
            pattern=pattern,
            path=path,
            include_ignored=include_ignored,
            max_results=max_results,
            project_root=project_root,
        )
    logger.debug("ripgrep unavailable; finding files with the in-house walker")
    return find_files_inhouse(
        pattern=pattern,
        path=path,
        max_results=max_results,
        project_root=project_root,
    )
