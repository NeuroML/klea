#!/usr/bin/env python3
"""
Tests for the ``read_file`` truncation / paging contract.

A truncated read is a partial *success* (empty ``error``): it ends on a line
boundary, ``line_end`` reflects the last line actually returned, and the
result carries ``next_offset`` + a ``note`` so the caller can continue.  A
missing file remains an error with a ``nearby``/``note`` remediation hint.

File: tests/test_read_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import os

import pytest
from klea_utils.mcp.tool_impls.read_file import (
    _locate_tail_start,
    _read_tail_page,
    _slice_segments,
    read_file,
)
from klea_utils.mcp.tool_result import to_result


def _write(tmp_path, n):
    """Write an *n*-line text file and return its path."""
    f = tmp_path / "t.txt"
    f.write_text("\n".join(f"line {i}" for i in range(1, n + 1)))
    return f


def test_char_cap_truncates_on_a_line_boundary(tmp_path):
    """The char cap ends on a whole line and reports where to continue."""
    f = _write(tmp_path, 1000)
    result = read_file(str(f), max_chars=100, project_root=str(tmp_path))

    assert result["error"] == ""
    assert result["truncated"] is True
    assert len(result["content"]) <= 100
    returned = result["content"].split("\n")
    assert result["line_end"] == result["line_start"] + len(returned) - 1
    assert result["next_offset"] == result["line_end"] + 1
    assert f"offset={result['next_offset']}" in result["note"]


def test_continue_from_next_offset_has_no_gap_or_overlap(tmp_path):
    """Paging with ``next_offset`` reads adjacent lines, with no duplication."""
    f = _write(tmp_path, 1000)
    first = read_file(str(f), max_chars=200, project_root=str(tmp_path))
    second = read_file(
        str(f), offset=first["next_offset"], max_chars=200, project_root=str(tmp_path)
    )

    assert second["line_start"] == first["line_end"] + 1
    assert first["content"].split("\n")[-1] != second["content"].split("\n")[0]


def test_everything_fits_reports_no_truncation(tmp_path):
    """A file within the cap has no note or next_offset and is not truncated."""
    f = _write(tmp_path, 5)
    result = read_file(str(f), max_chars=100_000, project_root=str(tmp_path))

    assert result["truncated"] is False
    assert result["next_offset"] is None
    assert result["note"] == ""
    assert result["error"] == ""


def test_character_budget_at_eof_is_not_reported_as_truncated(tmp_path):
    """A page that exactly fills the budget at EOF is complete, not truncated."""
    f = tmp_path / "exact.txt"
    f.write_text("abc\nxyz")

    result = read_file(
        str(f), max_chars=7, line_numbers=False, project_root=str(tmp_path)
    )

    assert result["content"] == "abc\nxyz"
    assert result["truncated"] is False
    assert result["truncation_reason"] == ""
    assert result["total_lines"] == 2
    assert result["next_offset"] is None


def test_character_budget_with_a_continuation_is_truncated(tmp_path):
    """Filling the budget is still a truncation when another line follows."""
    f = tmp_path / "exact.txt"
    f.write_text("abc\nxyz\nmore")

    result = read_file(
        str(f), max_chars=7, line_numbers=False, project_root=str(tmp_path)
    )

    assert result["content"] == "abc\nxyz"
    assert result["truncated"] is True
    assert result["truncation_reason"] == "char_budget"
    assert result["next_offset"] == 3


def test_limit_truncation_reports_next_offset(tmp_path):
    """A line-``limit`` truncation also carries a continuation offset."""
    f = _write(tmp_path, 10)
    result = read_file(str(f), offset=1, limit=3, project_root=str(tmp_path))

    assert result["line_end"] == 3
    assert result["truncated"] is True
    assert result["next_offset"] == 4
    assert "offset=4" in result["note"]


def test_truncation_is_not_an_error(tmp_path):
    """A truncated read is a partial success, so MCP ``isError`` is false."""
    f = _write(tmp_path, 1000)
    result = read_file(str(f), max_chars=100, project_root=str(tmp_path))

    assert to_result(result).is_error is False


def test_over_long_line_pages_by_characters(tmp_path):
    """A single line longer than the cap is paged with ``char_offset``."""
    line = "x" * 250
    f = tmp_path / "big.txt"
    f.write_text(line + "\n")

    result = read_file(
        str(f), max_chars=100, project_root=str(tmp_path), line_numbers=False
    )
    assert result["truncated"] is True
    assert len(result["content"]) == 100
    assert result["next_offset"] == 1
    assert result["next_char_offset"] == 100
    assert "char_offset=100" in result["note"]

    # Paging by character reconstructs the whole line with no gaps or overlap.
    chunks = [result["content"]]
    offset, char = result["next_offset"], result["next_char_offset"]
    while result["truncated"]:
        result = read_file(
            str(f),
            offset=offset,
            char_offset=char,
            max_chars=100,
            project_root=str(tmp_path),
            line_numbers=False,
        )
        chunks.append(result["content"])
        offset, char = result["next_offset"], result["next_char_offset"]
    assert "".join(chunks) == line


def test_over_long_line_char_offset_accounts_for_the_prefix(tmp_path):
    """With line numbers on, the resume column is within the raw line."""
    f = tmp_path / "big.txt"
    f.write_text("x" * 100 + "\n")

    result = read_file(str(f), max_chars=10, project_root=str(tmp_path))

    # "1: " (prefix) + 7 raw characters fills the 10-char cap.
    assert result["content"] == "1: " + "x" * 7
    assert result["next_offset"] == 1
    assert result["next_char_offset"] == 7


def test_limit_zero_reads_one_line(tmp_path):
    """A non-positive ``limit`` reads one line, not the whole file."""
    f = _write(tmp_path, 10)
    result = read_file(str(f), offset=1, limit=0, project_root=str(tmp_path))

    assert result["content"] == "1: line 1"
    assert result["line_end"] == 1
    assert result["truncated"] is True
    assert result["next_offset"] == 2


def test_offset_past_eof_reports_a_note(tmp_path):
    """An offset past the end is empty and explained by a note."""
    f = _write(tmp_path, 3)
    result = read_file(str(f), offset=50, limit=5, project_root=str(tmp_path))

    assert result["content"] == ""
    assert result["truncated"] is False
    assert "past the end" in result["note"]


def test_missing_file_is_still_an_error(tmp_path):
    """The missing-file case remains an error (with a note), unchanged."""
    result = read_file(str(tmp_path / "nope.txt"), project_root=str(tmp_path))

    assert result["error"] != ""
    assert to_result(result).is_error is True


def test_form_feed_is_not_a_line_break(tmp_path):
    """A form feed stays in the line (unlike ``str.splitlines``)."""
    f = tmp_path / "ff.txt"
    f.write_text("a\fb\n")

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["total_lines"] == 1
    assert result["content"] == "1: a\fb"


def test_crlf_and_lone_cr_are_normalised(tmp_path):
    """CRLF and lone CR line endings split like LF, without stray \\r."""
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"a\r\nb\rc\n")

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["total_lines"] == 3
    assert result["content"] == "1: a\n2: b\n3: c"


def test_trailing_newline_is_not_an_extra_line(tmp_path):
    """A single trailing newline does not add a blank final line."""
    f = tmp_path / "trail.txt"
    f.write_text("a\nb\n")

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["total_lines"] == 2


def test_raw_delimited_file_gets_a_note(tmp_path, monkeypatch):
    """A raw (unconverted) delimited file warns that fields may span lines."""
    from klea_utils.mcp.tool_impls import read_file as read_file_module

    monkeypatch.setattr(read_file_module, "_should_convert", lambda _suffix: False)
    f = tmp_path / "data.tsv"
    f.write_text('a\tb\n"x\ny"\t2\n')

    result = read_file(str(f), project_root=str(tmp_path))

    assert "physical lines" in result["note"]


def test_read_bounded_helper(tmp_path):
    """The bounded reader never returns more than max_bytes."""
    from klea_utils.mcp.tool_impls import read_file as read_file_module

    f = tmp_path / "x.bin"
    f.write_bytes(b"0123456789")

    assert read_file_module._read_bounded(f, 10) == b"0123456789"
    assert read_file_module._read_bounded(f, 100) == b"0123456789"
    assert read_file_module._read_bounded(f, 9) is None


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="mkfifo is not available")
def test_fifo_is_not_a_file(tmp_path):
    """A FIFO is not a regular file and is reported as such (not opened)."""
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)

    result = read_file(str(fifo), project_root=str(tmp_path))

    assert result["content"] == ""
    assert "not a file" in result["error"].lower()


def test_binary_file_is_refused(tmp_path):
    """A file with a NUL byte is refused, not returned as text."""
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00\x01\x02abc")

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["content"] == ""
    assert "binary" in result["error"].lower()
    assert to_result(result).is_error is True


def test_invalid_utf8_is_refused(tmp_path):
    """Text that is not valid UTF-8 is an error, not replacement garbage."""
    f = tmp_path / "latin.txt"
    f.write_bytes(b"caf\xe9\n")

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["content"] == ""
    assert "utf-8" in result["error"].lower()
    assert to_result(result).is_error is True


def test_utf8_bom_is_stripped(tmp_path):
    """A leading UTF-8 BOM is stripped and reported in the note."""
    f = tmp_path / "bom.txt"
    f.write_bytes(b"\xef\xbb\xbfhello\nworld\n")

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["content"] == "1: hello\n2: world"
    assert result["error"] == ""
    assert "BOM" in result["note"]


def test_default_limit_is_a_page(tmp_path):
    """The default read is one page (the ``limit`` default), not the whole file."""
    f = _write(tmp_path, 5000)
    result = read_file(str(f), project_root=str(tmp_path))

    assert result["line_end"] == 500
    assert result["truncated"] is True
    assert result["next_offset"] == 501


def test_server_budget_caps_a_large_read(tmp_path):
    """Even ``limit=None`` is bounded by the server-side character budget."""
    from klea_utils.mcp.tool_impls import read_file as read_file_module

    f = _write(tmp_path, 5000)
    result = read_file(str(f), limit=None, project_root=str(tmp_path))

    assert len(result["content"]) <= read_file_module._MAX_CHARS
    assert result["truncated"] is True
    assert result["truncation_reason"] == "char_budget"
    assert result["char_budget"] == read_file_module._MAX_CHARS
    assert result["next_offset"] is not None
    assert "server character budget" in result["note"]
    assert "limit=None" in result["note"]


def test_truncation_reason_line_limit(tmp_path):
    """A line-limited page reports why it stopped and the effective budget."""
    from klea_utils.mcp.tool_impls import read_file as read_file_module

    f = _write(tmp_path, 5000)
    result = read_file(str(f), limit=5, project_root=str(tmp_path))

    assert result["truncated"] is True
    assert result["truncation_reason"] == "line_limit"
    assert result["char_budget"] == read_file_module._MAX_CHARS


def test_truncation_reason_empty_when_complete(tmp_path):
    """A complete read reports an empty reason and the effective budget."""
    from klea_utils.mcp.tool_impls import read_file as read_file_module

    f = _write(tmp_path, 5)
    result = read_file(str(f), project_root=str(tmp_path))

    assert result["truncated"] is False
    assert result["truncation_reason"] == ""
    assert result["char_budget"] == read_file_module._MAX_CHARS


def test_limit_none_pages_to_eof_without_a_line_cap(tmp_path):
    """A ``limit=None`` read resumes by ``next_offset`` until it reaches EOF."""
    f = _write(tmp_path, 5000)
    offset = 1
    for _ in range(10):
        result = read_file(
            str(f), offset=offset, limit=None, project_root=str(tmp_path)
        )
        assert result["error"] == ""
        if not result["truncated"]:
            assert result["total_lines"] == 5000
            break
        assert result["truncation_reason"] == "char_budget"
        offset = result["next_offset"]
        assert offset is not None
    else:
        pytest.fail("limit=None paging did not reach EOF")


def test_tool_schema_hides_the_character_budget():
    """The model sees offset/limit/char_offset, not the server budget."""
    import inspect
    from typing import get_args

    from klea_utils.mcp.server import bundled_tools

    params = inspect.signature(bundled_tools.read_file).parameters
    assert "max_chars" not in params
    assert "char_offset" in params
    assert params["limit"].default == 500
    offset_field = get_args(params["offset"].annotation)[1]
    assert "Negative values" in offset_field.description
    assert "server character budget" in bundled_tools.read_file.__doc__
    assert "truncation_reason" in bundled_tools.read_file.__doc__


def test_file_larger_than_max_bytes_is_paged(tmp_path):
    """A file larger than max_bytes is paginated, not refused."""
    f = _write(tmp_path, 2000)

    result = read_file(str(f), limit=5, max_bytes=1024, project_root=str(tmp_path))

    assert result["error"] == ""
    assert result["content"].startswith("1: line 1")
    assert result["line_end"] == 5


def test_streamed_page_reports_unknown_total(tmp_path):
    """A page that stops before EOF reports ``total_lines`` as null."""
    f = _write(tmp_path, 5000)

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["truncated"] is True
    assert result["total_lines"] is None
    assert "of None" not in result["note"]


def test_streamed_read_to_eof_reports_total(tmp_path):
    """A small file read to EOF still reports its exact line count."""
    f = _write(tmp_path, 5)

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["total_lines"] == 5
    assert result["truncated"] is False


def test_mid_file_offset_pages_lazily(tmp_path):
    """A page in the middle of a large file reads the requested lines only."""
    f = _write(tmp_path, 2000)

    result = read_file(str(f), offset=1500, limit=3, project_root=str(tmp_path))

    assert result["content"] == "1500: line 1500\n1501: line 1501\n1502: line 1502"
    assert result["line_start"] == 1500
    assert result["line_end"] == 1502
    assert result["total_lines"] is None


def test_slice_segments_forward_returns_effective_offset():
    """A forward slice keeps the requested offset as the effective offset."""
    segments, total, effective = _slice_segments("a\nb\nc", 2, 1, 0)

    assert segments == ["b"]
    assert total == 3
    assert effective == 2


def test_slice_segments_tail_last_line():
    """Offset -1 returns the last line and a negative line number."""
    segments, total, effective = _slice_segments("a\nb\nc", -1, None, 0)

    assert segments == ["c"]
    assert total == 3
    assert effective == -1


def test_slice_segments_tail_last_n_lines():
    """A negative offset with a limit pages forward from the tail start."""
    segments, total, effective = _slice_segments("a\nb\nc\nd", -3, 2, 0)

    assert segments == ["b", "c"]
    assert total == 4
    assert effective == -3


def test_slice_segments_tail_clamps_to_start():
    """A tail offset beyond the start returns from the first line."""
    segments, total, effective = _slice_segments("a\nb\nc", -10, None, 0)

    assert segments == ["a", "b", "c"]
    assert total == 3
    assert effective == -3


def test_slice_segments_tail_empty_content():
    """An empty file has no tail line; the offset stays as requested."""
    segments, total, effective = _slice_segments("", -1, None, 0)

    assert segments == []
    assert total == 0
    assert effective == -1


def test_slice_segments_tail_char_offset():
    """A tail page can resume inside its first line."""
    segments, total, effective = _slice_segments("abc\nxyz", -1, None, 1)

    assert segments == ["yz"]
    assert total == 2
    assert effective == -1


def test_slice_segments_tail_trailing_newline():
    """A trailing newline does not add an empty tail line."""
    segments, total, effective = _slice_segments("a\nb\n", -1, None, 0)

    assert segments == ["b"]
    assert total == 2
    assert effective == -1


def test_slice_segments_tail_crlf():
    """CRLF content slices by logical lines in a tail read."""
    segments, total, effective = _slice_segments("a\r\nb\r\n", -1, None, 0)

    assert segments == ["b"]
    assert total == 2
    assert effective == -1


def test_slice_segments_tail_clamped_with_limit():
    """A clamped tail limited to two lines starts at the first line."""
    segments, total, effective = _slice_segments("a\nb\nc", -10, 2, 0)

    assert segments == ["a", "b"]
    assert total == 3
    assert effective == -3


def test_locate_tail_start_last_line_with_trailing_newline(tmp_path):
    """Trailing newlines are stripped before counting the tail."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n", encoding="utf-8", newline="\n")

    start = _locate_tail_start(f, 1, max_bytes=10_000)

    assert start.byte_offset == 4
    assert start.line_number == -1
    assert start.total_lines is None
    assert start.truncated is False


