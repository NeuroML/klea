#!/usr/bin/env python3
"""
Shared filesystem walker for read-only search tools.

File: klea_utils/mcp/tool_impls/walk.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import os
from collections.abc import Collection, Iterator
from pathlib import Path

from klea_utils.mcp.tool_impls.permission import check_path_access

logger = logging.getLogger(__name__)

#: Directory names never descended into by default.  These are version
#: control internals, virtual environments, and tool caches: searching them
#: is almost never what the caller wants, and some (``.git``, caches) can be
#: large enough to dominate a scan.  Matched against the directory basename
#: at every depth; the search root itself is always traversed, even when it
#: matches, since the caller explicitly asked for it.
SKIP_DIRECTORIES: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        ".klea-cache",
        "__pycache__",
        "node_modules",
    }
)


def iter_files(
    root: str | os.PathLike,
    *,
    skip_dirs: Collection[str] = SKIP_DIRECTORIES,
    project_root: str | os.PathLike | None = None,
) -> Iterator[Path]:
    """Yield the files under *root*, skipping caches and symlinks.

    Framework-agnostic helper shared by the read-only search tools
    (``grep``, ``find_files``).  This is a depth-first walk using
    ``os.scandir`` with symlinks disabled at every step:

    * directories named in *skip_dirs* are not descended into;
    * directory symlinks are not followed (so a link pointing outside
      *root* cannot pull external files into the results);
    * file symlinks are skipped, for the same boundary reason.

    The *root* itself is always traversed, even when its name is in
    *skip_dirs*: the caller explicitly asked for it.  Unreadable
    subdirectories are logged and skipped rather than aborting the walk.

    This function performs no result capping; callers bound the number of
    files scanned and matches returned.

    :param root: Directory to walk.
    :param skip_dirs: Directory basenames to skip at any depth.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :raises PermissionDeniedError: when *root* resolves outside the boundary.
    :raises OSError: when *root* cannot be scanned (e.g. it is a file or
        does not exist).
    :yields: :class:`pathlib.Path` for each regular file found.
    """
    check_path_access(root, project_root)
    base = Path(root)
    logger.debug(f"Walking files\n{base = }\n{project_root = }")

    # Scandir the root eagerly so an unusable root (missing, not a
    # directory, unreadable) raises before any results are yielded.
    stack: list[Path] = [base]
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError as exc:
            if current == base:
                raise
            logger.warning(f"Could not scan directory {current}: {exc}")
            continue

        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name in skip_dirs:
                        logger.debug(f"Skipping directory {entry.name}")
                        continue
                    stack.append(Path(entry.path))
                    continue
                if entry.is_symlink():
                    logger.debug(f"Skipping symlink {entry.path}")
                    continue
                if entry.is_file(follow_symlinks=False):
                    yield Path(entry.path)
            except OSError as exc:
                logger.warning(f"Could not stat entry {entry.path}: {exc}")
