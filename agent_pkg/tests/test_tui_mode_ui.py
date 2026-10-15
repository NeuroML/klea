#!/usr/bin/env python3
"""
Tests for the agent TUI operating-mode selector.

File: tests/test_tui_mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_agent.ui.tui.mode_ui import MODES, ModeSelector, current_mode
from klea_utils.ui.state import chats, ensure_chat
from klea_utils.ui.tui.context import TuiContext
from textual.app import App, ComposeResult


class _Host(App):
    """A minimal host that mounts the mode selector."""

    def __init__(self, ctx: TuiContext) -> None:
        """Store the context."""
        super().__init__()
        self.ctx = ctx

    def compose(self) -> ComposeResult:
        """Mount the mode selector."""
        yield ModeSelector(self.ctx)


@pytest.fixture(autouse=True)
def _isolate_chats():
    """Keep the global chat store from leaking between tests."""
    snapshot = dict(chats)
    yield
    chats.clear()
    chats.update(snapshot)


def test_current_mode_prefers_context_then_preference():
    """The effective mode comes from context, then the preference."""
    ctx = TuiContext(server_url="http://x", user_id="u", chat_id="c")
    assert current_mode(ctx) == "general"
    ensure_chat("u", "c")["context"] = {"requested": "scientific"}
    assert current_mode(ctx) == "scientific"
    ensure_chat("u", "c")["context"] = {}
    ensure_chat("u", "c")["mode_pref"] = "scientific"
    assert current_mode(ctx) == "scientific"


def test_modes_has_general_and_scientific():
    """The mode vocabulary matches the graph's."""
    assert set(MODES) == {"general", "scientific"}


async def test_mode_selector_renders_compact_line():
    """The selector renders the active mode as a compact one-line Static."""
    ctx = TuiContext(server_url="http://x", user_id="u", chat_id="c")
    app = _Host(ctx)
    async with app.run_test() as pilot:
        await pilot.pause()
        selector = app.query_one(ModeSelector)
        assert str(selector.render()) == "Mode: General"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
