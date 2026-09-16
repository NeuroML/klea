#!/usr/bin/env python3
"""
File reading implementation for Klea MCP tools.

File: klea_utils/mcp/tool_impls/read_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import DocumentConversionError, PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access
from klea_utils.mcp.tool_impls.web_fetch import _html_to_text

logger = logging.getLogger(__name__)

#: Fallback suffixes treated as documents when the anydoc library is not
#: installed.  Used only so a document-like file still produces a helpful
#: "anydoc is not installed" error instead of being read as binary garbage;
#: when anydoc IS available, :func:`_should_convert` asks anydoc itself
#: (``format_from_extension``) which suffixes it supports, so this list does
#: not need to track every format anydoc adds.
_FALLBACK_ANYDOC_SUFFIXES = frozenset(
    {
        ".doc",
        ".docx",
        ".docm",
        ".ppt",
        ".pps",
        ".pot",
        ".pptx",
        ".pptm",
        ".ppsx",
        ".ppsm",
        ".xls",
        ".xlsx",
        ".xlsm",
        ".xlsb",
        ".odt",
        ".ods",
        ".odp",
        ".rtf",
        ".epub",
        ".csv",
        ".pdf",
    }
)

#: Raw read safety cap: larger files are refused rather than loaded into
#: memory or converted (which would also be wasteful for LLM input).
_DEFAULT_MAX_BYTES = 100 * 1024 * 1024

#: Maximum number of converted documents held in the in-memory cache.
_MAX_CACHE_ENTRIES = 4

#: In-memory cache of converted document text, keyed by
#: ``(resolved path, mtime_ns, size)`` so edits invalidate automatically.
#: Paging a converted document (offset/limit) therefore converts it only
#: once per process; a fresh session pays a one-time conversion cost when a
#: document is first read again.  Held only for document formats -- plain
#: text and HTML are read from disk on every call (disk read + split is
#: cheap, and avoids stale content for frequently edited source files).
_CONVERT_CACHE: "OrderedDict[tuple[str, int, int], str]" = OrderedDict()
_CONVERT_CACHE_LOCK = threading.Lock()

#: Cached result of probing whether the anydoc library is importable:
#: ``True``/``False`` once known, ``None`` before the first probe.
_ANYDOC_AVAILABLE: bool | None = None


def read_file(
    path: str,
    offset: int = 1,
    limit: int | None = 2000,
    max_chars: int = 100_000,
    max_bytes: int = _DEFAULT_MAX_BYTES,
    line_numbers: bool = True,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Read a file and return a slice of its text content.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool (see klea_utils.mcp.server.bundled_tools).

    Files are converted to plain text first: HTML is stripped with
    BeautifulSoup, and office documents/PDF/EPUB/CSV are converted to
    Markdown with the anydoc library; anything else is read as plain text.
    For document formats the offsets/limits apply to that *converted* text,
    and the returned ``line_end``/``total_lines`` let the caller continue
    reading a large document in pages.

    :param path: File path to read.
    :param offset: 1-indexed line to start reading from.
    :param limit: Maximum number of lines to return.  ``None`` reads to the
        end of the file.
    :param max_chars: Hard cap on characters returned, applied after the
        line slice.
    :param max_bytes: Maximum file size in bytes to read; larger files are
        refused with an error.
    :param line_numbers: Prefix each returned line with its line number
        (default).  Set ``False`` to return the raw line text, e.g. to copy
        a span into an edit tool's ``old_string``.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with path, content, line_start, line_end, total_lines,
        truncated, error.
    """
    logger.debug(
        f"Reading file\n"
        f"{path = }\n"
        f"{offset = }\n"
        f"{limit = }\n"
        f"{max_chars = }\n"
        f"{max_bytes = }\n"
        f"{line_numbers = }\n"
        f"{project_root = }"
    )

    the_path = Path(path)

    try:
        check_path_access(the_path, project_root)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for {path}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": str(exc),
        }

    if not the_path.is_file():
        logger.warning(f"Not a readable file: {path}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": f"Not a file: {path}",
        }

    size = the_path.stat().st_size
    if size > max_bytes:
        logger.warning(f"File too large ({size} bytes > {max_bytes}): {path}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": f"File too large to read: {size} bytes",
        }

    if offset < 1:
        logger.warning(f"Invalid offset {offset}; starting from line 1")
        offset = 1
    if limit is not None and limit < 1:
        logger.warning(f"Invalid limit {limit}; reading to end of file")
        limit = None

    suffix = the_path.suffix.lower()
    try:
        if suffix in (".html", ".htm"):
            content = _html_to_text(
                the_path.read_text(encoding="utf-8", errors="replace")
            )
        elif _should_convert(suffix):
            content = _converted_text(the_path)
        else:
            content = the_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        logger.warning(f"Could not read {path}: {exc}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": f"Could not read file: {exc}",
        }
    except ImportError:
        logger.warning(f"anydoc is not installed; cannot convert {path}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": "anydoc is not installed; cannot convert this file type",
        }
    except DocumentConversionError as exc:
        logger.warning(f"Could not convert {path}: {exc}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": str(exc),
        }

    lines = content.splitlines()
    total_lines = len(lines)
    start = offset - 1
    end = None if limit is None else start + limit
    sliced = lines[start:end]

    if line_numbers:
        rendered = [
            f"{line_no}: {line}"
            for line_no, line in zip(range(start + 1, start + len(sliced) + 1), sliced)
        ]
    else:
        rendered = sliced
    content = "\n".join(rendered)

    line_start = start + 1
    line_end = start + len(sliced)
    truncated = line_end < total_lines
    if len(content) > max_chars:
        content = content[:max_chars]
        truncated = True

    logger.debug(
        f"Read file\n"
        f"{path = }\n"
        f"{line_start = }\n"
        f"{line_end = }\n"
        f"{total_lines = }\n"
        f"{len(content) = }\n"
        f"{truncated = }"
    )
    return {
        "path": str(the_path),
        "content": content,
        "line_start": line_start,
        "line_end": line_end,
        "total_lines": total_lines,
        "truncated": truncated,
        "error": "",
    }


