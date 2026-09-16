#!/usr/bin/env python3
"""
Exact search/replace edit implementation for the Klea MCP tools (ADR-0039).

File: klea_utils/mcp/tool_impls/edit_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import FileEditError, PermissionDeniedError
from klea_utils.mcp.tool_impls import file_ops
from klea_utils.mcp.tool_impls.permission import check_path_access

logger = logging.getLogger(__name__)


def _result(
    path: str,
    *,
    replacements: int = 0,
    additions: int = 0,
    deletions: int = 0,
    matcher: str = "",
    diff: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Build the standard edit result dict.

    :param path: The path edited (or attempted).
    :param replacements: Number of occurrences replaced.
    :param additions: Lines added.
    :param deletions: Lines removed.
    :param matcher: Name of the replacer that matched (empty on failure).
    :param diff: Unified diff of the change (possibly truncated).
    :param error: Empty on success; a message otherwise.
    :returns: The result dict returned to the MCP wrapper.
    """
    return {
        "path": path,
        "replacements": replacements,
        "additions": additions,
        "deletions": deletions,
        "matcher": matcher,
        "diff": diff,
        "error": error,
    }


def _apply_edit(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> tuple[str, int, str, str]:
    """Apply a single exact replacement to *content*.

    Works on LF-normalised text.  Returns the updated text, the number of
    replacements, the matcher name, and an error message (empty on success).

    :param content: File content (LF-normalised).
    :param old_string: Span to replace (LF-normalised).
    :param new_string: Replacement span (LF-normalised).
    :param replace_all: Replace every occurrence instead of requiring one.
    :returns: ``(updated, replacements, matcher, error)``.
    """
    if old_string == "":
        return (
            content,
            0,
            "",
            (
                "old_string must not be empty; use write_file to create or "
                "overwrite a file."
            ),
        )
    if old_string == new_string:
        return content, 0, "", "old_string and new_string are identical."

    count = content.count(old_string)
    if count == 0:
        return (
            content,
            0,
            "",
            (
                "Could not find old_string in the file. It must match exactly, "
                "including whitespace and indentation."
            ),
        )
    if count > 1 and not replace_all:
        return (
            content,
            0,
            "",
            (
                f"Found {count} matches for old_string. Provide more surrounding "
                "context to make it unique, or set replace_all."
            ),
        )

    if replace_all:
        return content.replace(old_string, new_string), count, "exact", ""
    return content.replace(old_string, new_string, 1), 1, "exact", ""


def edit_file(
    path: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Replace an exact span of an existing file.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool (see ``klea_utils.mcp.server.bundled_tools``).

    Matching normalises line endings; the file's own line ending, BOM and
    mode are preserved on write, which is atomic.  Unless *replace_all* is
    set, *old_string* must occur exactly once.

    :param path: File path to edit; must resolve inside *project_root*.
    :param old_string: Exact text to replace (must not be empty).
    :param new_string: Replacement text (must differ from *old_string*).
    :param replace_all: Replace every occurrence rather than requiring one.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with path, replacements, additions, deletions, matcher,
        diff, error.
    """
    logger.debug(
        f"Editing file\n"
        f"{path = }\n"
        f"{len(old_string) = }\n"
        f"{len(new_string) = }\n"
        f"{replace_all = }\n"
        f"{project_root = }"
    )

    the_path = Path(path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return _result(path, error=str(exc))

    try:
        doc = file_ops.read_whole_text(the_path)
    except FileEditError as exc:
        logger.warning(f"Cannot edit {path}: {exc}")
        return _result(str(the_path), error=str(exc))

    content = file_ops.normalize_newlines(doc.text)
    old_norm = file_ops.normalize_newlines(old_string)
    new_norm = file_ops.normalize_newlines(new_string)

    updated, replacements, matcher, error = _apply_edit(
        content, old_norm, new_norm, replace_all
    )
    if error:
        logger.debug(f"Edit rejected: {error}")
        return _result(str(the_path), error=error)

    diff, additions, deletions = file_ops.diff_payload(content, updated, str(the_path))

    try:
        file_ops.write_whole_text(
            the_path,
            updated,
            bom=doc.bom,
            newline=doc.newline,
            mode=doc.mode,
        )
    except FileEditError as exc:
        logger.warning(f"Could not write {path}: {exc}")
        return _result(str(the_path), error=str(exc))

    logger.debug(f"Edited {the_path}\n{replacements = }\n{matcher = }")
    return _result(
        str(the_path),
        replacements=replacements,
        additions=additions,
        deletions=deletions,
        matcher=matcher,
        diff=diff,
    )