def test_locate_tail_start_last_two_lines(tmp_path):
    """The second-from-last line start is returned for n=2."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n")

    start = _locate_tail_start(f, 2, max_bytes=10_000)

    assert start.byte_offset == 2
    assert start.line_number == -2
    assert start.total_lines is None
    assert start.truncated is False


def test_locate_tail_start_reaches_the_file_start(tmp_path):
    """A tail request past the start clamps to line -total and reports total."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n")

    start = _locate_tail_start(f, 10, max_bytes=10_000)

    assert start.byte_offset == 0
    assert start.line_number == -3
    assert start.total_lines == 3
    assert start.truncated is False


def test_locate_tail_start_without_a_trailing_newline(tmp_path):
    """The last line is still the last line when the file has no final LF."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc")

    start = _locate_tail_start(f, 1, max_bytes=10_000)

    assert start.byte_offset == 4
    assert start.line_number == -1
    assert start.total_lines is None


def test_locate_tail_start_empty_file(tmp_path):
    """An empty file has no tail line."""
    f = tmp_path / "t.txt"
    f.write_text("")

    start = _locate_tail_start(f, 1, max_bytes=10_000)

    assert start.byte_offset == 0
    assert start.line_number is None
    assert start.total_lines == 0
    assert start.truncated is False


def test_locate_tail_start_only_a_newline(tmp_path):
    """A file containing just a newline has one empty line."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"\n")

    start = _locate_tail_start(f, 1, max_bytes=10_000)

    assert start.byte_offset == 0
    assert start.line_number == -1
    assert start.total_lines == 1


