#!/usr/bin/env python3
"""
Tests for the agent TUI composition entry.

File: tests/test_tui_page.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_agent.ui.tui import page
from klea_agent.ui.tui.access_ui import access_status_extra
from klea_agent.ui.tui.mode_ui import mode_status_extra


def test_run_agent_tui_registers_selectors(monkeypatch):
    """The agent entry passes its mode + access selectors as status slots."""
    captured: dict = {}

    def _fake_run_tui(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("klea_utils.ui.tui.app.run_tui", _fake_run_tui)
    page.run_agent_tui(url="http://x", title="Klea")

    assert captured["status_extras"] == [mode_status_extra, access_status_extra]
    assert captured["url"] == "http://x"
    assert captured["title"] == "Klea"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
