#!/usr/bin/env python3
"""
File listing implementation for Klea MCP tools.

File: klea_utils/mcp/tool_impls/list_files.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import fnmatch
import logging
import os
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access

logger = logging.getLogger(__name__)


def list_files(
    path: str,
    max_depth: int | None = None,
    pattern: str = "*",
    include_files: bool = True,
    include_directories: bool = True,
    recursive: bool = False,
    max_results: int = 100,
    project_root: str | None = None,
) -> dict[str, Any]:
    """List files and directories with filtering and metadata.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool (see klea_utils.mcp.registry).

    :param path: Directory path to list.  Must be relative to current working
        directory and cannot contain '..' for security.
    :param max_depth: Maximum directory depth to traverse.  1 lists the
        immediate entries inside *path*, 2 also descends one directory
        deeper, and so on.  ``None`` for unlimited.
    :param pattern: Space separated file patterns to filter based on file type.
    :param include_files: Whether to include files in results.
    :param include_directories: Whether to include directories in results.
    :param recursive: If True, traverse subdirectories recursively.
    :param max_results: Maximum number of entries to return.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.

    :returns: dict with files, error, truncated, note.  ``note`` is non-empty
        when the requested filter matched nothing and the fallback below
        returned an unfiltered listing instead.
    """
    logger.debug(
        f"Listing files\n"
        f"{path = }\n"
        f"{max_depth = }\n"
        f"{pattern = }\n"
        f"{include_files = }\n"
        f"{include_directories = }\n"
        f"{recursive = }\n"
        f"{max_results = }"
    )

    the_path = Path(path)
    truncated = False
    error = ""
    note = ""
    files: list[dict[str, Any]] = []
    paths: list[Path] = []

    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return {
            "files": [],
            "truncated": False,
            "error": str(exc),
            "note": "",
        }

    patterns = list(set(pattern.split()))

    def _matches(entry: Path, patterns_to_match: list[str]) -> bool:
        """Return whether *entry*'s name matches any of *patterns_to_match*.

        :param entry: Directory entry to test.
        :param patterns_to_match: Glob patterns to match against the name.
        :returns: ``True`` on the first matching pattern.
        """
        return any(fnmatch.fnmatch(entry.name, p) for p in patterns_to_match)

    def _include(entry: Path, files_flag: bool, directories_flag: bool) -> bool:
        """Return whether *entry* passes the include flags.

        Symlinks are always included: they carry their own ``link`` type so
        the caller can decide how to treat them; the ``include_*`` flags only
        apply to real files and directories.

        :param entry: Directory entry to test.
        :param files_flag: Whether real files are included.
        :param directories_flag: Whether real directories are included.
        :returns: ``True`` when the entry should be listed.
        """
        if entry.is_symlink():
            return True
        if entry.is_dir():
            return directories_flag
        return files_flag

    def _collect(
        patterns_to_match: list[str], files_flag: bool, directories_flag: bool
    ) -> list[Path]:
        """Return the entries under ``the_path`` passing the given filters.

        Used for the requested listing and, unchanged in structure, for the
        unfiltered fallback below (called with ``["*"], True, True``).

        :param patterns_to_match: Glob patterns a name must match.
        :param files_flag: Whether real files are included.
        :param directories_flag: Whether real directories are included.
        :returns: Matching paths, subject to ``recursive``/``max_depth``.
        """
        collected: list[Path] = []
        if not recursive:
            with os.scandir(the_path) as it:
                for entry in it:
                    p = Path(entry.path)
                    if _matches(p, patterns_to_match) and _include(
                        p, files_flag, directories_flag
                    ):
                        collected.append(p)
        else:
            depth_limit = max_depth if max_depth is not None else float("inf")
            stack: list[tuple[Path, int]] = [(the_path, 1)]
            while stack:
                d, depth = stack.pop()
                if depth > depth_limit:
                    continue
                with os.scandir(d) as it:
                    entries = list(it)
                for entry in entries:
                    p = Path(entry.path)
                    if _matches(p, patterns_to_match) and _include(
                        p, files_flag, directories_flag
                    ):
                        collected.append(p)
                    if (
                        p.is_dir()
                        and not p.is_symlink()
                        and (max_depth is None or depth < max_depth)
                    ):
                        stack.append((p, depth + 1))
        return collected

    try:
        paths = _collect(patterns, include_files, include_directories)

        # A filtered listing that matches nothing can mislead the loop into
        # concluding the directory is empty (observed: ``list_files`` with
        # ``pattern="*.txt"`` on a directory of .md/.toml/.py files returned
        # ``files: []`` and the agent aborted).  When entries exist that the
        # filter hid, return the unfiltered listing instead and say so in
        # ``note``, so the caller can see what is actually there.
        filtered = "*" not in patterns or not include_files or not include_directories
        if not paths and filtered:
            unfiltered = _collect(["*"], True, True)
            if unfiltered:
                paths = unfiltered
                note = (
                    f"No entries matched the requested filter "
                    f"(pattern={pattern!r}, include_files={include_files}, "
                    f"include_directories={include_directories}); "
                    f"showing the unfiltered directory listing instead."
                )

        if len(paths) > max_results:
            truncated = True

        for f in paths[:max_results]:
            ftype = "file"
            if f.is_dir():
                ftype = "directory"
            if f.is_symlink():
                ftype = "link"
            files.append(
                {
                    "path": str(f),
                    "type": ftype,
                    "modified time": f.stat().st_mtime,
                    "size": f.stat().st_size,
                }
            )
    except OSError as e:
        logger.warning(f"Error listing {path}: {e}")
        error = e.__str__()

    logger.debug(
        f"Listed files\n{path = }\n{len(files) = }\n{truncated = }\n"
        f"{note = }\n{error = }"
    )

    result = {
        "files": files,
        "error": error,
        "truncated": truncated,
        "note": note,
    }

    return result


def nearby_entries(
    path: str | Path,
    project_root: str | None = None,
    max_results: int = 20,
) -> tuple[str, list[str]]:
    """Return the nearest existing directory and its entries for *path*.

    Diagnostic helper for the file tools: when a requested target is missing,
    the tool includes this so the caller can see what actually exists (the
    same "show what is there" idea as the ``list_files`` fallback, reused).
    Walks up from the target's parent to the nearest existing ancestor and
    lists its immediate entries.

    :param path: The requested (possibly missing) path.
    :param project_root: Boundary directory for the permission check, passed
        through to :func:`list_files`.
    :param max_results: Maximum number of entries to return.
    :returns: ``(directory, names)``, or ``("", [])`` when no existing ancestor
        directory could be found or listed.
    """
    target = Path(path)
    directory = target.parent
    while not directory.is_dir() and directory != directory.parent:
        directory = directory.parent
    if not directory.is_dir():
        return "", []
    result = list_files(
        path=str(directory), project_root=project_root, max_results=max_results
    )
    names = sorted({Path(entry["path"]).name for entry in result.get("files", [])})
    return str(directory), names


def missing_target_note(path: str, directory: str, nearby: list[str]) -> str:
    """Return a "target missing; nearby entries" note for a file-tool result.

    :param path: The requested (missing) path.
    :param directory: The nearest existing directory, or ``""``.
    :param nearby: Entry names found in *directory*.
    :returns: A one-line, human-readable note.
    """
    if not directory:
        return f"No file at {path!r}."
    entries = ", ".join(nearby) if nearby else "(empty)"
    return f"No file at {path!r}; entries in {directory!r}: {entries}"
