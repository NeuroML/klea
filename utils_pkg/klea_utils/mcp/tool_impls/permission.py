#!/usr/bin/env python3
"""
Path permission checking for file-accessing MCP tools.

File: klea_utils/mcp/tool_impls/permission.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError

logger = logging.getLogger(__name__)


# TODO (deferred): replace the hard boundary check with a full permission
# service: config rulesets (allow/deny/ask per path pattern) plus an
# LLM-free interactive approval loop (graph pause + TUI/web input),
# opencode-style.  Tracked as a separate task; for now access is denied to
# any path outside *project_root* and no user approval exists yet.
def check_path_access(
    path: str | os.PathLike, project_root: str | os.PathLike | None = None
) -> None:
    """Raise :class:`PermissionDeniedError` when *path* is not permitted.

    The permission layer is currently a stub: *path* is allowed only when it
    resolves inside *project_root* (default: the current working directory).
    Both sides are fully resolved first, so ``..`` traversal and symlink
    escapes outside the boundary are caught.  Anything else is denied with
    no way to grant access yet.

    :param path: File or directory path the tool wants to access.
    :param project_root: Boundary directory inside which access is allowed.
        Defaults to the current working directory.
    :raises PermissionDeniedError: when *path* resolves outside the boundary.
    """
    if not str(path).strip():
        logger.warning("Permission denied: empty path")
        raise PermissionDeniedError("Empty path is not allowed")
    the_path = Path(path).expanduser().resolve()
    root = (
        Path(project_root).expanduser().resolve()
        if project_root
        else Path.cwd().resolve()
    )

    try:
        the_path.relative_to(root)
    except ValueError:
        logger.warning(f"Permission denied: {the_path} is outside {root}")
        raise PermissionDeniedError(
            f"Access to path outside the project directory is denied: {the_path}"
        ) from None

    logger.debug(f"Permission granted: {the_path} is inside {root}")


def check_tool_arguments_permissions(
    tool_meta: dict[str, Any] | None,
    arguments: dict[str, Any],
    project_root: str | os.PathLike | None = None,
) -> list[str]:
    """Check the path arguments a tool call would pass against the boundary.

    Reads the ``checkpaths`` key from *tool_meta* (the ``meta`` dict of an
    MCP tool, populated by ``register_tools`` from ``ToolInfo.checkpaths``).
    For each declared argument name that is present in *arguments*, the value
    is checked with :func:`check_path_access`.  Unlike
    :func:`check_path_access`, this never raises: denied paths are collected
    and returned as human-readable messages so the caller (the tool caller
    node) can turn them into a non-halting error result without invoking the
    tool.

    Values that are not strings or path-like (e.g. an int) are skipped with a
    warning, so a mistyped declaration cannot crash the gate.

    :param tool_meta: Tool ``meta`` dict (``Tool.meta`` from ``mcp_tools``),
        or ``None``/empty when the tool declares nothing.
    :param arguments: The arguments dict the caller intends to pass to the tool.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: List of denial messages; empty when all declared paths are
        permitted (or no ``checkpaths`` are declared).
    """
    if not tool_meta:
        return []
    checkpaths = tool_meta.get("checkpaths")
    if not checkpaths:
        return []

    logger.debug(f"Checking declared path arguments\n{checkpaths = }\n{arguments = }")
    denials: list[str] = []
    for arg_name in checkpaths:
        if arg_name not in arguments:
            continue
        value = arguments[arg_name]
        if not isinstance(value, (str, os.PathLike)):
            logger.warning(
                f"Skipping non-path value for declared path arg\n"
                f"{arg_name = }\n"
                f"{value = }"
            )
            continue
        try:
            check_path_access(value, project_root)
        except PermissionDeniedError as exc:
            logger.warning(
                f"Permission denied for tool arg {arg_name}\n{value = }\n{exc = }"
            )
            denials.append(str(exc))
    return denials
