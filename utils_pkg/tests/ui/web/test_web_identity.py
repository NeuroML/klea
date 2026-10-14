#!/usr/bin/env python3
"""
Tests for the web page identity resolution (shared identity seam).

Order: explicit ``--user-id`` / ``KLEA_USER_ID`` > the canonical host-local
identity file (only when the backend and the requesting browser are both
loopback) > the per-browser cookie.

File: tests/ui/web/test_web_identity.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import pytest
from klea_utils.ui.web.nicegui.components import storage


@pytest.fixture(autouse=True)
def _no_env_user_id(monkeypatch):
    """Ensure KLEA_USER_ID does not leak in from the environment."""
    monkeypatch.delenv("KLEA_USER_ID", raising=False)


async def test_explicit_wins(monkeypatch):
    monkeypatch.setattr(storage, "client_is_loopback", lambda: True)
    resolved = await storage.resolve_page_user_id(
        explicit="abc", server_url="http://10.0.0.1:1"
    )
    assert resolved == "abc"


async def test_env_over_file(monkeypatch):
    monkeypatch.setenv("KLEA_USER_ID", "env-id")
    monkeypatch.setattr(storage, "client_is_loopback", lambda: True)
    resolved = await storage.resolve_page_user_id(server_url="http://127.0.0.1:8006")
    assert resolved == "env-id"


async def test_loopback_uses_shared_file(monkeypatch):
    monkeypatch.setattr(storage, "client_is_loopback", lambda: True)
    monkeypatch.setattr(
        "klea_utils.ui.identity.persisted_user_id", lambda app: f"file:{app}"
    )
    resolved = await storage.resolve_page_user_id(
        server_url="http://127.0.0.1:8006", identity_app_name="klea-agent"
    )
    assert resolved == "file:klea-agent"


async def test_remote_client_falls_back_to_cookie(monkeypatch):
    # Backend loopback but the browser is remote: never hand over the local file.
    monkeypatch.setattr(storage, "client_is_loopback", lambda: False)

    async def fake_browser_user_id():
        return "cookie"

    monkeypatch.setattr(storage, "browser_user_id", fake_browser_user_id)
    resolved = await storage.resolve_page_user_id(server_url="http://127.0.0.1:8006")
    assert resolved == "cookie"


async def test_remote_server_falls_back_to_cookie(monkeypatch):
    monkeypatch.setattr(storage, "client_is_loopback", lambda: True)

    async def fake_browser_user_id():
        return "cookie"

    monkeypatch.setattr(storage, "browser_user_id", fake_browser_user_id)
    resolved = await storage.resolve_page_user_id(server_url="http://192.168.1.10:8006")
    assert resolved == "cookie"
