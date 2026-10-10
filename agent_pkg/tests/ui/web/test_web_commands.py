#!/usr/bin/env python3
"""
Tests for session-command handling in the web frontend (ADR-0047).

File: tests/ui/web/test_commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction


def _suggestion_items(user, usage: str) -> list:
    """Autocomplete items whose label text contains *usage*."""
    return [
        item
        for item in user.find(ui.item).elements
        if any(
            usage in (getattr(descendant, "text", "") or "")
            for descendant in item.descendants()
        )
    ]


def _send(user, text: str) -> None:
    """Type *text* into the chat input and press Enter."""
    user.find(ui.textarea).type(text)
    user.find(ui.textarea).trigger("keydown.enter.exact.prevent")


def _mode_metadata() -> dict:
    """Server-side ``/mode`` catalogue entry."""
    return {
        "name": "mode",
        "summary": "Show or set the operating mode",
        "arg_hint": "[general|scientific]",
        "side": "server",
        "klass": "session-state",
        "while_streaming": "block",
        "persists": "checkpoint",
        "capabilities": [],
        "implemented": True,
        "aliases": [],
    }


async def test_help_renders_in_the_chat(fake_backend, agent_user):
    """A client ``/help`` renders the merged catalogue as a system block."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "hi"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("hi", retries=50)

    _send(agent_user, "/help")
    await agent_user.should_see("Available commands:", retries=50)
    await agent_user.should_see("/help")


async def test_unknown_command_is_rejected_locally(fake_backend, agent_user):
    """An unknown command is rejected in the frontend and never sent."""
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/nope")
    await agent_user.should_see("Unknown command", retries=50)
    assert fake_backend.stream_bodies() == []


async def test_slash_lists_matching_commands(fake_backend, agent_user):
    """Typing ``/`` opens a menu of matching commands from the catalogue."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("/")
    await agent_user.should_see("/help", retries=50)
    await agent_user.should_see("/mode", retries=50)


async def test_slash_filters_by_prefix(fake_backend, agent_user):
    """The menu lists only commands matching the typed prefix."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("/mo")
    await agent_user.should_see("/mode", retries=50)
    await agent_user.should_not_see("/help")


async def test_clicking_a_suggestion_completes_and_closes(fake_backend, agent_user):
    """Clicking a suggestion fills the input and hides the list (ADR-0047)."""
    fake_backend.commands = [_mode_metadata()]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find(ui.textarea).type("/mo")
    await agent_user.should_see("/mode", retries=50)

    items = _suggestion_items(agent_user, "/mode")
    assert len(items) == 1
    UserInteraction(agent_user, {items[0]}, None).click()

    assert agent_user.find(ui.textarea).elements.pop().value == "/mode "
    assert _suggestion_items(agent_user, "/mode") == []


async def test_server_command_is_forwarded(fake_backend, agent_user):
    """A server command is forwarded to the graph as the query."""
    fake_backend.commands = [_mode_metadata()]
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "ok"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/mode general")
    await agent_user.should_see("ok", retries=50)
    assert fake_backend.stream_bodies()[-1]["query"] == "/mode general"
