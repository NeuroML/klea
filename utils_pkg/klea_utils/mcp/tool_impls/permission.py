#!/usr/bin/env python3
"""
Path boundary helpers for the client-side permission gate.

The tool implementations no longer call these; the client-side gate uses
:func:`resolve_path` / :func:`path_is_allowed` / :func:`permitted_roots`
(:mod:`klea_utils.mcp.path_detect`) and :func:`check_path_access` remains a
convenience that raises on denial.  See ``devdocs/system/mcp-permissions.md``
and ``devdocs/adr/0007-mcp-permissions.md``.

Containment is path-based and taken as a snapshot.  Two known limitations:
a *hard link* inside the root to an inode whose other names are outside it is
not detectable path-wise and is allowed; and a path swapped for an external
symlink after the check (TOCTOU) could still be opened.  Hardening would need
a per-open check (``openat``/``O_NOFOLLOW``).

File: klea_utils/mcp/tool_impls/permission.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from klea_utils.mcp.errors import PermissionDeniedError

logger = logging.getLogger(__name__)


def _is_relative_to(path: Path, root: Path) -> bool:
    """Return whether resolved *path* is inside resolved *root*."""
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def permitted_roots(
    project_root: str | os.PathLike | None = None,
    allowed_dirs: Sequence[str] | None = None,
) -> list[Path]:
    """Return the resolved roots a path is allowed to be inside.

    All are fully resolved, so ``..`` traversal and symlink escapes are
    caught against every root.  Blank or unresolvable ``allowed_dirs`` entries
    are skipped (fail open for the root, not the path).

    :param project_root: Primary boundary; defaults to the current directory.
    :param allowed_dirs: Additional roots, for example directories a user
        approved for the session (ADR-0007 update 2026-10-08).
    :returns: A list of resolved boundary roots.
    """
    roots = [
        Path(project_root).expanduser().resolve()
        if project_root
        else Path.cwd().resolve()
    ]
    for entry in allowed_dirs or []:
        if not str(entry).strip():
            continue
        try:
            roots.append(Path(entry).expanduser().resolve())
        except (OSError, RuntimeError) as exc:
            logger.warning(f"Skipping unresolvable allowed directory {entry!r}: {exc}")
    return roots


def resolve_path(path: str | os.PathLike) -> Path | None:
    """Resolve *path* to an absolute, symlink-free path, or ``None``.

    Returns ``None`` for an empty path or one that cannot be resolved (for
    example a symlink loop), so callers can fail closed.  A missing target is
    resolved non-strictly so the boundary check still applies to its location.

    :param path: Path to resolve.
    :returns: The resolved path, or ``None``.
    """
    if not str(path).strip():
        return None
    base_path = Path(path).expanduser()
    try:
        # ``strict=True`` makes a symlink loop (ELOOP) or an unreadable parent
        # raise here instead of being silently returned as-is by the non-strict
        # resolve.
        return base_path.resolve(strict=True)
    except FileNotFoundError:
        # A missing target is allowed through so the tool can report nearby
        # entries; the boundary check still applies to its location.
        try:
            return base_path.resolve(strict=False)
        except (OSError, RuntimeError):
            return None
    except (OSError, RuntimeError):
        return None


def path_is_allowed(path: Path, roots: list[Path]) -> bool:
    """Return whether resolved *path* is inside any of *roots*.

    :param path: An already-resolved path.
    :param roots: Resolved boundary roots (see :func:`permitted_roots`).
    :returns: True when *path* is inside at least one root.
    """
    return any(_is_relative_to(path, root) for root in roots)


def check_path_access(
    path: str | os.PathLike,
    project_root: str | os.PathLike | None = None,
    *,
    allowed_dirs: Sequence[str] | None = None,
) -> None:
    """Raise :class:`PermissionDeniedError` when *path* is not permitted.

    *path* is allowed when it resolves inside *project_root* (default: the
    current working directory) or inside any of *allowed_dirs*.  Both sides
    are fully resolved first, so ``..`` traversal and symlink escapes outside
    the boundary are caught.

    This is the utility used by the client-side tool-call gate
    (:mod:`klea_utils.mcp.path_detect`); it is no longer called inside tool
    implementations (ADR-0007 update 2026-10-08).

    :param path: File or directory path the tool wants to access.
    :param project_root: Boundary directory inside which access is allowed.
        Defaults to the current working directory.
    :param allowed_dirs: Additional directories permitted for this check
        (session approvals), or ``None``.
    :raises PermissionDeniedError: when *path* resolves outside every root, or
        cannot be resolved at all (for example a symlink loop).
    """
    if not str(path).strip():
        logger.warning("Permission denied: empty path")
        raise PermissionDeniedError("Empty path is not allowed")
    the_path = resolve_path(path)
    if the_path is None:
        logger.warning(f"Permission denied: cannot resolve {path!r}")
        raise PermissionDeniedError(f"Cannot resolve path: {path}")
    roots = permitted_roots(project_root, allowed_dirs)
    if not path_is_allowed(the_path, roots):
        logger.warning(f"Permission denied: {the_path} is outside {roots}")
        raise PermissionDeniedError(
            f"Access to path outside the project directory is denied: {the_path}"
        )

    logger.debug(f"Permission granted: {the_path} is inside {roots}")
