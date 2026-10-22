#!/usr/bin/env python3
"""
File reading implementation for Klea MCP tools.

File: klea_utils/mcp/tool_impls/read_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import codecs
import logging
import threading
from collections import OrderedDict
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from klea_utils.mcp.errors import DocumentConversionError
from klea_utils.mcp.tool_impls.file_ops import (
    split_bom,
    split_lines,
)
from klea_utils.mcp.tool_impls.list_files import missing_target_note, nearby_entries
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

#: Character allowance for a rendered line-number prefix when capping a
#: streamed line (any realistic line number fits well within this).
_PREFIX_ALLOWANCE = 64

#: Read granularity for the streaming plain-text reader.
_STREAM_CHUNK_BYTES = 64 * 1024

#: Files larger than this get a "prefer grep" hint when a page is truncated.
_LARGE_FILE_HINT_BYTES = 1024 * 1024

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
    refused with an error instead of being returned as garbled text.  Lines
    are delimited by ``\\n`` after normalising CRLF and lone-CR endings, so the
    line numbers match editors and ``wc -l`` (``str.splitlines`` would also
    break on form feeds and Unicode separators).

    :param path: File path to read.  Defaults to the current directory
        (``"."``), which is not a file, so a missing path yields the
        nearby-entries fallback.
    :param offset: 1-indexed line to start reading from.
    :param char_offset: 0-indexed character position within *offset*'s line to
        start from (default 0).  Only needed to continue inside a single line
        longer than ``max_chars``: the returned ``next_char_offset`` gives the
        resume position.
    :param limit: Maximum number of lines to return.  ``None`` removes only
        the line limit; the server-owned ``max_chars`` budget still applies, so
        the result can still be truncated (with ``next_offset``).
    :param max_chars: Character budget for the response (server-owned backstop;
        not exposed to the model), applied after the line slice.  The effective
        value is reported as ``char_budget``.
    :param max_bytes: Per-call scan cap in bytes (server-owned).  Plain text is
        streamed, so the scan stops after this many bytes and the page is
        returned truncated with a hint; HTML/office/PDF conversions and a
        file that grows mid-read are still bounded by it.
    :param line_numbers: Prefix each returned line with its line number
        (default).  Set ``False`` to return the raw line text, e.g. to copy
        a span into an edit tool's ``old_string``.
    :param project_root: Base directory used to locate nearby entries when the
        target is missing; the boundary itself is enforced by the client-side
        gate (ADR-0007 update 2026-10-08).
    :returns: dict with path, content, line_start, line_end, total_lines,
        truncated, truncation_reason, char_budget, next_offset,
        next_char_offset, error, note (plus ``nearby`` on a missing-file
        error).  A truncated result is a success (``error`` empty) that carries
        the line range actually returned and ``next_offset`` for continuing;
        ``nearby``/``note`` are populated on a missing/not-a-file error so the
        caller can see what exists instead.  ``total_lines`` is exact only when
        the streamed read reached EOF; a page that stops early reports it as
        ``None``.  ``char_budget`` is the effective server character cap, and
        ``truncation_reason`` is ``"char_budget"``, ``"line_limit"`` or
        ``"scan_cap"`` when truncated (empty otherwise), so a ``limit=None``
        read that still reports ``truncated`` shows why.  A truncated page of a
        large file also carries a note suggesting ``grep`` to locate the region.
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

    try:
        size = the_path.stat().st_size
    except OSError as exc:
        logger.warning(f"Could not stat {path}: {exc}")
        size = None

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
    delimited_raw = False
    streamed = False
    reached_eof = True
    scan_capped = False
    stop_reason = ""
    total_lines: int | None = 0
    segments: list[str] = []
    try:
        if suffix in (".html", ".htm"):
            # HTML is web content and may not be UTF-8; decode leniently.  It is
            # parsed whole (BeautifulSoup), then paged like any other text.
            data = _read_bounded(the_path, max_bytes)
            if data is None:
                return _too_large_result(the_path, max_bytes)
            html, had_bom = split_bom(data.decode("utf-8", errors="replace"))
            segments, total_lines, offset = _slice_segments(
                _html_to_text(html), offset, limit, char_offset
            )
        elif _should_convert(suffix):
            # Conversions (anydoc) read the whole document, so keep the size
            # guard: a huge document is refused rather than pulled into memory.
            if size is not None and size > max_bytes:
                return _too_large_result(the_path, max_bytes)
            segments, total_lines, offset = _slice_segments(
                _converted_text(the_path), offset, limit, char_offset
            )
        else:
            # Plain text: stream the page lazily, so memory is O(page) and files
            # larger than ``max_bytes`` still page (``max_bytes`` now bounds the
            # bytes scanned in one call rather than refusing the file).
            delimited_raw = suffix in {".csv", ".tsv"}
            streamed = True
            (
                segments,
                total_lines,
                reached_eof,
                scan_capped,
                had_bom,
                stop_reason,
            ) = _read_streamed_page(
                the_path,
                offset=offset,
                limit=limit,
                char_offset=char_offset,
                max_chars=max_chars,
                max_bytes=max_bytes,
            )
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
    except _BinaryFileError:
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

    start = offset - 1
    line_start = start + 1
    from_end = offset < 0

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
    if streamed:
        # A streamed page knows the total only when it reaches EOF.
        truncated = char_limited or not reached_eof
    elif from_end:
        # For a tail read, EOF is line -1; an empty file has no line to reach.
        truncated = char_limited or (bool(total_lines) and line_end != -1)
    else:
        truncated = char_limited or (total_lines is not None and line_end < total_lines)
    if truncated and next_offset is None and kept:
        # Truncation came from the page boundary / scan cap, not the char cap.
        next_offset = line_end + 1
        next_char_offset = 0

    if not truncated:
        truncation_reason = ""
    elif char_limited or stop_reason == "chars":
        # Also covers a page that filled the budget exactly and has a
        # continuation: ``_cap_segments`` did not trim it, but the budget is
        # still what stopped the read.
        truncation_reason = "char_budget"
    elif scan_capped or stop_reason == "cap":
        truncation_reason = "scan_cap"
    else:
        truncation_reason = "line_limit"

    content = "\n".join(kept)

    total_suffix = f" of {total_lines}" if total_lines is not None else ""
    budget_note = (
        f" by the server character budget ({max_chars} chars)" if char_limited else ""
    )
    note = ""
    if truncated and next_offset is not None:
        if next_char_offset:
            note = (
                f"Output truncated mid-line{budget_note}: showing line "
                f"{line_start} from char {char_offset} to line {line_end} char "
                f"{next_char_offset}{total_suffix}. Continue with "
                f"offset={next_offset}, char_offset={next_char_offset}."
            )
        else:
            note = (
                f"Output truncated{budget_note}: showing lines "
                f"{line_start}-{line_end}{total_suffix}. Continue with "
                f"offset={next_offset}."
            )
    elif truncated and not reached_eof:
        note = (
            "The requested line is past the region scanned in one call; the file "
            "is too large to scan that far. Use grep to locate the region, then "
            "read around it."
        )
    elif not segments and total_lines:
        note = f"offset {offset} is past the end of the file ({total_lines} lines)."
    elif had_bom:
        note = "Stripped a UTF-8 BOM."
    elif delimited_raw:
        note = "Delimited file read as physical lines; quoted fields may span lines."

    if limit is None and char_limited and note:
        note = (
            f"{note} limit=None removes only the line limit, not the server "
            "character budget."
        )

    if truncated and size is not None and size > _LARGE_FILE_HINT_BYTES:
        note = (
            f"{note} Large file: prefer grep to find the region you need, then "
            "read around it with offset/limit."
        ).strip()

    char_budget = max_chars
    logger.debug(
        f"Read file\n"
        f"{path = }\n"
        f"{line_start = }\n"
        f"{line_end = }\n"
        f"{total_lines = }\n"
        f"{len(content) = }\n"
        f"{truncated = }\n"
        f"{truncation_reason = }\n"
        f"{char_budget = }\n"
        f"{next_offset = }\n"
        f"{next_char_offset = }\n"
        f"{streamed = }\n"
        f"{scan_capped = }"
    )
    return {
        "path": str(the_path),
        "content": content,
        "line_start": line_start,
        "line_end": line_end,
        "total_lines": total_lines,
        "truncated": truncated,
        "truncation_reason": truncation_reason,
        "char_budget": char_budget,
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


def _read_bounded(path: Path, max_bytes: int) -> bytes | None:
    """Read at most *max_bytes* bytes, or return ``None`` if the file exceeds it.

    Reading a bounded amount (instead of ``read_bytes``) both caps memory and
    closes the stat/read race: a file that grows after the size check cannot
    make this read unbounded.

    :param path: File to read.
    :param max_bytes: Maximum number of bytes to accept.
    :returns: The file bytes, or ``None`` when the file is larger than
        *max_bytes*.
    """
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    return None if len(data) > max_bytes else data


def _too_large_result(path: Path, max_bytes: int) -> dict[str, Any]:
    """Return the standard "file too large" error result."""
    return {
        "path": str(path),
        "content": "",
        "line_start": 1,
        "line_end": 0,
        "total_lines": 0,
        "truncated": False,
        "error": f"File too large to read: over {max_bytes} bytes",
    }


def _slice_segments(
    content: str, offset: int, limit: int | None, char_offset: int
) -> tuple[list[str], int, int]:
    """Return the raw page lines of *content*, its total, and the effective offset.

    The first segment may be a suffix of the start line when continuing inside
    an over-long line (``char_offset`` > 0); the rest are whole lines.

    A negative *offset* counts from the end (``-1`` is the last line).  The
    returned effective offset is then negative relative to EOF and clamped to
    the first line when the request reaches past the start.

    :param content: Decoded/converted file text.
    :param offset: 1-indexed line to start from; negative counts from the end.
    :param limit: Maximum number of lines, or ``None`` for no line limit.
    :param char_offset: 0-indexed character within the start line.
    :returns: ``(segments, total_lines, effective_offset)``.  For a forward
        read ``effective_offset`` equals *offset*; for a tail read it is the
        clamped signed line number (``-total_lines`` at the earliest).
    """
    lines = split_lines(content)
    total_lines = len(lines)
    if offset < 0:
        if total_lines == 0:
            return [], 0, offset
        start = max(0, total_lines + offset)
        effective_offset = start - total_lines
    else:
        start = offset - 1
        effective_offset = offset
    end = None if limit is None else start + limit
    sliced = lines[start:end]
    segments: list[str] = []
    if sliced:
        first = sliced[0][char_offset:] if char_offset else sliced[0]
        segments = [first, *sliced[1:]]
    return segments, total_lines, effective_offset


@dataclass(frozen=True)
class _TailStart:
    """Result of locating the start of a tail line."""

    #: Byte offset of the first byte of the start line (``None`` on scan cap).
    byte_offset: int | None
    #: Signed line number of the start line (``None`` on scan cap/empty file).
    line_number: int | None
    #: Exact total line count when the scan reached byte 0, else ``None``.
    total_lines: int | None
    #: Whether ``max_bytes`` was hit before the requested line was found.
    truncated: bool


def _locate_tail_start(
    path: Path,
    n: int,
    max_bytes: int,
    chunk_size: int = _STREAM_CHUNK_BYTES,
) -> _TailStart:
    """Locate the byte offset of the *n*th line from the end of *path*.

    Scans backwards in chunks, counting ``\\n`` and lone ``\\r`` terminators
    (a CRLF is one terminator) and mirroring :func:`file_ops.split_lines`
    semantics: a single trailing line ending does not add an empty line.  The
    scan stops as soon as the requested line is found or when *max_bytes* have
    been read, so it is O(tail) I/O and O(chunk) memory.

    :param path: Text file to inspect.
    :param n: Number of lines from the end (``1`` is the last line).
    :param max_bytes: Per-call scan cap, counted backwards from EOF.
    :param chunk_size: Read granularity in bytes.
    :returns: A :class:`_TailStart`.  Reaching byte 0 also reports the exact
        ``total_lines``; hitting the cap first sets ``truncated`` and leaves
        the byte/line fields ``None``.
    """
    n = max(1, n)
    chunk_size = max(1, chunk_size)
    size = path.stat().st_size
    if size == 0:
        return _TailStart(0, None, 0, False)

    with path.open("rb") as handle:
        # Strip one trailing line ending so it does not count as an extra line.
        handle.seek(max(0, size - 2))
        tail = handle.read(2)
        if tail.endswith(b"\r\n"):
            effective_end = size - 2
        elif tail.endswith((b"\n", b"\r")):
            effective_end = size - 1
        else:
            effective_end = size
        if effective_end == 0:
            # The file is a single line ending, i.e. one empty line.
            return _TailStart(0, -1, 1, False)

        pos = effective_end
        terminators = 0
        bytes_scanned = 0
        # The line-ending bytes stripped above are not counted against the
        # scan budget; the overage is at most two bytes.
        right_byte: int | None = None
        while pos > 0:
            remaining = max_bytes - bytes_scanned
            if remaining <= 0:
                return _TailStart(None, None, None, True)
            read_len = min(chunk_size, pos, remaining)
            read_start = pos - read_len
            handle.seek(read_start)
            chunk = handle.read(read_len)
            if len(chunk) != read_len:
                # The file shrank under us; the region is no longer complete.
                return _TailStart(None, None, None, True)
            bytes_scanned += read_len
            for index in range(read_len - 1, -1, -1):
                byte = chunk[index]
                if byte == 0x0A:  # final byte of an LF or CRLF terminator
                    terminators += 1
                    if terminators == n:
                        return _TailStart(read_start + index + 1, -n, None, False)
                elif byte == 0x0D:  # lone CR unless followed by LF
                    next_byte = chunk[index + 1] if index + 1 < read_len else right_byte
                    if next_byte == 0x0A:
                        continue
                    terminators += 1
                    if terminators == n:
                        return _TailStart(read_start + index + 1, -n, None, False)
            right_byte = chunk[0]
            pos = read_start

    total_lines = terminators + 1
    return _TailStart(0, -total_lines, total_lines, False)


class _BinaryFileError(Exception):
    """Raised by the streaming reader when it sees a NUL byte."""


class _StreamState:
    """Mutable state shared with the streaming line reader."""

    __slots__ = ("bytes_read", "capped", "exhausted", "had_bom")

    def __init__(self) -> None:
        self.bytes_read = 0
        self.capped = False
        self.exhausted = False
        self.had_bom = False


def _peek_for_eof(lines: Iterator[tuple[str, bool]]) -> None:
    """Consume one line so the reader can set ``state.exhausted`` at EOF.

    The page collector breaks as soon as it is full (line limit or character
    budget).  Without this peek, a page that ended exactly at EOF would still
    report ``reached_eof=False`` and be mislabelled as truncated.

    :param lines: The line iterator being collected from.
    """
    try:
        next(lines)
    except StopIteration:
        pass


def _collect_page(
    handle: BinaryIO,
    *,
    first_line_no: int,
    skip_lines: int,
    limit: int | None,
    char_offset: int,
    max_chars: int,
    max_bytes: int,
    strip_bom: bool = True,
) -> tuple[list[str], int, bool, bool, bool, str]:
    """Collect one page of lines from an already-positioned *handle*.

    Lines are produced lazily by :func:`_iter_capped_lines`; the scan stops once
    the page is satisfied (``limit`` lines or the ``max_chars`` budget) or the
    ``max_bytes`` scan cap is hit, so only the page's bytes are held in memory.
    Both the forward and tail readers use this so their cap and EOF behaviour
    cannot diverge.

    :param handle: Open binary handle positioned at the first byte to read.
    :param first_line_no: Line number assigned to the first line yielded.
        Positive for a forward read, negative (relative to EOF) for a tail.
    :param skip_lines: Lines to discard before collecting (used by a forward
        read to reach ``offset``).
    :param limit: Maximum number of lines to collect, or ``None`` for no line
        limit.
    :param char_offset: 0-indexed character within the first collected line.
    :param max_chars: Character budget for the page (bounds the scan too).
    :param max_bytes: Per-call scan cap in bytes.
    :param strip_bom: Strip a leading BOM (only when reading from byte 0).
    :returns: ``(segments, last_line_no, reached_eof, scan_capped, had_bom,
        stop_reason)``, where ``stop_reason`` is ``"eof"``, ``"limit"``,
        ``"chars"`` or ``"cap"``.  It says which bound stopped the page, so the
        caller can report an accurate truncation reason even when the rendered
        text happens to fit the character budget exactly.
    """
    state = _StreamState()
    max_line_chars = char_offset + max_chars + _PREFIX_ALLOWANCE
    segments: list[str] = []
    line_no = first_line_no - 1
    skipped = 0
    collected = 0
    accumulated = 0
    stop_reason = "eof"
    lines = _iter_capped_lines(
        handle,
        state,
        max_line_chars=max_line_chars,
        max_bytes=max_bytes,
        strip_bom=strip_bom,
    )
    for text, _truncated in lines:
        line_no += 1
        if skipped < skip_lines:
            skipped += 1
            continue
        segment = text[char_offset:] if collected == 0 and char_offset else text
        segments.append(segment)
        collected += 1
        accumulated += len(segment) + (1 if collected > 1 else 0)
        if limit is not None and collected >= limit:
            stop_reason = "limit"
            _peek_for_eof(lines)
            break
        if accumulated >= max_chars:
            stop_reason = "chars"
            _peek_for_eof(lines)
            break
    else:
        stop_reason = "eof" if state.exhausted else "cap"
    return (
        segments,
        line_no,
        state.exhausted,
        state.capped,
        state.had_bom,
        stop_reason,
    )


def _read_streamed_page(
    path: Path,
    *,
    offset: int,
    limit: int | None,
    char_offset: int,
    max_chars: int,
    max_bytes: int,
) -> tuple[list[str], int | None, bool, bool, bool, str]:
    """Collect one page of lines from *path* without loading the whole file.

    This is the forward-read wrapper around :func:`_collect_page`; it opens the
    file at byte 0, skips to ``offset`` and reports the absolute total only when
    the scan reached EOF.

    :param path: Text file to read.
    :param offset: 1-indexed line to start from.
    :param limit: Maximum number of lines, or ``None`` for no line limit.
    :param char_offset: 0-indexed character within ``offset``'s line.
    :param max_chars: Character budget for the page (bounds the scan too).
    :param max_bytes: Per-call scan cap in bytes.
    :returns: ``(segments, total_lines, reached_eof, scan_capped, had_bom,
        stop_reason)``.  ``total_lines`` is ``None`` unless the scan reached
        EOF.
    """
    with path.open("rb") as handle:
        (
            segments,
            line_no,
            reached_eof,
            scan_capped,
            had_bom,
            stop_reason,
        ) = _collect_page(
            handle,
            first_line_no=1,
            skip_lines=offset - 1,
            limit=limit,
            char_offset=char_offset,
            max_chars=max_chars,
            max_bytes=max_bytes,
            strip_bom=True,
        )
    total_lines = line_no if reached_eof else None
    return (
        segments,
        total_lines,
        reached_eof,
        scan_capped,
        had_bom,
        stop_reason,
    )


@dataclass(frozen=True)
class _TailPage:
    """One tail page plus the locator metadata needed by ``read_file``."""

    segments: list[str]
    line_number: int | None
    total_lines: int | None
    reached_eof: bool
    scan_capped: bool
    had_bom: bool
    stop_reason: str
    locator_truncated: bool


def _read_tail_page(
    path: Path,
    *,
    offset: int,
    limit: int | None,
    char_offset: int,
    max_chars: int,
    max_bytes: int,
) -> _TailPage:
    """Collect one page starting the given number of lines from the end.

    The backward locator finds the byte offset of line ``offset`` (negative,
    relative to EOF); the page is then read forward from there with the same
    collector the forward reader uses.  The locator scan and the page read are
    each bounded by ``max_bytes``.

    :param path: Text file to read.
    :param offset: Negative line number to start from (``-1`` is the last
        line).
    :param limit: Maximum number of lines, or ``None`` for no line limit.
    :param char_offset: 0-indexed character within the start line.
    :param max_chars: Character budget for the page (bounds the scan too).
    :param max_bytes: Per-call scan cap in bytes, for the locator and the page.
    :returns: A :class:`_TailPage`.  ``locator_truncated`` is set when the
        backward scan could not reach the requested line within ``max_bytes``;
        the other fields then describe an empty page.
    """
    n = max(1, -offset)
    start = _locate_tail_start(path, n, max_bytes)
    if start.truncated:
        return _TailPage(
            segments=[],
            line_number=None,
            total_lines=None,
            reached_eof=False,
            scan_capped=True,
            had_bom=False,
            stop_reason="cap",
            locator_truncated=True,
        )
    if start.total_lines == 0:
        # The caller reports the empty-file / past-the-start case.
        return _TailPage(
            segments=[],
            line_number=None,
            total_lines=0,
            reached_eof=True,
            scan_capped=False,
            had_bom=False,
            stop_reason="eof",
            locator_truncated=False,
        )

    byte_offset = start.byte_offset
    line_number = start.line_number
    if byte_offset is None or line_number is None:
        return _TailPage(
            segments=[],
            line_number=None,
            total_lines=None,
            reached_eof=False,
            scan_capped=True,
            had_bom=False,
            stop_reason="cap",
            locator_truncated=True,
        )

    with path.open("rb") as handle:
        handle.seek(byte_offset)
        (
            segments,
            _last_line_no,
            reached_eof,
            scan_capped,
            had_bom,
            stop_reason,
        ) = _collect_page(
            handle,
            first_line_no=line_number,
            skip_lines=0,
            limit=limit,
            char_offset=char_offset,
            max_chars=max_chars,
            max_bytes=max_bytes,
            strip_bom=byte_offset == 0,
        )
    return _TailPage(
        segments=segments,
        line_number=line_number,
        total_lines=start.total_lines,
        reached_eof=reached_eof,
        scan_capped=scan_capped,
        had_bom=had_bom,
        stop_reason=stop_reason,
        locator_truncated=False,
    )


def _iter_capped_lines(
    handle: BinaryIO,
    state: _StreamState,
    *,
    max_line_chars: int,
    max_bytes: int,
    chunk_size: int = _STREAM_CHUNK_BYTES,
    strip_bom: bool = True,
) -> Iterator[tuple[str, bool]]:
    """Yield ``(line, truncated)`` pairs read lazily from a binary *handle*.

    Decodes incrementally (so a multibyte character split across a chunk is
    handled), normalises CRLF and lone CR to LF across chunk boundaries, and
    caps each line at *max_line_chars* (the rest of an over-long line is
    discarded, with ``truncated`` set).  Stops at EOF (``state.exhausted``) or
    once *max_bytes* have been read (``state.capped``).  A NUL byte raises
    :class:`_BinaryFileError`; invalid UTF-8 raises ``UnicodeDecodeError``.

    :param handle: Open binary file handle.
    :param state: Mutable state updated as the scan proceeds.
    :param max_line_chars: Maximum characters kept per line.
    :param max_bytes: Per-call scan cap in bytes.
    :param chunk_size: Read granularity in bytes.
    :param strip_bom: Strip a leading UTF-8 BOM from the first decoded chunk.
        Set ``False`` when reading from a mid-file seek, where a leading
        U+FEFF is real content rather than a BOM.
    :yields: ``(line_text, truncated)`` for each line, without its newline.
    """
    decoder = codecs.getincrementaldecoder("utf-8")()
    carry = ""
    discarding = False
    pending_cr = False
    first = True
    while True:
        remaining = max_bytes - state.bytes_read
        if remaining <= 0:
            state.capped = True
            return
        raw = handle.read(min(chunk_size, remaining))
        eof = not raw
        if raw:
            state.bytes_read += len(raw)
            if b"\x00" in raw:
                raise _BinaryFileError()
            text = decoder.decode(raw)
        else:
            # Flush the decoder; raises UnicodeDecodeError on a split character.
            text = decoder.decode(b"", final=True)
            if pending_cr:
                text = "\n" + text
                pending_cr = False
        if first and text:
            first = False
            if strip_bom and text.startswith("\ufeff"):
                text = text[1:]
                state.had_bom = True
        if pending_cr:
            text = "\r" + text
            pending_cr = False
        if text.endswith("\r"):
            pending_cr = True
            text = text[:-1]
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        pos = 0
        while True:
            nl = text.find("\n", pos)
            if nl == -1:
                remainder = text[pos:]
                if not discarding:
                    room = max_line_chars - len(carry)
                    if room > 0:
                        carry += remainder[:room]
                    if len(carry) >= max_line_chars:
                        discarding = True
                break
            segment = text[pos:nl]
            if discarding:
                truncated = True
            else:
                room = max_line_chars - len(carry)
                if room > 0:
                    carry += segment[:room]
                truncated = len(segment) > room
            yield carry, truncated
            carry = ""
            discarding = False
            pos = nl + 1

        if eof:
            if carry:
                yield carry, discarding
            state.exhausted = True
            return


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
