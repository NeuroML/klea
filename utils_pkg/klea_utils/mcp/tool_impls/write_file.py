#!/usr/bin/env python3
"""
Whole-file write implementation for the Klea MCP tools.

File: klea_utils/mcp/tool_impls/write_file.py

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
    created: bool = False,
    bytes_written: int = 0,
    additions: int = 0,
    deletions: int = 0,
    diff: str = "",
    error: str = "",
) -> dict[str, Any]:
    """Build the standard write result dict.

    :param path: The path written (or attempted).
    :param created: Whether the file did not exist before.
    :param bytes_written: Final file size in bytes.
    :param additions: Lines added relative to the previous content.
    :param deletions: Lines removed relative to the previous content.
    :param diff: Unified diff of the change (possibly truncated).
    :param error: Empty on success; a message otherwise.
    :returns: The result dict returned to the MCP wrapper.
    """
    return {
        "path": path,
        "created": created,
        "bytes_written": bytes_written,
        "additions": additions,
        "deletions": deletions,
        "diff": diff,
        "error": error,
    }


def write_file(
    path: str,
    content: str,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Create or overwrite a file with *content*.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool (see ``klea_utils.mcp.server.bundled_tools``).

    The write is atomic (a temporary file is replaced into place).  An
    existing file keeps its mode; a UTF-8 BOM is preserved when either the
    old or the new content has one; the existing file's line ending is kept
    and the new content normalised to it.  Missing parent directories are
    created.  The result carries a unified diff against the previous content.

    :param path: File path to write; must resolve inside *project_root*.
    :param content: Complete file content.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with path, created, bytes_written, additions, deletions,
        diff, error.
    """
    logger.debug(f"Writing file\n{path = }\n{len(content) = }\n{project_root = }")

    the_path = Path(path)
    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return _result(path, error=str(exc))

    if the_path.is_dir():
        logger.warning(f"Path is a directory: {path}")
        return _result(path, error=f"Path is a directory: {path}")

    content_text, content_bom = file_ops.split_bom(content)

    existing = None
    if the_path.exists():
        try:
            existing = file_ops.read_whole_text(the_path)
        except FileEditError as exc:
            logger.warning(f"Cannot overwrite {path}: {exc}")
            return _result(str(the_path), error=str(exc))

    if existing is not None:
        bom = existing.bom or content_bom
        newline = existing.newline
        old_text = file_ops.normalize_newlines(existing.text)
        created = False
    else:
        bom = content_bom
        newline = file_ops.detect_newline(content_text)
        old_text = ""
        created = True

    new_text = file_ops.normalize_newlines(content_text)
    logger.debug(
        f"Write plan\n"
        f"{the_path = }\n"
        f"{created = }\n"
        f"{bom = }\n"
        f"{newline = }\n"
        f"{len(old_text) = }\n"
        f"{len(new_text) = }"
    )
    diff, additions, deletions = file_ops.diff_payload(
        old_text, new_text, str(the_path)
    )

    try:
        file_ops.write_whole_text(
            the_path, content_text, bom=bom, newline=newline, mode=None
        )
    except FileEditError as exc:
        logger.warning(f"Could not write {path}: {exc}")
        return _result(str(the_path), error=str(exc))

    try:
        bytes_written = the_path.stat().st_size
    except OSError:
        bytes_written = 0

    logger.debug(f"Wrote {the_path}\n{created = }\n{bytes_written = }")
    return _result(
        str(the_path),
        created=created,
        bytes_written=bytes_written,
        additions=additions,
        deletions=deletions,
        diff=diff,
    )