def test_locate_tail_start_crlf(tmp_path):
    """CRLF is one terminator, not two."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"a\r\nb\r\nc\r\n")

    start = _locate_tail_start(f, 1, max_bytes=10_000)
    assert start.byte_offset == 6
    assert start.line_number == -1

    start = _locate_tail_start(f, 2, max_bytes=10_000)
    assert start.byte_offset == 3
    assert start.line_number == -2

    start = _locate_tail_start(f, 3, max_bytes=10_000)
    assert start.byte_offset == 0
    assert start.total_lines == 3


def test_locate_tail_start_lone_cr(tmp_path):
    """A lone CR is a line terminator."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"a\rb\rc\r")

    start = _locate_tail_start(f, 1, max_bytes=10_000)
    assert start.byte_offset == 4
    assert start.line_number == -1

    start = _locate_tail_start(f, 2, max_bytes=10_000)
    assert start.byte_offset == 2
    assert start.line_number == -2

    start = _locate_tail_start(f, 3, max_bytes=10_000)
    assert start.byte_offset == 0
    assert start.total_lines == 3


def test_locate_tail_start_crlf_across_chunks(tmp_path):
    """A CRLF split across chunk boundaries is counted once."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"a\r\nb\r\nc\r\n")

    start = _locate_tail_start(f, 2, max_bytes=10_000, chunk_size=2)

    assert start.byte_offset == 3
    assert start.line_number == -2
    assert start.truncated is False


def test_locate_tail_start_is_safe_after_multibyte_text(tmp_path):
    """The located offset is a line boundary after multibyte characters."""
    f = tmp_path / "t.txt"
    f.write_bytes("caf\u00e9\nx\n".encode())

    start = _locate_tail_start(f, 1, max_bytes=10_000)

    assert start.byte_offset == len("caf\u00e9\n".encode())
    assert start.line_number == -1


def test_locate_tail_start_bom_does_not_affect_the_count(tmp_path):
    """A UTF-8 BOM is content for the backward scan."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"\xef\xbb\xbfx\n")

    start = _locate_tail_start(f, 1, max_bytes=10_000)

    assert start.byte_offset == 0
    assert start.line_number == -1
    assert start.total_lines == 1


