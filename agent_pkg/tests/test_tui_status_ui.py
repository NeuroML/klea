#!/usr/bin/env python3
"""
Tests for the agent TUI combined mode/access status row.

File: tests/test_tui_status_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_agent.ui.tui.status_ui import ModeAccessSelector
from klea_utils.ui.state import chats, ensure_chat
from klea_utils.ui.tui.context import TuiContext
from textual.app import App, ComposeResult
from textual.widgets import Static


class _Host(App):
    """A minimal host that mounts the combined mode/access row."""

    def __init__(self, ctx: TuiContext) -> None:
        """Store the context."""
        super().__init__()
        self.ctx = ctx

    def compose(self) -> ComposeResult:
        """Mount the combined selector."""
        yield ModeAccessSelector(self.ctx)


@pytest.fixture(autouse=True)
def _isolate_chats():
    """Keep the global chat store from leaking between tests."""
    snapshot = dict(chats)
    yield
    chats.clear()
    chats.update(snapshot)


async def test_mode_access_row_renders_one_line():
    """The row shows ``<mode> | <level> access`` on one line."""
    ctx = TuiContext(server_url="http://x", user_id="u", chat_id="c")
    app = _Host(ctx)
    async with app.run_test() as pilot:
        await pilot.pause()
        mode = app.query_one("#mode-access-mode", Static)
        access = app.query_one("#mode-access-access", Static)
        assert str(mode.render()) == "General | "
        assert str(access.render()) == "Full access"
        assert access.has_class("access-full")
        assert mode.region.y == access.region.y


async def test_mode_access_row_tracks_context():
    """The row follows the checkpointed mode and read-only access."""
    ensure_chat("u", "c")["context"] = {
        "requested": "scientific",
        "access_level": "read_only",
    }
    ctx = TuiContext(server_url="http://x", user_id="u", chat_id="c")
    app = _Host(ctx)
    async with app.run_test() as pilot:
        await pilot.pause()
        mode = app.query_one("#mode-access-mode", Static)
        access = app.query_one("#mode-access-access", Static)
        assert str(mode.render()) == "Scientific | "
        assert str(access.render()) == "Read-only access"
        assert access.has_class("access-read-only")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
