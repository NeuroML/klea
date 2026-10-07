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


def test_single_over_long_line_cannot_page(tmp_path):
    """A line longer than the cap is clipped and reported as unpaginable."""
    f = tmp_path / "big.txt"
    f.write_text("x" * 500 + "\nsecond\n")
    result = read_file(
        str(f), max_chars=100, project_root=str(tmp_path), line_numbers=False
    )

    assert result["truncated"] is True
    assert len(result["content"]) <= 100
    assert result["next_offset"] is None
    assert result["note"] != ""


def test_missing_file_is_still_an_error(tmp_path):
    """The missing-file case remains an error (with a note), unchanged."""
    result = read_file(str(tmp_path / "nope.txt"), project_root=str(tmp_path))

    assert result["error"] != ""
    assert to_result(result).is_error is True