def _anydoc_available() -> bool:
    """Return whether the anydoc library can be imported, caching the result.

    :returns: ``True`` when anydoc is importable, ``False`` otherwise.
    """
    global _ANYDOC_AVAILABLE
    if _ANYDOC_AVAILABLE is None:
        try:
            # Lazy: anydoc is a Rust binary extension.  Importing it at module
            # level would load it even for servers that never read
            # office/PDF documents, and would break the import of this module
            # when the [mcp] extra's anydoc dependency is not installed.
            import anydoc  # noqa: F401

            _ANYDOC_AVAILABLE = True
        except ImportError:
            _ANYDOC_AVAILABLE = False
            logger.warning(
                "anydoc is not installed; document files cannot be converted"
            )
    return _ANYDOC_AVAILABLE


def _should_convert(suffix: str) -> bool:
    """Return whether *suffix* should be converted with the anydoc library.

    When anydoc is available, the decision is delegated to anydoc itself
    (``format_from_extension``) so newly supported formats are picked up
    automatically without maintaining a suffix list here.  When anydoc is
    missing, a small fallback list is used so document-like files still get
    the "anydoc is not installed" error instead of being read as binary.

    :param suffix: File extension, lower-cased and including the dot.
    :returns: ``True`` when the file should go through document conversion.
    """
    if not _anydoc_available():
        return suffix in _FALLBACK_ANYDOC_SUFFIXES
    import anydoc

    return anydoc.format_from_extension(suffix) is not None


def _converted_text(path: Path) -> str:
    """Return the converted Markdown for *path*, using the in-memory cache.

    The cache is keyed on ``(path, mtime_ns, size)`` so a file edited on
    disk is converted again; conversion happens outside the lock so a slow
    anydoc pass never blocks other readers, only the cache dict access is
    locked.

    :param path: Document file to convert.
    :returns: Converted Markdown text.
    :raises DocumentConversionError: when the file cannot be converted.
    """
    stat = path.stat()
    key = (str(path), stat.st_mtime_ns, stat.st_size)

    with _CONVERT_CACHE_LOCK:
        cached = _CONVERT_CACHE.get(key)
        if cached is not None:
            _CONVERT_CACHE.move_to_end(key)
            return cached

    converted = _to_markdown(path.read_bytes(), path.suffix)

    with _CONVERT_CACHE_LOCK:
        _CONVERT_CACHE[key] = converted
        _CONVERT_CACHE.move_to_end(key)
        while len(_CONVERT_CACHE) > _MAX_CACHE_ENTRIES:
            _CONVERT_CACHE.popitem(last=False)
    return converted


def _to_markdown(data: bytes, suffix: str) -> str:
    """Convert *data* to Markdown with the anydoc library.

    :param data: Raw file content.
    :param suffix: File extension, used to name signature-less formats
        (e.g. CSV) that content detection cannot identify.
    :returns: Markdown text.
    :raises DocumentConversionError: when anydoc cannot convert the file.
    """
    # Lazy: anydoc is a Rust binary extension.  Importing it at module level
    # would load it even for servers that never read office/PDF documents,
    # and would break the import of this module when the [mcp] extra's
    # anydoc dependency is not installed.
    import anydoc

    fmt = anydoc.format_from_bytes(data)
    if fmt is None:
        # Signature-less formats (e.g. CSV) cannot be detected from content;
        # name the format from the extension instead.
        fmt = anydoc.format_from_extension(suffix)
    try:
        if fmt:
            return anydoc.to_markdown_bytes(data, fmt)
        return anydoc.to_markdown_bytes(data)
    except anydoc.ConvertError as exc:
        logger.warning(f"anydoc could not convert file: {exc}")
        raise DocumentConversionError(
            f"Could not convert file to text: {type(exc).__name__}: {exc}"
        ) from exc
