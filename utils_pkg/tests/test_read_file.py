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
from klea_utils.mcp.tool_impls.read_file import read_file
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
    assert result["next_offset"] is not None


def test_tool_schema_hides_the_character_budget():
    """The model sees offset/limit/char_offset, not the server budget."""
    import inspect

    from klea_utils.mcp.server import bundled_tools

    params = inspect.signature(bundled_tools.read_file).parameters
    assert "max_chars" not in params
    assert "char_offset" in params
    assert params["limit"].default == 500


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


def test_scan_cap_before_offset_gives_grep_hint(tmp_path):
    """The per-call scan cap stops a deep read and points to grep."""
    f = _write(tmp_path, 5000)

    result = read_file(
        str(f), offset=4000, limit=5, max_bytes=1024, project_root=str(tmp_path)
    )

    assert result["content"] == ""
    assert result["truncated"] is True
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
