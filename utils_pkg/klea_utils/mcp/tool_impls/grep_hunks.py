#!/usr/bin/env python3
"""
Shared helpers for building grep context hunks.

Both grep backends return the same hunk shape: a contiguous group of lines
around one or more matches, with explicit line numbers and a ``match`` flag
per line.  Keeping the grouping and character-budget logic here ensures the
in-house walker and ripgrep produce identical output.

File: klea_utils/mcp/tool_impls/grep_hunks.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from collections.abc import Callable, Iterable
from typing import Any

logger = logging.getLogger(__name__)

#: Default number of context lines shown before and after each match.
DEFAULT_CONTEXT_LINES = 2

#: Hard upper bound on context lines per side of a match.
MAX_CONTEXT_LINES = 7


def clamp_context_lines(context_lines: int) -> int:
    """Clamp *context_lines* to the supported range.

    :param context_lines: Requested lines of context on each side of a match.
    :returns: The value clamped to ``0..MAX_CONTEXT_LINES``.
    """
    if context_lines < 0:
        logger.warning(f"Negative context_lines {context_lines}; using 0")
        return 0
    if context_lines > MAX_CONTEXT_LINES:
        logger.warning(
            f"context_lines {context_lines} exceeds {MAX_CONTEXT_LINES}; "
            f"using {MAX_CONTEXT_LINES}"
        )
        return MAX_CONTEXT_LINES
    return context_lines


def merge_windows(
    match_lines: Iterable[int],
    total_lines: int,
    context_lines: int,
) -> list[tuple[int, int]]:
    """Merge context windows around *match_lines* into contiguous intervals.

    :param match_lines: Sorted 1-based line numbers of matches.
    :param total_lines: Number of lines in the file, used to clamp the end of
        the last window.
    :param context_lines: Lines of context on each side of a match.
    :returns: Sorted ``(start_line, end_line)`` intervals with overlapping or
        adjacent windows merged.
    """
    windows: list[list[int]] = []
    for line_number in match_lines:
        start = max(1, line_number - context_lines)
        end = min(total_lines, line_number + context_lines)
        if windows and start <= windows[-1][1] + 1:
            windows[-1][1] = max(windows[-1][1], end)
        else:
            windows.append([start, end])
    return [(start, end) for start, end in windows]


def build_hunk(
    path: str,
    start_line: int,
    end_line: int,
    get_line: Callable[[int], str | None],
    match_lines: set[int],
    *,
    chars_used: int,
    max_chars: int,
) -> tuple[dict[str, Any] | None, int, bool]:
    """Materialise one hunk, respecting the total character budget.

    :param path: Path reported for the hunk.
    :param start_line: First 1-based line number in the hunk.
    :param end_line: Last 1-based line number in the hunk.
    :param get_line: Callable returning the text of a 1-based line, or ``None``
        when the line is unavailable.
    :param match_lines: 1-based line numbers that are matches.
    :param chars_used: Characters already spent on earlier hunks.
    :param max_chars: Total character budget for the whole result.
    :returns: ``(hunk, chars_used, truncated)``.  The hunk is ``None`` when
        the budget was already exhausted before its first line; *truncated*
        is ``True`` when the budget cut the hunk short.
    """
    lines: list[dict[str, Any]] = []
    last_line = start_line - 1
    truncated = False
    for line_number in range(start_line, end_line + 1):
        text = get_line(line_number)
        if text is None:
            continue
        remaining = max_chars - chars_used
        if remaining <= 0:
            truncated = True
            break
        if len(text) > remaining:
            text = text[:remaining]
            truncated = True
        lines.append(
            {
                "line_number": line_number,
                "line": text,
                "match": line_number in match_lines,
            }
        )
        chars_used += len(text)
        last_line = line_number
        if truncated:
            break

    if not lines:
        return None, chars_used, truncated
    hunk = {
        "path": path,
        "start_line": start_line,
        "end_line": last_line,
        "lines": lines,
    }
    return hunk, chars_used, truncated