def test_locate_tail_start_scan_cap(tmp_path):
    """The backward scan is bounded by max_bytes."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\nd\n")

    start = _locate_tail_start(f, 3, max_bytes=2)

    assert start.byte_offset is None
    assert start.line_number is None
    assert start.total_lines is None
    assert start.truncated is True


def test_read_tail_page_last_line(tmp_path):
    """A tail page starts at the located line with a signed line number."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n")

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["c"]
    assert page.line_number == -1
    assert page.total_lines is None
    assert page.reached_eof is True
    assert page.stop_reason == "eof"
    assert page.locator_truncated is False


def test_read_tail_page_last_two_lines(tmp_path):
    """A tail page includes the lines forward from the located start."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n")

    page = _read_tail_page(
        f, offset=-2, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["b", "c"]
    assert page.line_number == -2
    assert page.reached_eof is True


def test_read_tail_page_limit_stops_before_eof(tmp_path):
    """A tail page honours the line limit and reports a continuation."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n")

    page = _read_tail_page(
        f, offset=-3, limit=2, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["a", "b"]
    assert page.line_number == -3
    assert page.total_lines == 3
    assert page.reached_eof is False
    assert page.stop_reason == "limit"


def test_read_tail_page_clamps_to_the_file_start(tmp_path):
    """A tail request past the start reports the exact total."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\n")

    page = _read_tail_page(
        f, offset=-10, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["a", "b", "c"]
    assert page.line_number == -3
    assert page.total_lines == 3
    assert page.reached_eof is True


def test_read_tail_page_empty_file(tmp_path):
    """An empty file produces an empty page with a zero total."""
    f = tmp_path / "t.txt"
    f.write_text("")

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == []
    assert page.line_number is None
    assert page.total_lines == 0
    assert page.reached_eof is True
    assert page.locator_truncated is False


def test_read_tail_page_only_a_newline(tmp_path):
    """A file with just a newline has one empty tail line."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"\n")

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == [""]
    assert page.line_number == -1
    assert page.total_lines == 1
    assert page.reached_eof is True


def test_read_tail_page_applies_char_offset(tmp_path):
    """char_offset resumes inside the tail start line."""
    f = tmp_path / "t.txt"
    f.write_text("abc\nxyz")

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=1, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["yz"]
    assert page.line_number == -1


def test_read_tail_page_strips_a_bom_at_the_file_start(tmp_path):
    """A tail page that reaches byte 0 strips a UTF-8 BOM."""
    f = tmp_path / "t.txt"
    f.write_bytes(b"\xef\xbb\xbfx\n")

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["x"]
    assert page.had_bom is True
    assert page.total_lines == 1


def test_read_tail_page_preserves_a_mid_file_bom(tmp_path):
    """A U+FEFF at a mid-file tail line is content, not a BOM."""
    f = tmp_path / "t.txt"
    f.write_text("x\n\ufeffy\n")

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )

    assert page.segments == ["\ufeffy"]
    assert page.had_bom is False


