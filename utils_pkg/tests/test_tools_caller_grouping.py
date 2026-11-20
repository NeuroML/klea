"""Tests for the grouped tool-round status lines.

File: tests/test_tools_caller_grouping.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.nodes.tools_caller import _grouped_tool_lines


def _record(tool: str, title: str, *, ok: bool = True) -> str:
    """Build one per-call record in the emitter's ``tool\\ttitle\\toutcome`` form.

    :param tool: The raw tool name (the grouping key).
    :param title: The displayed label for the tool.
    :param ok: Whether the call succeeded.
    :returns: The record string the node collects before grouping.
    """
    return f"{tool}\t{title}\t{'ok' if ok else 'failed'}"


def test_grouped_lines_aggregate_repeated_tool() -> None:
    """Repeated calls of one tool collapse into a single counted line."""
    records = [
        _record("read_file", "Read file"),
        _record("read_file", "Read file"),
        _record("read_file", "Read file", ok=False),
    ]
    assert _grouped_tool_lines(records) == ["- **Read file**: ok x2, failed x1"]


def test_grouped_lines_keep_first_seen_order() -> None:
    """Tools appear in the order their first call ran, not alphabetically."""
    records = [
        _record("run_command", "Run command"),
        _record("read_file", "Read file"),
        _record("run_command", "Run command", ok=False),
        _record("read_file", "Read file"),
        _record("read_file", "Read file"),
    ]
    assert _grouped_tool_lines(records) == [
        "- **Run command**: ok x1, failed x1",
        "- **Read file**: ok x3",
    ]


def test_grouped_lines_omit_zero_counts() -> None:
    """A segment whose count is zero is omitted, never rendered as ``x0``."""
    records = [
        _record("edit_file", "Edit file"),
        _record("edit_file", "Edit file"),
        _record("grep", "Search", ok=False),
    ]
    lines = _grouped_tool_lines(records)
    assert lines == ["- **Edit file**: ok x2", "- **Search**: failed x1"]
    assert "x0" not in "\n".join(lines)


def test_grouped_lines_key_on_raw_tool_name() -> None:
    """Two tools sharing a displayed title stay separate lines."""
    records = [
        _record("read_file", "File"),
        _record("write_file", "File"),
    ]
    assert _grouped_tool_lines(records) == [
        "- **File**: ok x1",
        "- **File**: ok x1",
    ]


def test_grouped_lines_empty_without_calls() -> None:
    """No dispatched call yields no lines, so the section is not emitted."""
    assert _grouped_tool_lines([]) == []
