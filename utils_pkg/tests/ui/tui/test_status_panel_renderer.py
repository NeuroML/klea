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

from klea_utils.ui.tui.components.status_panel import (
    _ROLE_STYLES,
    _block_lines,
    _content_body,
)


def test_items_block_renders_one_line_per_item_with_roles():
    """An ``items`` block renders one styled line per item, mapping roles."""
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
        "[green]ok item[/green]",
        "[red]error item[/red]",
        "[bold]current item[/bold]",
        "[dim]pending item[/dim]",
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
    assert lines == ["plain"]


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
    assert _block_lines(block) == ["[green]kept[/green]"]


def test_content_body_renders_blocks_without_an_app():
    """``_content_body`` builds one widget per rendered line, no app needed."""
    widgets = _content_body(
        [
            {"type": "items", "items": [{"text": "one"}, {"text": "two"}]},
            {"type": "mystery", "text": "three"},
            {"type": "text", "text": "plain", "role": "mystery"},
        ]
    )
    assert len(widgets) == 4
