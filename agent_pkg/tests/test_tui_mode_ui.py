#!/usr/bin/env python3
"""
Tests for the agent TUI operating-mode vocabulary.

File: tests/test_tui_mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_agent.ui.tui.mode_ui import MODES, current_mode
from klea_utils.ui.state import chats, ensure_chat
from klea_utils.ui.tui.context import TuiContext


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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