def test_read_tail_page_crlf_and_lone_cr(tmp_path):
    """CRLF and lone-CR files both page by logical line."""
    crlf = tmp_path / "crlf.txt"
    crlf.write_bytes(b"a\r\nb\r\nc\r\n")
    page = _read_tail_page(
        crlf, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )
    assert page.segments == ["c"]
    assert page.line_number == -1

    cr = tmp_path / "cr.txt"
    cr.write_bytes(b"a\rb\rc\r")
    page = _read_tail_page(
        cr, offset=-1, limit=None, char_offset=0, max_chars=10_000, max_bytes=10_000
    )
    assert page.segments == ["c"]
    assert page.line_number == -1


def test_read_tail_page_locator_scan_cap(tmp_path):
    """The locator cap produces an empty, truncated tail page."""
    f = tmp_path / "t.txt"
    f.write_text("a\nb\nc\nd\n")

    page = _read_tail_page(
        f, offset=-3, limit=None, char_offset=0, max_chars=10_000, max_bytes=2
    )

    assert page.segments == []
    assert page.line_number is None
    assert page.total_lines is None
    assert page.scan_capped is True
    assert page.stop_reason == "cap"
    assert page.locator_truncated is True


def test_read_tail_page_reports_a_char_stop(tmp_path):
    """A long tail line stops the collector on the character budget."""
    f = tmp_path / "t.txt"
    f.write_text("ok\n" + "x" * 200)

    page = _read_tail_page(
        f, offset=-1, limit=None, char_offset=0, max_chars=5, max_bytes=10_000
    )

    assert page.line_number == -1
    assert page.stop_reason == "chars"
    assert page.reached_eof is True
    assert page.segments
    assert len(page.segments[0]) > 5


