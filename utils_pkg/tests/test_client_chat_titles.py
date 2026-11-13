#!/usr/bin/env python3
"""
Regression tests for chat-title handling in the UI client and state.

A brand-new chat must not be named from its slug: ``ensure_chat`` starts with
an empty name, ``create_chat_on_server`` posts no derived title (the server
derives a readable label on read), and hydration keeps the stored title the
server returns.

File: tests/test_client_chat_titles.py
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from klea_utils.ui import client
from klea_utils.ui.state import chats, ensure_chat


@pytest.fixture(autouse=True)
def _clean_chats() -> Iterator[None]:
    """Reset the global chat cache around each test."""
    chats.clear()
    yield
    chats.clear()


def _patch_transport(
    monkeypatch: pytest.MonkeyPatch,
    handler: Callable[[httpx.Request], httpx.Response],
) -> None:
    """Route the client's HTTP calls through *handler* (no network)."""
    real = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", _factory)


async def test_ensure_chat_has_no_name_until_hydrated() -> None:
    """A fresh chat id yields an empty name, not a Title-Cased slug."""
    chat = ensure_chat("u", "stragic-tody")

    assert chat["name"] == ""


async def test_create_chat_does_not_send_a_derived_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Creating a chat posts only the chat id; the title stays server-side."""
    sent: dict[str, Any] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"chat_id": sent["chat_id"], "title": "Stragic Tody"},
        )

    _patch_transport(monkeypatch, _handler)
    ensure_chat("u", "stragic-tody")

    await client.create_chat_on_server("http://backend", "u", "stragic-tody")

    assert "title" not in sent
    assert chats["u:stragic-tody"]["name"] == "Stragic Tody"


async def test_hydration_keeps_a_stored_title(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Hydration keeps the stored (possibly renamed) title, never a slug."""

    def _handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/chat/u":
            return httpx.Response(
                200,
                json=[
                    {
                        "chat_id": "c1",
                        "title": "klea-scientific-mode",
                        "created_at": 1.0,
                        "preview": "",
                    }
                ],
            )
        if path == "/chat/u/c1/messages":
            return httpx.Response(200, json=[])
        if path == "/chat/u/c1/events":
            return httpx.Response(200, json=[])
        if path == "/chat/u/c1/context":
            return httpx.Response(200, json={})
        return httpx.Response(404)

    _patch_transport(monkeypatch, _handler)
    ensure_chat("u", "c1")

    await client.hydrate_chats("http://backend", "u")

    assert chats["u:c1"]["name"] == "klea-scientific-mode"
