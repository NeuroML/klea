#!/usr/bin/env python3
"""
Tests for session-command handling in the web frontend (ADR-0047).

File: tests/ui/web/test_commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.commands.common import CommandContext, ParsedCommand
from klea_utils.ui.state import chats
from klea_utils.ui.web.nicegui.components.commands import client_commands
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


async def test_help_without_a_chat_creates_one(fake_backend, agent_user):
    """A client command before the first message still creates a chat."""
    before = set(chats)
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/help")
    await agent_user.should_see("Available commands:", retries=50)

    new_chats = [chats[key] for key in set(chats) - before]
    assert new_chats
    system_texts = [
        message["text"]
        for data in new_chats
        for message in data["messages"]
        if message["role"] == "system"
    ]
    assert any("Available commands:" in text for text in system_texts)


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


def test_new_handler_calls_the_new_chat_hook_silently():
    """``/new`` invokes the hook and produces no output (ADR-0047)."""
    calls: list[str] = []
    context = CommandContext(services={"new_chat": lambda: calls.append("new")})
    result = client_commands().dispatch(ParsedCommand(name="new"), context)
    assert calls == ["new"]
    assert result.output == []
    assert result.error == ""
    assert result.notice == ""


async def _wait_for(predicate, retries: int = 50, delay: float = 0.05) -> bool:
    """Poll *predicate* until it is true (or give up after *retries*)."""
    import asyncio

    for _ in range(retries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return False


async def test_new_starts_a_fresh_session(fake_backend, agent_user):
    """``/new`` creates and switches to an empty session, without a run."""
    before = set(chats)
    marker = "zzz-answer-marker-zzz"
    fake_backend.stream_events = [{"type": "complete", "message_for_user": marker}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see(marker, retries=50)

    _send(agent_user, "/new")
    assert await _wait_for(lambda: len(set(chats) - before) == 2)

    new_chats = [chats[key] for key in set(chats) - before]
    # ``/new`` opened an empty session and sent nothing to the graph.
    assert sum(not data["messages"] for data in new_chats) == 1
    assert len(fake_backend.stream_bodies()) == 1


def test_rename_handler_requires_a_name():
    """``/rename`` with no name is a usage error (ADR-0047)."""
    result = client_commands().dispatch(
        ParsedCommand(name="rename", raw_args="   "), CommandContext(chat_id="c1")
    )
    assert result.error
    assert "Usage: /rename" in result.error


def test_rename_handler_requires_an_active_chat():
    """``/rename`` with no active session is an error."""
    result = client_commands().dispatch(
        ParsedCommand(name="rename", raw_args="New Name"), CommandContext(chat_id="")
    )
    assert "No active session" in result.error


def test_rename_handler_calls_the_hook_with_the_raw_name():
    """The full argument (spaces preserved) reaches the rename hook."""
    calls: list[tuple[str, str]] = []
    context = CommandContext(
        chat_id="c1",
        services={"rename_chat": lambda chat_id, name: calls.append((chat_id, name))},
    )
    result = client_commands().dispatch(
        ParsedCommand(name="rename", raw_args="My New Name"), context
    )
    assert calls == [("c1", "My New Name")]
    assert any("My New Name" in line for line in result.output)


async def test_rename_command_renames_the_session(fake_backend, agent_user):
    """``/rename`` renames the active session (ADR-0047)."""
    before = set(chats)
    marker = "zzz-rename-marker-zzz"
    fake_backend.stream_events = [{"type": "complete", "message_for_user": marker}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see(marker, retries=50)

    _send(agent_user, "/rename My Renamed Session")
    await agent_user.should_see("Renamed session", retries=50)

    new_chats = [chats[key] for key in set(chats) - before]
    assert any(data["name"] == "My Renamed Session" for data in new_chats)