def test_tail_offset_last_line(tmp_path):
    """A -1 offset reads the last line with a negative line number."""
    f = _write(tmp_path, 5)

    result = read_file(str(f), offset=-1, project_root=str(tmp_path))

    assert result["content"] == "-1: line 5"
    assert result["line_start"] == -1
    assert result["line_end"] == -1
    assert result["total_lines"] is None
    assert result["truncated"] is False
    assert result["truncation_reason"] == ""
    assert result["next_offset"] is None


def test_tail_offset_last_n_lines(tmp_path):
    """A negative offset returns the last N lines to EOF."""
    f = _write(tmp_path, 5)

    result = read_file(str(f), offset=-3, project_root=str(tmp_path))

    assert result["content"] == "-3: line 3\n-2: line 4\n-1: line 5"
    assert result["line_start"] == -3
    assert result["line_end"] == -1
    assert result["truncated"] is False
    assert result["next_offset"] is None


def test_tail_offset_pages_forward(tmp_path):
    """A tail page continues with a negative next_offset."""
    f = _write(tmp_path, 10)

    first = read_file(str(f), offset=-10, limit=4, project_root=str(tmp_path))
    assert first["line_start"] == -10
    assert first["line_end"] == -7
    assert first["truncated"] is True
    assert first["next_offset"] == -6
    assert "offset=-6" in first["note"]

    second = read_file(str(f), offset=-6, limit=4, project_root=str(tmp_path))
    assert second["line_start"] == -6
    assert second["line_end"] == -3
    assert second["next_offset"] == -2

    third = read_file(str(f), offset=-2, limit=4, project_root=str(tmp_path))
    assert third["line_end"] == -1
    assert third["truncated"] is False
    assert third["next_offset"] is None


