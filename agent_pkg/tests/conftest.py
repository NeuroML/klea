#!/usr/bin/env python3
"""
Shared fixtures for the agent NiceGUI tests.

The agent page is driven in-process with NiceGUI's user simulation.  A
:class:`FakeBackend` supplies canned responses for every backend call the
frontend makes (bootstrap, hydration, model/credential lookup), and a
scriptable ``/query/stream`` response, so no server and no model are
needed.  The frontend's ``httpx.AsyncClient`` is patched to route through
the fake backend's transport.

File: tests/conftest.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

import httpx
import pytest


class FakeBackend:
    """Canned in-process backend for the frontend's HTTP calls.

    Tests set the response fields (``chats``, ``session_models``, ...) and
    the scripted ``stream_events``; every request is recorded in
    :attr:`requests` so a test can assert what the UI sent.
    """

    def __init__(self) -> None:
        """Start with empty responses and no scripted stream events."""
        self.chats: list[dict] = []
        self.messages: dict[str, list[dict]] = {}
        self.context: dict[str, dict] = {}
        self.session_models: dict = {}
        self.chat_models: dict = {}
        self.credentials: list[dict] = []
        self.catalogue: dict = {"providers": [], "models": []}
        #: Events returned by ``POST /query/stream`` as an SSE body.
        self.stream_events: list[dict] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Return a canned response for *request*."""
        self.requests.append(request)
        parts = request.url.path.strip("/").split("/")
        method = request.method
        if parts == ["health", "ready"]:
            return httpx.Response(200, json={"status": "ok"})
        if parts == ["query", "stream"] and method == "POST":
            body = "".join(
                f"data: {json.dumps(event)}\n\n" for event in self.stream_events
            )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        if parts == ["query", "cancel"] and method == "POST":
            return httpx.Response(204)
        if parts and parts[0] == "credentials":
            return httpx.Response(200, json=self.credentials)
        if parts and parts[0] == "chat":
            return self._chat_response(parts, method)
        return httpx.Response(200, json={})

    def _chat_response(self, parts: list[str], method: str) -> httpx.Response:
        """Resolve ``/chat/...`` read endpoints from the configured state."""
        # ["chat", <user>]
        if len(parts) == 2 and method == "GET":
            return httpx.Response(200, json=self.chats)
        # ["chat", <user>, "models", ...]
        if parts[2:3] == ["models"]:
            if parts[3:4] == ["active"]:
                return httpx.Response(200, json=self.session_models)
            if parts[3:4] == ["catalogue"]:
                return httpx.Response(200, json=self.catalogue)
        # ["chat", <user>, <chat>, ...]
        if len(parts) >= 4 and parts[3:4] == ["messages"] and method == "GET":
            return httpx.Response(200, json=self.messages.get(parts[2], []))
        if len(parts) >= 4 and parts[3:4] == ["context"] and method == "GET":
            return httpx.Response(
                200,
                json=self.context.get(
                    parts[2], {"context": None, "pending_interrupt": None}
                ),
            )
        if len(parts) >= 5 and parts[3:5] == ["models", "active"]:
            return httpx.Response(200, json=self.chat_models)
        return httpx.Response(200, json={})


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Route all frontend HTTP calls through a :class:`FakeBackend`."""
    backend = FakeBackend()
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(backend.handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return backend


@pytest.fixture
async def agent_user(fake_backend: FakeBackend):
    """A simulated user on the real agent page, backed by *fake_backend*."""
    pytest.importorskip("nicegui")

    from klea_agent.ui.web.page import setup_layout
    from nicegui.testing.user_simulation import user_simulation

    def root() -> None:
        setup_layout(
            chat_id="",
            server_url="http://backend",
            user_id="u",
            title="Klea Test",
        )

    async with user_simulation(root=root) as user:
        yield user
