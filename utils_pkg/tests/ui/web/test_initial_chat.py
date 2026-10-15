#!/usr/bin/env python3
"""
Tests for the web frontend's initial-chat resolution (``--resume``/``--continue``).

File: tests/ui/web/test_initial_chat.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

from klea_utils.ui.web.nicegui.components import bootstrap


async def test_resume_wins():
    """``--resume <chat_id>`` is used verbatim."""
    resolved = await bootstrap.resolve_initial_chat(resume="chat-9")
    assert resolved == "chat-9"


async def test_continue_opens_most_recent(monkeypatch):
    """``--continue`` opens the first chat from the server list."""
    calls: list[tuple[str, str]] = []

    async def fake_fetch_chats(server_url: str, user_id: str):
        calls.append((server_url, user_id))
        return [{"chat_id": "recent"}, {"chat_id": "older"}]

    monkeypatch.setattr("klea_utils.ui.client.fetch_chats", fake_fetch_chats)
    resolved = await bootstrap.resolve_initial_chat(
        continue_session=True, server_url="http://x", user_id="u1"
    )
    assert resolved == "recent"
    assert calls == [("http://x", "u1")]


async def test_continue_with_no_chats_starts_fresh(monkeypatch):
    async def fake_fetch_chats(server_url: str, user_id: str):
        return []

    monkeypatch.setattr("klea_utils.ui.client.fetch_chats", fake_fetch_chats)
    assert await bootstrap.resolve_initial_chat(continue_session=True) == ""


async def test_default_is_empty():
    """Neither flag -> a fresh chat."""
    assert await bootstrap.resolve_initial_chat() == ""
