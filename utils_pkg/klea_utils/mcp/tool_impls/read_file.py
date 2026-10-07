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
from klea_utils.mcp.tool_impls.file_ops import is_binary, split_bom
from klea_utils.mcp.tool_impls.list_files import missing_target_note, nearby_entries
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

#: Server-owned character budget for a single read.  This is deliberately not
#: exposed to the model: it bounds the response (and token cost) regardless of
#: what the caller asks for, so a large file is read by paging
#: (``offset``/``limit``, or ``char_offset`` for a single over-long line)
#: rather than pulled in one call.
_MAX_CHARS = 20_000

#: Default number of lines returned per read (a page).  ``None`` still means
#: "to the end", bounded by :data:`_MAX_CHARS`.
_DEFAULT_LIMIT = 500

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
    path: str = ".",
    offset: int = 1,
    char_offset: int = 0,
    limit: int | None = _DEFAULT_LIMIT,
    max_chars: int = _MAX_CHARS,
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

    Plain text must be valid UTF-8 (a leading UTF-8 BOM is stripped).  A
    binary file (containing a NUL byte) or a file that is not valid UTF-8 is
    refused with an error instead of being returned as garbled text.

    :param path: File path to read.  Defaults to the current directory
        (``"."``), which is not a file, so a missing path yields the
        nearby-entries fallback.
    :param offset: 1-indexed line to start reading from.
    :param char_offset: 0-indexed character position within *offset*'s line to
        start from (default 0).  Only needed to continue inside a single line
        longer than ``max_chars``: the returned ``next_char_offset`` gives the
        resume position.
    :param limit: Maximum number of lines to return.  ``None`` reads to the
        end of the file.
    :param max_chars: Character budget for the response (server-owned backstop;
        not exposed to the model), applied after the line slice.
    :param max_bytes: Maximum file size in bytes to read; larger files are
        refused with an error.
    :param line_numbers: Prefix each returned line with its line number
        (default).  Set ``False`` to return the raw line text, e.g. to copy
        a span into an edit tool's ``old_string``.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with path, content, line_start, line_end, total_lines,
        truncated, next_offset, error, note (plus ``nearby`` on a missing-file
        error).  A truncated result is a success (``error`` empty) that carries
        the line range actually returned and ``next_offset`` for continuing;
        ``nearby``/``note`` are populated on a missing/not-a-file error so the
        caller can see what exists instead.
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
        directory, nearby = nearby_entries(the_path, project_root)
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": f"Not a file: {path}",
            "nearby": nearby,
            "note": missing_target_note(path, directory, nearby),
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
    if char_offset < 0:
        logger.warning(f"Invalid char_offset {char_offset}; starting at 0")
        char_offset = 0
    # ``limit`` is a maximum line count: ``None`` means "to the end", and a
    # non-positive value is coerced to 1 rather than reading the whole file.
    if limit is not None and limit < 1:
        logger.warning(f"Invalid limit {limit}; reading a single line")
        limit = 1
    if max_chars < 1:
        logger.warning(f"Invalid max_chars {max_chars}; using 1")
        max_chars = 1

    suffix = the_path.suffix.lower()
    had_bom = False
    try:
        if suffix in (".html", ".htm"):
            # HTML is web content and may not be UTF-8; decode leniently.
            text, had_bom = split_bom(
                the_path.read_bytes().decode("utf-8", errors="replace")
            )
            content = _html_to_text(text)
        elif _should_convert(suffix):
            content = _converted_text(the_path)
        else:
            data = the_path.read_bytes()
            if is_binary(data):
                logger.warning(f"Refusing to read binary file: {path}")
                return {
                    "path": str(the_path),
                    "content": "",
                    "line_start": 1,
                    "line_end": 0,
                    "total_lines": 0,
                    "truncated": False,
                    "error": f"Cannot read binary file: {the_path}",
                }
            # Strict UTF-8: report invalid bytes rather than returning
            # replacement-character garbage.  A UTF-8 BOM is stripped.
            content, had_bom = split_bom(data.decode("utf-8"))
    except UnicodeDecodeError:
        logger.warning(f"Not valid UTF-8 text: {path}")
        return {
            "path": str(the_path),
            "content": "",
            "line_start": 1,
            "line_end": 0,
            "total_lines": 0,
            "truncated": False,
            "error": f"File is not valid UTF-8 text: {the_path}",
        }
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

    # The first segment may be a suffix of ``offset``'s line when continuing
    # inside an over-long line (``char_offset`` > 0); the rest are whole lines.
    segments: list[str] = []
    if sliced:
        first = sliced[0][char_offset:] if char_offset else sliced[0]
        segments = [first, *sliced[1:]]

    line_start = start + 1
    if line_numbers:
        rendered = [
            f"{line_no}: {segment}"
            for line_no, segment in zip(
                range(line_start, line_start + len(segments)), segments
            )
        ]
        first_prefix_len = len(f"{line_start}: ")
    else:
        rendered = list(segments)
        first_prefix_len = 0

    # Apply the character cap to the *rendered* text (line-number prefixes and
    # joining newlines included) so the returned content respects max_chars.
    if rendered:
        kept, next_offset, next_char_offset, char_limited = _cap_segments(
            rendered,
            max_chars,
            start_line=line_start,
            start_char=char_offset,
            first_prefix_len=first_prefix_len,
        )
    else:
        kept, next_offset, next_char_offset, char_limited = [], None, 0, False
    line_end = start + len(kept)
    truncated = char_limited or line_end < total_lines
    if truncated and next_offset is None:
        # Truncation came from the ``limit`` slice, not the character cap.
        next_offset = line_end + 1
        next_char_offset = 0

    content = "\n".join(kept)

    note = ""
    if truncated and next_offset is not None:
        if next_char_offset:
            note = (
                f"Output truncated mid-line: showing line {line_start} from "
                f"char {char_offset} to line {line_end} char {next_char_offset} "
                f"of {total_lines}. Continue with offset={next_offset}, "
                f"char_offset={next_char_offset}."
            )
        else:
            note = (
                f"Output truncated: showing lines {line_start}-{line_end} of "
                f"{total_lines}. Continue with offset={next_offset}."
            )
    elif not segments and total_lines:
        note = f"offset {offset} is past the end of the file ({total_lines} lines)."
    elif had_bom:
        note = "Stripped a UTF-8 BOM."

    logger.debug(
        f"Read file\n"
        f"{path = }\n"
        f"{line_start = }\n"
        f"{line_end = }\n"
        f"{total_lines = }\n"
        f"{len(content) = }\n"
        f"{truncated = }\n"
        f"{next_offset = }\n"
        f"{next_char_offset = }"
    )
    return {
        "path": str(the_path),
        "content": content,
        "line_start": line_start,
        "line_end": line_end,
        "total_lines": total_lines,
        "truncated": truncated,
        "next_offset": next_offset,
        "next_char_offset": next_char_offset,
        "error": "",
        "note": note,
    }


