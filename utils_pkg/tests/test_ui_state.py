#!/usr/bin/env python3
"""
Tests for the NiceGUI frontend state helpers.

File: tests/test_ui_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.ui.web.nicegui.state import resolve_chat_choice, resolve_choice

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


def test_chat_choice_pending_before_chat_exists():
    """Before a chat exists the pending page-session choice is honored."""
    assert (
        resolve_chat_choice(None, "scientific", None, None, OPTIONS, "general")
        == "scientific"
    )


def test_chat_choice_per_chat_state_wins():
    """With a chat active, its own preference/context beat the stale pending."""
    chat = {"mode_pref": "scientific"}
    assert (
        resolve_chat_choice(chat, "general", "scientific", None, OPTIONS, "general")
        == "scientific"
    )


def test_chat_choice_context_when_no_pref():
    """With a chat active and no preference, its hydrated context is used."""
    chat = {"context": {}}
    assert (
        resolve_chat_choice(chat, "general", None, "scientific", OPTIONS, "general")
        == "scientific"
    )


def test_chat_choice_ignores_pending_for_existing_chat():
    """A stale pending from another chat cannot leak into an existing chat."""
    chat = {"context": {}}
    assert (
        resolve_chat_choice(chat, "scientific", None, None, OPTIONS, "general")
        == "general"
    )
