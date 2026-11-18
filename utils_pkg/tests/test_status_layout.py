#!/usr/bin/env python3
"""Unit tests for the shared status-section height budget.

The helper is frontend-neutral (it imports neither ``textual`` nor
``nicegui``): it counts the lines a renderer would produce and turns that
count into a banded height budget.  These tests pin the floor and ceiling,
the band quantisation, and both counting paths (``content`` blocks and a
preformatted ``display``), using minimal duck-typed section dicts that mirror
the stored ``StateSection`` shape.

File: tests/test_status_layout.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_utils.ui.status_layout import (
    BAND_LINES,
    MAX_LINES,
    MIN_LINES,
    estimate_section_lines,
    section_budget_lines,
)


def _display(text: str, *, preformatted: bool = False) -> dict:
    """Build a minimal display-only section.

    :param text: The section's ``display`` text.
    :param preformatted: Whether to stamp the ``preformatted`` flag.
    :returns: The section dict.
    """
    section: dict = {"display": text}
    if preformatted:
        section["preformatted"] = True
    return section


def _display_lines(count: int) -> dict:
    """Build a display section with exactly *count* logical lines.

    :param count: The number of newline-separated lines.
    :returns: The section dict.
    """
    return _display("\n".join(f"line {index}" for index in range(count)))


def _items(count: int, *, layout: str = "spaced") -> dict:
    """Build an ``items`` content section with *count* non-empty items.

    :param count: The number of item entries.
    :param layout: The block's ``layout`` hint.
    :returns: The section dict.
    """
    return {
        "content": [
            {
                "type": "items",
                "items": [{"text": f"item {index}"} for index in range(count)],
                "layout": layout,
            }
        ]
    }


def test_empty_section_gets_the_floor_budget() -> None:
    """An empty section estimates no lines and takes the floor."""
    assert estimate_section_lines({}) == 0
    assert section_budget_lines({}) == MIN_LINES


def test_a_single_rendered_line_gets_the_floor_budget() -> None:
    """One rendered line still sits on the floor (0-1 lines -> 5)."""
    section = _display("Goal: one line")
    assert estimate_section_lines(section) == 1
    assert section_budget_lines(section) == MIN_LINES


def test_mid_sized_body_lands_on_its_band() -> None:
    """A mid-sized body is quantised up to its band, not clamped."""
    section = _display_lines(8)
    assert estimate_section_lines(section) == 8
    assert section_budget_lines(section) == 10


def test_very_long_items_body_is_capped_at_the_ceiling() -> None:
    """A long fan-out body cannot exceed the ceiling."""
    section = _items(60)
    assert estimate_section_lines(section) > MAX_LINES
    assert section_budget_lines(section) == MAX_LINES


def test_very_long_display_is_capped_at_the_ceiling() -> None:
    """A long plan body cannot exceed the ceiling either."""
    section = _display_lines(200)
    assert estimate_section_lines(section) == 200
    assert section_budget_lines(section) == MAX_LINES


def test_two_counts_in_one_band_share_a_budget() -> None:
    """Streaming growth within a band does not resize the section."""
    shorter = _display_lines(6)
    longer = _display_lines(9)
    assert estimate_section_lines(shorter) == 6
    assert estimate_section_lines(longer) == 9
    assert section_budget_lines(shorter) == section_budget_lines(longer) == 10


@pytest.mark.parametrize("count", [0, 1, 4, 5, 9, 10, 14, 20, 25, 40])
def test_budget_is_always_a_banded_value_within_range(count: int) -> None:
    """Every budget is clamped and a whole number of bands."""
    budget = section_budget_lines(_display_lines(count))
    assert MIN_LINES <= budget <= MAX_LINES
    assert budget % BAND_LINES == 0


def test_preformatted_display_counts_per_logical_line() -> None:
    """A preformatted (aligned plan) display is counted line by line."""
    text = "Phase 1  done\nPhase 2  running\nPhase 3  pending"
    section = _display(text, preformatted=True)
    assert estimate_section_lines(section) == 3
    assert section_budget_lines(section) == MIN_LINES


def test_preformatted_flag_does_not_change_the_count() -> None:
    """The count is logical lines either way; only rendering differs."""
    text = "a\nb\nc\nd"
    assert (
        estimate_section_lines(_display(text))
        == estimate_section_lines(_display(text, preformatted=True))
        == 4
    )


def test_items_block_counts_items_and_their_spacers() -> None:
    """A spaced items block counts one line per gap between items."""
    assert estimate_section_lines(_items(3)) == 5  # 3 items + 2 blank spacers


def test_compact_items_block_drops_the_spacers() -> None:
    """The compact layout adds no blank lines between items."""
    assert estimate_section_lines(_items(3, layout="compact")) == 3


def test_empty_items_are_not_counted() -> None:
    """Blank entries are dropped, matching the renderers."""
    section = {
        "content": [{"type": "items", "items": [{"text": "a"}, {"text": ""}, "b"]}]
    }
    assert estimate_section_lines(section) == 3  # 2 items + 1 spacer


def test_fields_block_counts_one_line_per_field() -> None:
    """A fields block counts one line for each labelled value."""
    section = {"content": [{"type": "fields", "fields": {"A": 1, "B": 2}}]}
    assert estimate_section_lines(section) == 2


def test_text_block_counts_one_line_only_when_non_empty() -> None:
    """A text block renders one line unless it is empty."""
    assert estimate_section_lines({"content": [{"type": "text", "text": "hello"}]}) == 1
    assert estimate_section_lines({"content": [{"type": "text", "text": ""}]}) == 0


def test_unknown_kind_degrades_to_its_text() -> None:
    """An unknown block kind is counted by its plain text, not dropped."""
    section = {"content": [{"type": "mystery", "text": "still rendered"}]}
    assert estimate_section_lines(section) == 1


def test_non_mapping_block_counts_one_line_when_truthy() -> None:
    """A bare (non-mapping) block is one line when truthy, else none."""
    assert estimate_section_lines({"content": ["plain block"]}) == 1
    assert estimate_section_lines({"content": [""]}) == 0


def test_bare_content_block_is_wrapped() -> None:
    """A single mapping in ``content`` is treated as a one-block list."""
    section = {"content": {"type": "text", "text": "single block"}}
    assert estimate_section_lines(section) == 1


def test_summary_is_counted_when_no_content_or_display() -> None:
    """The fallback path counts the summary's logical lines."""
    section = {"summary": "a\nb"}
    assert estimate_section_lines(section) == 2


def test_non_mapping_section_estimates_zero() -> None:
    """A malformed section degrades to zero lines and the floor budget."""
    assert estimate_section_lines(None) == 0
    assert section_budget_lines(None) == MIN_LINES
