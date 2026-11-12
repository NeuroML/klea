#!/usr/bin/env python3
"""
Renderer-level tests for the TUI status panel content blocks.

These exercise the module-level helpers that turn a streamed section's
``content`` blocks into styled lines / widgets, independent of a running
Textual app, and pin the graceful degradation behaviour: unknown kinds,
unknown roles and malformed blocks must render rather than raise.

File: tests/ui/tui/test_status_panel_renderer.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from klea_utils.ui.glyphs import ASCII_GLYPHS, UNICODE_GLYPHS
from klea_utils.ui.tui.components import status_panel
from klea_utils.ui.tui.components.status_panel import (
    _ROLE_STYLES,
    _block_lines,
    _content_body,
)


@pytest.fixture(autouse=True)
def _unicode_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the panel to the Unicode glyph table for deterministic markers."""

    monkeypatch.setattr(
        status_panel, "get_glyphs", lambda *args, **kwargs: UNICODE_GLYPHS
    )


def test_items_block_renders_one_line_per_item_with_roles():
    """An ``items`` block renders one marked, styled line per item."""
    block = {
        "type": "items",
        "items": [
            {"text": "ok item", "role": "ok"},
            {"text": "error item", "role": "error"},
            {"text": "current item", "role": "current"},
            {"text": "pending item", "role": "pending"},
        ],
    }
    assert _block_lines(block) == [
        f"[green]{_escaped(UNICODE_GLYPHS.marker_ok)} ok item[/green]",
        " ",
        f"[red]{_escaped(UNICODE_GLYPHS.marker_error)} error item[/red]",
        " ",
        f"[bold]{_escaped(UNICODE_GLYPHS.marker_current)} current item[/bold]",
        " ",
        f"[dim]{_escaped(UNICODE_GLYPHS.marker_pending)} pending item[/dim]",
    ]


def test_role_styles_cover_the_known_roles():
    """The four known roles map to their tints."""
    assert _ROLE_STYLES["ok"] == "green"
    assert _ROLE_STYLES["error"] == "red"
    assert _ROLE_STYLES["current"] == "bold"
    assert _ROLE_STYLES["pending"] == "dim"


def test_unknown_item_role_renders_plain_without_raising():
    """An unknown role degrades to unstyled text instead of raising."""
    lines = _block_lines(
        {"type": "items", "items": [{"text": "plain", "role": "mystery"}]}
    )
    # A single item has no separator before or after it.
    assert lines == [f"{_escaped(UNICODE_GLYPHS.marker_plain)} plain"]


def test_unknown_block_kind_degrades_to_text_without_raising():
    """An unknown block kind falls back to its ``text`` field."""
    assert _block_lines({"type": "mystery", "text": "hello"}) == ["hello"]


def test_malformed_block_does_not_raise():
    """``None`` yields no lines; a non-mapping degrades to its ``str``."""
    assert _block_lines(None) == []
    assert _block_lines("free text") == ["free text"]


def test_high_content_version_is_never_consulted():
    """A high ``content_version`` does not cause the block to be dropped."""
    block = {
        "type": "items",
        "content_version": 10_000_000,
        "items": [{"text": "kept", "role": "ok"}],
    }
    # A single item has no separator before or after it.
    assert _block_lines(block) == [
        f"[green]{_escaped(UNICODE_GLYPHS.marker_ok)} kept[/green]"
    ]


def test_items_are_separated_with_a_blank_line_without_a_trailing_separator() -> None:
    """Consecutive items are separated by one blank line; the last has none."""

    block = {
        "type": "items",
        "items": [
            {"text": "first", "role": "ok"},
            {"text": "second", "role": "ok"},
        ],
    }
    assert _block_lines(block) == [
        f"[green]{_escaped(UNICODE_GLYPHS.marker_ok)} first[/green]",
        " ",
        f"[green]{_escaped(UNICODE_GLYPHS.marker_ok)} second[/green]",
    ]


def test_content_body_renders_blocks_without_an_app():
    """``_content_body`` builds one widget per rendered line, no app needed."""
    widgets = _content_body(
        [
            {"type": "items", "items": [{"text": "one"}, {"text": "two"}]},
            {"type": "mystery", "text": "three"},
            {"type": "text", "text": "plain", "role": "mystery"},
        ]
    )
    # Two items render as item, spacer line, item (the inter-item blank
    # line), then the mystery line and the plain line: five widgets.
    assert len(widgets) == 5


def _escaped(text: str) -> str:
    """Escape *text* exactly as the renderer escapes Rich markup."""

    return text.replace("\\", "\\\\").replace("[", "\\[")


def test_ascii_markers_are_rendered_when_the_ascii_table_is_selected(monkeypatch):
    """Forced-ASCII rendering prefixes each role with its ASCII marker."""
    monkeypatch.setattr(
        status_panel, "get_glyphs", lambda *args, **kwargs: ASCII_GLYPHS
    )
    assert [
        ASCII_GLYPHS.marker_ok,
        ASCII_GLYPHS.marker_error,
        ASCII_GLYPHS.marker_current,
        ASCII_GLYPHS.marker_pending,
        ASCII_GLYPHS.marker_plain,
    ] == ["[x]", "[!]", "[*]", "[ ]", "[-]"]
    block = {
        "type": "items",
        "items": [
            {"text": "ok item", "role": "ok"},
            {"text": "error item", "role": "error"},
            {"text": "current item", "role": "current"},
            {"text": "pending item", "role": "pending"},
            {"text": "plain item", "role": "mystery"},
        ],
    }
    assert _block_lines(block) == [
        f"[green]{_escaped(ASCII_GLYPHS.marker_ok)} ok item[/green]",
        " ",
        f"[red]{_escaped(ASCII_GLYPHS.marker_error)} error item[/red]",
        " ",
        f"[bold]{_escaped(ASCII_GLYPHS.marker_current)} current item[/bold]",
        " ",
        f"[dim]{_escaped(ASCII_GLYPHS.marker_pending)} pending item[/dim]",
        " ",
        f"{_escaped(ASCII_GLYPHS.marker_plain)} plain item",
    ]
