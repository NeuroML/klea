#!/usr/bin/env python3
"""
Tests for the chat UI-event endpoint (ADR-0032 update).

``GET /chat/{user_id}/{chat_id}/events`` returns the persisted inspection/
status pane events so the frontends can rebuild those panes on load.

File: tests/test_api_events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from klea_utils.api.events import create_events_router
from klea_utils.api.sessions_db import SessionStore


@pytest.fixture
def app(tmp_path):
    """Minimal FastAPI app with the events router and a real SessionStore."""
    _app = FastAPI()
    _app.state.chat_sessions = SessionStore(str(tmp_path / "sessions.db"))
    _app.include_router(create_events_router())
    yield _app
    _app.state.chat_sessions.close()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as _client:
        yield _client


def _events() -> list[dict]:
    """A small UI-event batch (a query marker + two pane events)."""
    return [
        {"type": "query", "text": "hi", "stamp": "10:00:00"},
        {"type": "inspect", "node": "Plan", "data": {"summary": "s"}},
        {"type": "state", "node": "Plan", "data": {"key": "plan"}},
    ]


async def test_returns_stored_events_in_order(app, client):
    """Stored events come back whole, oldest first."""
    store: SessionStore = app.state.chat_sessions
    store.create_chat("u", "c")
    store.add_chat_events("u", "c", _events())

    resp = await client.get("/chat/u/c/events")
    assert resp.status_code == 200
    assert resp.json() == _events()


async def test_empty_for_a_chat_without_events(app, client):
    """A chat with no stored events returns an empty list."""
    store: SessionStore = app.state.chat_sessions
    store.create_chat("u", "c")

    resp = await client.get("/chat/u/c/events")
    assert resp.status_code == 200
    assert resp.json() == []


async def test_unknown_chat_is_404(app, client):
    """A chat that does not exist is a 404 (like ``/messages``)."""
    resp = await client.get("/chat/u/missing/events")
    assert resp.status_code == 404
