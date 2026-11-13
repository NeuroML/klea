#!/usr/bin/env python3
"""Unit tests for the shared per-line character cap helpers.

File: utils_pkg/tests/test_mcp_text_limits.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from klea_utils.mcp.tool_impls.text_limits import (
    DEFAULT_MAX_LINE_CHARS,
    LINE_TRUNCATION_MARKER,
    cap_line,
    cap_lines,
)


def test_defaults_are_sane():
    """The module exposes the documented default and marker template."""
    assert DEFAULT_MAX_LINE_CHARS == 2_000
    assert "{omitted}" in LINE_TRUNCATION_MARKER


def test_cap_line_shortens_oversized_line_with_marker():
    """A 10k-character line is cut to max_chars plus a visible marker."""
    line = "a" * 10_000

    capped, was_capped = cap_line(line, 100)

    assert was_capped is True
    assert capped.startswith("a" * 100)
    assert "[line truncated:" in capped
    assert "9900 chars omitted" in capped
    # Only the kept text plus the marker is present (no line terminator here).
    assert capped == "a" * 100 + " ... [line truncated: 9900 chars omitted]"


def test_cap_line_passes_short_line_through():
    """A line within the cap is returned unchanged and not flagged."""
    line = "short line"

    capped, was_capped = cap_line(line, 100)

    assert capped == line
    assert was_capped is False


def test_cap_lines_passes_short_text_through():
    """Text whose lines all fit is returned unchanged with a zero count."""
    text = "short\ntext"

    capped, capped_count = cap_lines(text, 100)

    assert capped == text
    assert capped_count == 0


def test_cap_lines_caps_per_line_not_whole_block():
    """Each over-long line is capped independently, not the block as a whole."""
    text = "a" * 300 + "\n" + "b" * 300

    capped, capped_count = cap_lines(text, 50)

    assert capped_count == 2
    assert capped.count("[line truncated:") == 2
    first, second = capped.split("\n")
    assert first.startswith("a" * 50)
    assert second.startswith("b" * 50)
    assert "250 chars omitted" in first
    assert "250 chars omitted" in second


def test_cap_line_preserves_trailing_newline():
    """The line terminator is preserved and not counted against max_chars."""
    line = "x" * 200 + "\n"

    capped, was_capped = cap_line(line, 50)

    assert was_capped is True
    assert capped.endswith("\n")
    assert capped.count("\n") == 1
    assert capped.startswith("x" * 50)
    assert "150 chars omitted" in capped


def test_cap_lines_preserves_trailing_newline():
    """A block's trailing newline survives capping of its over-long line."""
    text = "y" * 200 + "\n"

    capped, capped_count = cap_lines(text, 50)

    assert capped_count == 1
    assert capped.endswith("\n")
    assert capped.count("\n") == 1
    assert "150 chars omitted" in capped