def test_tail_offset_clamps_to_the_file_start(tmp_path):
    """A tail request past the start clamps and reports the exact total."""
    f = _write(tmp_path, 3)

    result = read_file(str(f), offset=-10, project_root=str(tmp_path))

    assert result["content"] == "-3: line 1\n-2: line 2\n-1: line 3"
    assert result["line_start"] == -3
    assert result["line_end"] == -1
    assert result["total_lines"] == 3
    assert result["truncated"] is False
    assert "before the start" in result["note"]


def test_tail_offset_empty_file(tmp_path):
    """A tail read of an empty file reports zero lines and a note."""
    f = tmp_path / "empty.txt"
    f.write_text("")

    result = read_file(str(f), offset=-1, project_root=str(tmp_path))

    assert result["content"] == ""
    assert result["total_lines"] == 0
    assert result["line_start"] == -1
    assert result["line_end"] == -2
    assert result["truncated"] is False
    assert "past the start" in result["note"]


def test_tail_offset_scan_cap(tmp_path):
    """A tail locator that exceeds the scan cap returns a note, not a file error."""
    f = _write(tmp_path, 10)

    result = read_file(str(f), offset=-5, max_bytes=2, project_root=str(tmp_path))

    assert result["content"] == ""
    assert result["truncated"] is True
    assert result["truncation_reason"] == "scan_cap"
    assert result["line_start"] == -5
    assert result["line_end"] == -6
    assert result["next_offset"] is None
    assert "tail scan cap" in result["note"]


def test_tail_offset_char_paging(tmp_path):
    """A long last line is paged by characters from the tail."""
    f = tmp_path / "long.txt"
    f.write_text("a\n" + "x" * 30)

    first = read_file(
        str(f),
        offset=-1,
        max_chars=10,
        line_numbers=False,
        project_root=str(tmp_path),
    )
    assert first["content"] == "x" * 10
    assert first["line_start"] == -1
    assert first["line_end"] == -1
    assert first["truncated"] is True
    assert first["truncation_reason"] == "char_budget"
    assert first["next_offset"] == -1
    assert first["next_char_offset"] == 10

    second = read_file(
        str(f),
        offset=-1,
        char_offset=first["next_char_offset"],
        max_chars=10,
        line_numbers=False,
        project_root=str(tmp_path),
    )
    assert second["content"] == "x" * 10
    assert second["line_start"] == -1
    assert second["next_char_offset"] == 20


def test_tail_offset_preserves_bom_at_start(tmp_path):
    """A tail that reaches byte 0 strips the BOM and reports the total."""
    f = tmp_path / "bom.txt"
    f.write_bytes(b"\xef\xbb\xbfhello\nworld\n")

    result = read_file(str(f), offset=-10, project_root=str(tmp_path))

    assert result["content"] == "-2: hello\n-1: world"
    assert result["line_start"] == -2
    assert result["line_end"] == -1
    assert result["total_lines"] == 2
    assert "BOM" in result["note"]
    assert "before the start" in result["note"]


def test_tail_offset_bom_only_file(tmp_path):
    """A file containing only a BOM has no lines after stripping."""
    f = tmp_path / "bom.txt"
    f.write_bytes(b"\xef\xbb\xbf")

    result = read_file(str(f), offset=-1, project_root=str(tmp_path))

    assert result["content"] == ""
    assert result["total_lines"] == 0
    assert result["line_start"] == -1
    assert result["line_end"] == -2
    assert result["truncated"] is False
    assert "past the start" in result["note"]
    assert "BOM" in result["note"]


def test_tail_offset_html(tmp_path):
    """Converted content is sliced from the end with its exact total."""
    f = tmp_path / "page.html"
    f.write_text("<html><body><h1>Title</h1><p>Body</p></body></html>")

    result = read_file(str(f), offset=-1, project_root=str(tmp_path))

    assert result["content"] == "-1: Body"
    assert result["line_start"] == -1
    assert result["line_end"] == -1
    assert result["total_lines"] == 2
    assert result["truncated"] is False