def _cap_segments(
    segments: list[str],
    max_chars: int,
    *,
    start_line: int,
    start_char: int,
    first_prefix_len: int,
) -> tuple[list[str], int | None, int, bool]:
    """Fit whole rendered segments within *max_chars*, splitting an oversized first.

    Segments (already rendered, so the character count includes any line-number
    prefix and the joining newlines) are added whole until the next would
    exceed the cap, so the returned text normally ends on a line boundary and
    line-based continuation is unambiguous.  When the *first* segment alone
    exceeds the cap (a single line longer than the cap), a character chunk of
    it is returned instead and the continuation is expressed in characters on
    the same line.

    :param segments: The rendered segments to return; the first may be a
        partial line when ``start_char`` > 0.
    :param max_chars: The character cap.
    :param start_line: 1-indexed line number of ``segments[0]``.
    :param start_char: Character offset of ``segments[0]`` within its (raw) line.
    :param first_prefix_len: Length of the line-number prefix prepended to
        ``segments[0]`` (0 when line numbers are not rendered); subtracted when
        mapping a chunk back to a character position in the raw line.
    :returns: ``(kept, next_offset, next_char_offset, truncated)``.  On
        truncation, ``next_offset`` is the line to resume from; when the page
        ends mid-line, ``next_offset`` is the last line shown and
        ``next_char_offset`` is the resume column.
    """
    kept: list[str] = []
    length = 0
    for index, segment in enumerate(segments):
        segment_char = start_char if index == 0 else 0
        added = len(segment) + (1 if kept else 0)  # include the joining newline
        if length + added <= max_chars:
            kept.append(segment)
            length += added
            continue
        if kept:
            # Stop before this whole segment; resume at its start.
            return kept, start_line + index, 0, True
        # The first segment alone exceeds the cap: return a character chunk.
        # ``max(1, ...)`` guarantees forward progress even when the cap is
        # smaller than the prefix (an absurd request).
        chunk = segment[:max_chars]
        raw_consumed = max(1, len(chunk) - first_prefix_len)
        return [chunk], start_line, segment_char + raw_consumed, True
    return kept, None, 0, False


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
