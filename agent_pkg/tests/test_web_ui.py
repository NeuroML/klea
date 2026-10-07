#!/usr/bin/env python3
"""
Tests for the agent NiceGUI page using NiceGUI's in-process user simulation.

The page is driven without a browser: ``nicegui.testing.user_simulation``
runs :func:`klea_agent.ui.web.page.setup_layout` in-process, while a patched
``httpx.AsyncClient`` routes the frontend's backend calls to a canned fake
backend, so no server or model is needed.

File: tests/test_web_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import httpx
import pytest

pytest.importorskip("nicegui")

from klea_agent.ui.web.page import setup_layout
from nicegui.testing.user_simulation import user_simulation


def _handler(request: httpx.Request) -> httpx.Response:
    """Canned backend responses for the frontend's bootstrap calls."""
    url = str(request.url)
    if url.endswith("/health/ready"):
        return httpx.Response(200, json={"status": "ok"})
    if request.method == "GET" and url.endswith("/chat/u"):
        return httpx.Response(200, json=[])
    if url.endswith("/models/active"):
        return httpx.Response(200, json={})
    if "/credentials/" in url:
        return httpx.Response(200, json=[])
    if url.endswith("/models/catalogue"):
        return httpx.Response(200, json={"providers": [], "models": []})
    return httpx.Response(200, json={})


@pytest.fixture
def fake_backend(monkeypatch):
    """Route all frontend HTTP through a canned in-process transport."""
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(_handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)


@pytest.fixture
async def agent_user(fake_backend):
    """A simulated user on the real agent page with a fake backend."""

    def root() -> None:
        setup_layout(
            chat_id="",
            server_url="http://backend",
            user_id="u",
            title="Klea Test",
        )

    async with user_simulation(root=root) as user:
        yield user


async def test_agent_page_renders(agent_user):
    """The agent page renders, and the background load clears the banner."""
    await agent_user.open("/")
    await agent_user.should_see("Klea Test")
    # The health probe + hydrate run in a background task; the readiness
    # banner is cleared only after they complete, so this also proves the
    # patched transport served the bootstrap calls.
    await agent_user.should_not_see("Backend is starting")
