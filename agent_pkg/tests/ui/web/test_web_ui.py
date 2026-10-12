#!/usr/bin/env python3
"""
Tests for the agent NiceGUI page using NiceGUI's in-process user simulation.

The page is driven without a browser: ``nicegui.testing.user_simulation``
runs :func:`klea_agent.ui.web.page.setup_layout` in-process, while a patched
``httpx.AsyncClient`` routes the frontend's backend calls to a canned fake
backend, so no server or model is needed.  The fixtures live in
``tests/ui/web/conftest.py``.

File: tests/ui/web/test_web_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.ui.web.nicegui.state import chats
from nicegui import ui


def _send(user, text: str) -> None:
    """Type *text* into the chat input and press Enter."""
    user.find(ui.textarea).type(text)
    user.find(ui.textarea).trigger("keydown.enter.exact.prevent")


def _new_chats(before: set) -> list:
    """Chats created since *before* (a snapshot of the store's keys)."""
    return [chats[key] for key in set(chats) - before]


async def test_agent_page_renders(agent_user):
    """The agent page renders, and the background load clears the banner."""
    await agent_user.open("/")
    await agent_user.should_see("Klea Test")
    # The health probe + hydrate run in a background task; the readiness
    # banner is cleared only after they complete, so this also proves the
    # patched transport served the bootstrap calls.
    await agent_user.should_not_see("Backend is starting")


async def test_mode_and_access_selectors_render(agent_user):
    """The agent status pane carries both the mode and access selectors."""
    await agent_user.open("/")
    await agent_user.should_see("Mode:")
    await agent_user.should_see("General")
    await agent_user.should_see("Scientific")
    await agent_user.should_see("Access:")
    await agent_user.should_see("Full")
    await agent_user.should_see("Read-only")


def _mode_command_metadata() -> dict:
    """Server-side ``/mode`` catalogue entry so the input is forwarded."""
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


async def test_mode_command_reconciles_the_selector(fake_backend, agent_user):
    """A ``/mode`` command's context event updates the per-chat preference."""
    before = set(chats)
    fake_backend.commands = [_mode_command_metadata()]
    fake_backend.stream_events = [
        {
            "type": "context",
            "data": {
                "requested": "scientific",
                "mode": "scientific",
                "access_level": "full",
            },
        },
        {"type": "complete", "message_for_user": "Operating mode set to scientific."},
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/mode scientific")
    await agent_user.should_see("Operating mode set to scientific.", retries=50)

    new_chats = _new_chats(before)
    assert new_chats
    assert any(data.get("mode_pref") == "scientific" for data in new_chats)


async def test_no_mode_request_before_a_selection(fake_backend, agent_user):
    """A plain first message carries no seeded default mode request."""
    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "greeting-response"}
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "hello")
    await agent_user.should_see("greeting-response", retries=50)

    body = fake_backend.stream_bodies()[-1]
    assert "mode" not in (body.get("extra") or {})


def _access_command_metadata() -> dict:
    """Server-side ``/access`` catalogue entry so the input is forwarded."""
    return {
        "name": "access",
        "summary": "Show or set the tool access level",
        "arg_hint": "[read_only|full]",
        "side": "server",
        "klass": "session-state",
        "while_streaming": "block",
        "persists": "checkpoint",
        "capabilities": [],
        "implemented": True,
        "aliases": [],
    }


async def test_access_command_reconciles_the_selector(fake_backend, agent_user):
    """An ``/access`` command's context event updates the per-chat preference."""
    before = set(chats)
    fake_backend.commands = [_access_command_metadata()]
    fake_backend.stream_events = [
        {
            "type": "context",
            "data": {
                "requested": "general",
                "mode": "general",
                "access_level": "read_only",
            },
        },
        {"type": "complete", "message_for_user": "Tool access level set to read_only."},
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "/access read_only")
    await agent_user.should_see("Tool access level set to read_only.", retries=50)

    new_chats = _new_chats(before)
    assert new_chats
    assert any(data.get("access_pref") == "read_only" for data in new_chats)
