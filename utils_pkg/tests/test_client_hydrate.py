#!/usr/bin/env python3
"""
Tests for the NiceGUI server-API client hydration.

``hydrate_chats`` populates the in-memory chat store from the server, and
must re-present a paused thread's HITL ask (ADR-0046) so a reloaded page
renders the form instead of a dead chat.

File: tests/test_client_hydrate.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import httpx
import pytest
from klea_utils.ui.web.nicegui import client
from klea_utils.ui.web.nicegui.state import chats


@pytest.fixture(autouse=True)
def _clear_chats():
    chats.clear()
    yield
    chats.clear()


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/chat/u") and request.method == "GET":
        return httpx.Response(
            200, json=[{"chat_id": "c1", "title": "C1", "created_at": 0}]
        )
    if url.endswith("/chat/u/c1/messages"):
        return httpx.Response(200, json=[])
    if url.endswith("/chat/u/c1/context"):
        return httpx.Response(
            200,
            json={
                "context": None,
                "pending_interrupt": {
                    "kind": "input",
                    "questions": [{"step_number": 1, "question": "which file?"}],
                    "interrupt_id": "i1",
                },
            },
        )
    return httpx.Response(404)


@pytest.fixture
def _mock_client(monkeypatch):
    """Patch the client's httpx client with a recording MockTransport."""
    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", _factory)


async def test_pending_interrupt_is_hydrated(_mock_client):
    """A pending interrupt sets the ask and the awaiting_input turn status."""
    await client.hydrate_chats("http://backend", "u")

    chat = chats["u:c1"]
    assert chat["interrupt"]["interrupt_id"] == "i1"
    assert chat["interrupt"]["questions"][0]["question"] == "which file?"
    assert chat["turn_status"] == {"kind": "awaiting_input"}


async def test_no_pending_interrupt_leaves_idle(monkeypatch):
    """Without a pending interrupt the chat stays idle."""
    real = httpx.AsyncClient

    def _idle_handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).endswith("/context"):
            return httpx.Response(
                200, json={"context": None, "pending_interrupt": None}
            )
        return _handler(request)

    def _factory(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(_idle_handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", _factory)
    await client.hydrate_chats("http://backend", "u")

    chat = chats["u:c1"]
    assert "interrupt" not in chat
    assert chat["turn_status"] == {"kind": "idle"}
