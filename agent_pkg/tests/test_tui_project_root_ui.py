#!/usr/bin/env python3
"""
Tests for the agent TUI project-root status slot.

File: tests/test_tui_project_root_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import pytest
from klea_agent.ui.tui.project_root_ui import (
    current_project_root,
    project_root_status_extra,
)
from klea_utils.ui.state import chats, ensure_chat
from klea_utils.ui.tui.components.selectable import SelectableStatic
from klea_utils.ui.tui.context import TuiContext


@pytest.fixture(autouse=True)
def _isolate_chats():
    """Keep the global chat store from leaking between tests."""
    snapshot = dict(chats)
    yield
    chats.clear()
    chats.update(snapshot)


def _ctx() -> TuiContext:
    return TuiContext(server_url="http://x", user_id="u", chat_id="c")


def test_current_project_root_reads_context():
    """The effective root comes from the chat's streamed context."""
    ctx = _ctx()
    assert current_project_root(ctx) == ""
    ensure_chat("u", "c")["context"] = {"project_root": "/work/proj"}
    assert current_project_root(ctx) == "/work/proj"


def test_extra_renders_default_and_value():
    """The row shows the server-cwd placeholder, then the context value."""
    ctx = _ctx()
    widget = project_root_status_extra(ctx)
    assert isinstance(widget, SelectableStatic)
    assert "(server working directory)" in str(widget.render())
    ensure_chat("u", "c")["context"] = {"project_root": "/work/proj"}
    widget = project_root_status_extra(ctx)
    assert str(widget.render()) == "Project root: /work/proj"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
