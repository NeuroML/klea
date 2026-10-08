#!/usr/bin/env python3
"""
Tests for the NiceGUI frontend state helpers.

File: tests/test_ui_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import (
    chat_indicator,
    missing_credentials,
    resolve_chat_choice,
    resolve_choice,
)

OPTIONS = ("general", "scientific")


class _FakeTask:
    """Minimal asyncio.Task stand-in for the registry tests."""

    def __init__(self, done: bool) -> None:
        self._done = done

    def done(self) -> bool:
        """Return the configured done state."""
        return self._done


def test_chat_is_streaming_reads_the_registry():
    """Only a live task for this page's user_id:chat_id counts."""
    ctx = PageContext(server_url="http://x", user_id="u")

    assert ctx.chat_is_streaming("c") is False

    ctx.stream_tasks["u:c"] = _FakeTask(done=False)
    assert ctx.chat_is_streaming("c") is True

    ctx.stream_tasks["u:c"] = _FakeTask(done=True)
    assert ctx.chat_is_streaming("c") is False

    # Another chat's task does not make this chat look busy.
    ctx.stream_tasks["u:other"] = _FakeTask(done=False)
    assert ctx.chat_is_streaming("c") is False


def test_chat_indicator_states_and_precedence():
    """The avatar indicator shows running > awaiting > pinned > history."""
    # Streaming wins over everything, and is a spinner (not an icon).
    assert chat_indicator(streaming=True, awaiting=True, pinned=True, active=False) == (
        "spinner",
        "",
        "w-4 h-4",
    )
    assert chat_indicator(
        streaming=True, awaiting=False, pinned=False, active=True
    ) == ("spinner", "", "w-4 h-4 text-primary")

    # Awaiting input (amber) beats pinned.
    assert chat_indicator(
        streaming=False, awaiting=True, pinned=True, active=False
    ) == ("icon", "help_outline", "text-amber")

    # Pinned vs plain history.
    assert chat_indicator(
        streaming=False, awaiting=False, pinned=True, active=False
    ) == ("icon", "push_pin", "")
    assert chat_indicator(
        streaming=False, awaiting=False, pinned=False, active=False
    ) == ("icon", "history", "")

    # The active chat is coloured, whatever the base icon.
    assert chat_indicator(
        streaming=False, awaiting=False, pinned=False, active=True
    ) == ("icon", "history", "text-primary")
    assert chat_indicator(
        streaming=False, awaiting=False, pinned=True, active=True
    ) == ("icon", "push_pin", "text-primary")


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


def test_missing_credentials_flags_required_unset():
    """A required provider with source=none is reported (by role)."""
    info = {
        "chat": {
            "model": "openai:gpt-4o",
            "credential": {
                "provider": "openai",
                "requires_key": True,
                "source": "none",
            },
        }
    }
    assert missing_credentials(info) == ["chat"]


def test_missing_credentials_ignores_env_and_user():
    """Stored or environment credentials are not 'missing'."""
    info = {
        "chat": {
            "model": "openai:gpt-4o",
            "credential": {"provider": "openai", "requires_key": True, "source": "env"},
        },
        "plan": {
            "model": "anthropic:claude",
            "credential": {
                "provider": "anthropic",
                "requires_key": True,
                "source": "user",
            },
        },
    }
    assert missing_credentials(info) == []


def test_missing_credentials_ignores_keyless_provider():
    """A local (key-less) provider is never 'missing'."""
    info = {
        "chat": {
            "model": "ollama:qwen3",
            "credential": {
                "provider": "ollama",
                "requires_key": False,
                "source": "none",
            },
        }
    }
    assert missing_credentials(info) == []


def test_missing_credentials_ignores_unset_model():
    """A role with no resolved model is not flagged."""
    info = {
        "plan": {
            "model": "",
            "credential": {
                "provider": "openai",
                "requires_key": True,
                "source": "none",
            },
        }
    }
    assert missing_credentials(info) == []


def test_missing_credentials_without_credential_block():
    """A config without a credential block is treated as not missing."""
    assert missing_credentials({"chat": {"model": "openai:gpt-4o"}}) == []
