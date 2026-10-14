#!/usr/bin/env python3
"""
Tests for the RAG TUI composition entry.

File: tests/test_tui_page.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import pytest
from klea_rag.ui.tui import page


def test_run_rag_tui_registers_no_slots(monkeypatch):
    """The RAG entry registers no app-specific status slots."""
    captured: dict = {}

    def _fake_run_tui(**kwargs) -> None:
        captured.update(kwargs)

    monkeypatch.setattr("klea_utils.ui.tui.app.run_tui", _fake_run_tui)
    page.run_rag_tui(url="http://x", title="Klea RAG")

    assert captured.get("status_extras", []) == []
    assert captured["url"] == "http://x"
    assert captured["title"] == "Klea RAG"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
