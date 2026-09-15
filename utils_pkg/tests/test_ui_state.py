#!/usr/bin/env python3
"""
Tests for the NiceGUI frontend state helpers.

File: tests/test_ui_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.ui.web.nicegui.state import resolve_choice

OPTIONS = ("general", "scientific")


def test_pending_wins():
    """The pending (next-query) choice is used when valid."""
    assert resolve_choice("scientific", "general", "general", OPTIONS, "general") == (
        "scientific"
    )


def test_pref_used_without_pending():
    """The per-chat preference is used when there is no pending choice."""
    assert resolve_choice(None, "scientific", "general", OPTIONS, "general") == (
        "scientific"
    )


def test_context_used_without_pending_or_pref():
    """The hydrated context value is used last."""
    assert resolve_choice(None, None, "scientific", OPTIONS, "general") == "scientific"


def test_default_when_no_valid_choice():
    """An invalid candidate is skipped and the default returned."""
    assert resolve_choice(None, "bogus", None, OPTIONS, "general") == "general"
    assert resolve_choice(None, None, None, OPTIONS, "general") == "general"
