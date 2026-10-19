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

from typing import cast

import httpx
import pytest
from klea_utils.ui import client
from klea_utils.ui.state import chats

#: Stored UI events the fake server returns for ``/events`` (query marker +
#: an inspect entry + a status section).
_EVENTS = [
    {"type": "query", "text": "hello", "stamp": "10:00:00"},
    {
        "type": "inspect",
        "node": "Plan",
        "data": {"heading": "Plan", "summary": "1 step", "details": {}},
    },
    {
        "type": "state",
        "node": "Plan",
        "data": {
            "key": "plan",
            "heading": "Plan",
            "display": "[x] Step 1",
            "summary": "1 step",
            "preformatted": True,
        },
    },
]

#: Checkpointed cumulative token totals the fake server projects.
_USAGE = {"input_tokens": 5, "output_tokens": 3, "total_tokens": 8}


@pytest.fixture(autouse=True)
def _clear_chats():
    chats.clear()
    yield
    chats.clear()


def _handler(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url.endswith("/chat/u") and request.method == "GET":
        return httpx.Response(
            200,
            json=[
                {
                    "chat_id": "c1",
                    "title": "C1",
                    "created_at": 0,
                    "preview": "show me X",
                }
            ],
        )
    if url.endswith("/chat/u/c1/messages"):
        return httpx.Response(200, json=[])
    if url.endswith("/chat/u/c1/events"):
        return httpx.Response(200, json=_EVENTS)
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
                "usage": _USAGE,
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


async def test_events_are_replayed_into_the_panes(_mock_client):
    """Stored events rebuild the inspector/status panes on hydration."""
    await client.hydrate_chats("http://backend", "u")

    chat = chats["u:c1"]
    # The query marker opens the section; the inspect entry follows it, and
    # the state event fills the status-pane section.
    entries = cast(list[dict], chat["inspector_entries"])
    assert [e["type"] for e in entries] == ["query", "inspect"]
    assert entries[0]["text"] == "hello"
    assert entries[1]["heading"] == "Plan"
    sections = cast(dict, chat["state_sections"])
    assert sections["plan"]["display"] == "[x] Step 1"


async def test_usage_is_hydrated_from_the_checkpoint(_mock_client):
    """The checkpointed token totals seed ``token_usage`` (ADR-0032 update)."""
    await client.hydrate_chats("http://backend", "u")

    assert chats["u:c1"]["token_usage"] == _USAGE


async def test_session_preview_is_hydrated(_mock_client):
    """The server's first-query preview reaches the chat cache."""
    await client.hydrate_chats("http://backend", "u")

    assert chats["u:c1"]["preview"] == "show me X"
