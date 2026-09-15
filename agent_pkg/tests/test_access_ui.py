#!/usr/bin/env python3
"""
Tests for the agent status-pane UI registration (ADR-0030/ADR-0037).

File: tests/test_access_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest

pytest.importorskip("nicegui")

from klea_agent.ui.web import access_ui, mode_ui
from klea_utils.ui.web.nicegui.components.context import PageContext


def _ctx() -> PageContext:
    return PageContext(server_url="http://x", user_id="u", chat_id="c")


def test_access_ui_registers_renderer():
    """Attaching the access UI registers exactly one status-pane renderer."""
    ctx = _ctx()
    access_ui.attach_access_ui(ctx)
    assert len(ctx.status_extras) == 1


def test_mode_and_access_renderers_stack():
    """Mode and access UIs each register their own renderer (no overwrite)."""
    ctx = _ctx()
    mode_ui.attach_mode_ui(ctx)
    access_ui.attach_access_ui(ctx)
    assert len(ctx.status_extras) == 2
