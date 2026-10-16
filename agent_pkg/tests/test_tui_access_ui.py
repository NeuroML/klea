#!/usr/bin/env python3
"""
Tests for the agent TUI tool-access vocabulary.

File: tests/test_tui_access_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_agent.ui.tui.access_ui import ACCESS_LEVELS, current_access_level
from klea_utils.ui.state import chats, ensure_chat
from klea_utils.ui.tui.context import TuiContext


@pytest.fixture(autouse=True)
def _isolate_chats():
    """Keep the global chat store from leaking between tests."""
    snapshot = dict(chats)
    yield
    chats.clear()
    chats.update(snapshot)


def test_current_access_level_prefers_context_then_preference():
    """The effective level comes from context, then the preference."""
    ctx = TuiContext(server_url="http://x", user_id="u", chat_id="c")
    assert current_access_level(ctx) == "full"
    ensure_chat("u", "c")["context"] = {"access_level": "read_only"}
    assert current_access_level(ctx) == "read_only"
    ensure_chat("u", "c")["context"] = {}
    ensure_chat("u", "c")["access_pref"] = "read_only"
    assert current_access_level(ctx) == "read_only"


def test_access_levels_has_full_and_read_only():
    """The access vocabulary matches the graph's."""
    assert set(ACCESS_LEVELS) == {"full", "read_only"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