def test_tail_offset_crlf(tmp_path):
    """CRLF content pages by logical line in a tail read."""
    f = tmp_path / "crlf.txt"
    f.write_bytes(b"a\r\nb\r\n")

    result = read_file(str(f), offset=-1, project_root=str(tmp_path))

    assert result["content"] == "-1: b"
    assert result["line_end"] == -1
    assert result["truncated"] is False


def test_tail_offset_honours_line_numbers_false(tmp_path):
    """A tail read returns raw text when line numbers are disabled."""
    f = _write(tmp_path, 5)

    result = read_file(
        str(f), offset=-1, line_numbers=False, project_root=str(tmp_path)
    )

    assert result["content"] == "line 5"
    assert result["line_start"] == -1


def test_offset_zero_still_starts_at_the_first_line(tmp_path):
    """Offset 0 keeps the legacy clamp to the first line."""
    f = _write(tmp_path, 3)

    result = read_file(str(f), offset=0, project_root=str(tmp_path))

    assert result["line_start"] == 1
    assert result["content"].startswith("1: line 1")


def test_scan_cap_before_offset_gives_grep_hint(tmp_path):
    """The per-call scan cap stops a deep read and points to grep."""
    f = _write(tmp_path, 5000)

    result = read_file(
        str(f), offset=4000, limit=5, max_bytes=1024, project_root=str(tmp_path)
    )

    assert result["content"] == ""
    assert result["truncated"] is True
    assert result["truncation_reason"] == "scan_cap"
    assert "grep" in result["note"]


def test_large_file_truncated_page_has_grep_hint(tmp_path):
    """A truncated page of a large file suggests grep-then-read."""
    f = tmp_path / "big.txt"
    f.write_text("\n".join("y" * 100 for _ in range(20000)))

    result = read_file(str(f), project_root=str(tmp_path))

    assert result["truncated"] is True
    assert "grep" in result["note"]


def test_stream_reader_splits_crlf_across_chunks():
    """A CRLF straddling a chunk boundary is normalised, not split."""
    import io

    from klea_utils.mcp.tool_impls.read_file import (
        _iter_capped_lines,
        _StreamState,
    )

    state = _StreamState()
    lines = list(
        _iter_capped_lines(
            io.BytesIO(b"ab\r\ncd\n"),
            state,
            max_line_chars=10,
            max_bytes=10_000,
            chunk_size=3,
        )
    )

    assert [text for text, _ in lines] == ["ab", "cd"]
    assert state.exhausted is True


def test_stream_reader_splits_multibyte_across_chunks():
    """A UTF-8 character split across a chunk boundary decodes correctly."""
    import io

    from klea_utils.mcp.tool_impls.read_file import (
        _iter_capped_lines,
        _StreamState,
    )

    state = _StreamState()
    lines = list(
        _iter_capped_lines(
            io.BytesIO("caf\u00e9\n".encode()),
            state,
            max_line_chars=10,
            max_bytes=10_000,
            chunk_size=1,
        )
    )

    assert [text for text, _ in lines] == ["caf\u00e9"]
    assert state.exhausted is True


def test_stream_reader_can_preserve_a_bom_after_a_seek():
    """A mid-file seek treats a leading U+FEFF as content, not a BOM."""
    import io

    from klea_utils.mcp.tool_impls.read_file import (
        _iter_capped_lines,
        _StreamState,
    )

    state = _StreamState()
    lines = list(
        _iter_capped_lines(
            io.BytesIO(b"\xef\xbb\xbfx\n"),
            state,
            max_line_chars=10,
            max_bytes=10_000,
            strip_bom=False,
        )
    )

    assert [text for text, _ in lines] == ["\ufeffx"]
    assert state.had_bom is False
    assert state.exhausted is True


def test_stream_reader_caps_an_over_long_line():
    """A line beyond the cap is truncated and flagged, bounding memory."""
    import io

    from klea_utils.mcp.tool_impls.read_file import (
        _iter_capped_lines,
        _StreamState,
    )

    state = _StreamState()
    lines = list(
        _iter_capped_lines(
            io.BytesIO(b"x" * 100 + b"\n"),
            state,
            max_line_chars=10,
            max_bytes=10_000,
            chunk_size=5,
        )
    )

    assert lines == [("x" * 10, True)]
    assert state.exhausted is True
