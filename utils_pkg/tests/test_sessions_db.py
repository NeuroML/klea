#!/usr/bin/env python3
"""
Tests for the SQLite session store's per-user default model overrides.

File: tests/test_sessions_db.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_utils.api.sessions_db import SessionStore

logger = logging.getLogger(__name__)


@pytest.fixture
def store(tmp_path):
    _store = SessionStore(str(tmp_path / "sessions.db"))
    yield _store
    _store.close()


def test_session_overrides_empty_by_default(store):
    """A user with no stored defaults returns an empty mapping."""
    assert store.get_session_overrides("u1") == {}


def test_set_and_get_session_override(store):
    """A stored per-user default is returned keyed by role."""
    store.set_session_override("u1", "chat", {"model": "ollama:qwen3"})
    logger.info("stored chat default for u1")
    assert store.get_session_overrides("u1") == {"chat": {"model": "ollama:qwen3"}}


def test_set_session_override_replaces_role(store):
    """Setting a role again replaces only that role's config."""
    store.set_session_override("u1", "chat", {"model": "ollama:qwen3"})
    store.set_session_override("u1", "plan", {"model": "ollama:qwen3:0.6b"})
    store.set_session_override("u1", "chat", {"model": "openai:gpt-4o"})
    assert store.get_session_overrides("u1") == {
        "chat": {"model": "openai:gpt-4o"},
        "plan": {"model": "ollama:qwen3:0.6b"},
    }


def test_clear_session_override_removes_single_role(store):
    """Clearing a role leaves the other defaults intact."""
    store.set_session_override("u1", "chat", {"model": "ollama:qwen3"})
    store.set_session_override("u1", "plan", {"model": "ollama:qwen3:0.6b"})
    store.clear_session_override("u1", "chat")
    assert store.get_session_overrides("u1") == {"plan": {"model": "ollama:qwen3:0.6b"}}


def test_clear_session_overrides_removes_all(store):
    """Clearing all defaults empties the user's mapping."""
    store.set_session_override("u1", "chat", {"model": "ollama:qwen3"})
    store.clear_session_overrides("u1")
    assert store.get_session_overrides("u1") == {}


def test_session_overrides_are_per_user(store):
    """Defaults are isolated per user."""
    store.set_session_override("u1", "chat", {"model": "ollama:qwen3"})
    store.set_session_override("u2", "chat", {"model": "openai:gpt-4o"})
    assert store.get_session_overrides("u1")["chat"]["model"] == "ollama:qwen3"
    assert store.get_session_overrides("u2")["chat"]["model"] == "openai:gpt-4o"


def test_delete_user_chats_clears_session_overrides(store):
    """Deleting a user session also drops its default overrides."""
    store.create_chat("u1", "c1")
    store.set_override("u1", "c1", "chat", {"model": "ollama:qwen3"})
    store.set_session_override("u1", "chat", {"model": "ollama:qwen3"})
    store.delete_user_chats("u1")
    assert store.get_session_overrides("u1") == {}
    assert store.get_overrides("u1", "c1") == {}
    assert store.get_chat("u1", "c1") is None
