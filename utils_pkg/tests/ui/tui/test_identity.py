#!/usr/bin/env python3
"""
Tests for the TUI identity seam (ADR-0050, invariant I5).

``resolve_user_id`` is pure: explicit ``--user-id`` > ``KLEA_USER_ID`` > a
persisted local UUID, the last only for a loopback server.

File: tests/ui/tui/test_identity.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import uuid

from klea_utils.ui.tui import identity


def test_explicit_wins():
    resolved = identity.resolve_user_id(
        explicit="abc", env="env", server_url="http://10.0.0.1:1"
    )
    assert resolved == "abc"


def test_env_over_persisted(monkeypatch):
    monkeypatch.setattr(identity, "persisted_user_id", lambda app: "persisted")
    assert (
        identity.resolve_user_id(env="env", server_url="http://127.0.0.1:8006") == "env"
    )


def test_loopback_uses_persisted(monkeypatch):
    monkeypatch.setattr(identity, "persisted_user_id", lambda app: "persisted")
    assert (
        identity.resolve_user_id(env="", server_url="http://127.0.0.1:8006")
        == "persisted"
    )


def test_remote_without_identity_is_empty(monkeypatch):
    monkeypatch.setattr(identity, "persisted_user_id", lambda app: "persisted")
    assert identity.resolve_user_id(env="", server_url="http://192.168.1.10:8006") == ""


def test_persisted_uuid_is_stable_and_valid(tmp_path, monkeypatch):
    class FakeDirs:
        def __init__(self, app: str) -> None:
            self.user_data_dir = str(tmp_path)

    monkeypatch.setattr("platformdirs.PlatformDirs", FakeDirs)
    first = identity.persisted_user_id("klea-tui")
    second = identity.persisted_user_id("klea-tui")
    assert first == second
    uuid.UUID(first)  # raises when not a UUID
