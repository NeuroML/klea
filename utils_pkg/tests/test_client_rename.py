#!/usr/bin/env python3
"""
Tests for the NiceGUI client's server-side chat rename.

``rename_chat_on_server`` checks the PATCH status: it returns ``True`` and
updates the cached chat name only when the server accepts the rename, and
returns ``False`` (leaving the cache untouched) when the server rejects it.

File: tests/test_client_rename.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import httpx
import pytest
from klea_utils.ui import client
from klea_utils.ui.state import chats, ensure_chat

#: Status the fake PATCH handler answers with; each test sets it before use.
_PATCH_STATUS: dict[str, int] = {"status": 200}


@pytest.fixture(autouse=True)
def _clear_chats():
    chats.clear()
    _PATCH_STATUS["status"] = 200
    yield
    chats.clear()


@pytest.fixture
def _patch_transport(monkeypatch):
    """Patch the client's httpx client with a MockTransport PATCH handler.

    The handler answers only ``PATCH /chat/u/c1``; the status it returns is
    read from ``_PATCH_STATUS`` so each test can pick success or failure.
    """
    real = httpx.AsyncClient

    def _factory(*args, **kwargs):
        def _handler(request: httpx.Request) -> httpx.Response:
            if request.method == "PATCH" and str(request.url).endswith("/chat/u/c1"):
                return httpx.Response(_PATCH_STATUS["status"])
            return httpx.Response(404)

        kwargs["transport"] = httpx.MockTransport(_handler)
        return real(*args, **kwargs)

    monkeypatch.setattr(client.httpx, "AsyncClient", _factory)


async def test_rename_failure_is_reported(_patch_transport):
    """A rejected rename returns False and leaves the cached name unchanged."""
    _PATCH_STATUS["status"] = 404
    ensure_chat("u", "c1")["name"] = "Old"

    ok = await client.rename_chat_on_server("http://backend", "u", "c1", "New")

    assert ok is False
    assert chats["u:c1"]["name"] == "Old"


async def test_rename_success_updates_the_cache(_patch_transport):
    """An accepted rename returns True and updates the cached name."""
    _PATCH_STATUS["status"] = 200
    ensure_chat("u", "c1")["name"] = "Old"

    ok = await client.rename_chat_on_server("http://backend", "u", "c1", "New")

    assert ok is True
    assert chats["u:c1"]["name"] == "New"
