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


def test_credential_empty_by_default(store):
    """A provider with no stored credential returns None."""
    assert store.get_credential("u1", "openai") is None


def test_set_and_get_credential(store):
    """A stored provider credential is returned."""
    store.set_credential("u1", "openai", "", "sk-oai")
    logger.info("stored openai credential for u1")
    assert store.get_credential("u1", "openai") == "sk-oai"


def test_set_credential_replaces(store):
    """Setting a credential again replaces the previous secret."""
    store.set_credential("u1", "openai", "", "sk-old")
    store.set_credential("u1", "openai", "", "sk-new")
    assert store.get_credential("u1", "openai") == "sk-new"


def test_credentials_are_endpoint_scoped(store):
    """Two custom endpoints under the same provider are distinct scopes."""
    store.set_credential("u1", "custom", "https://a/v1", "sk-a")
    store.set_credential("u1", "custom", "https://b/v1", "sk-b")
    assert store.get_credential("u1", "custom", "https://a/v1") == "sk-a"
    assert store.get_credential("u1", "custom", "https://b/v1") == "sk-b"


def test_credentials_are_per_user(store):
    """Credentials are isolated per user."""
    store.set_credential("u1", "openai", "", "sk-u1")
    store.set_credential("u2", "openai", "", "sk-u2")
    assert store.get_credential("u1", "openai") == "sk-u1"
    assert store.get_credential("u2", "openai") == "sk-u2"


def test_list_credentials(store):
    """Listing returns every provider(+endpoint) scope with its secret."""
    store.set_credential("u1", "openai", "", "sk-oai")
    store.set_credential("u1", "custom", "https://a/v1", "sk-a")
    creds = {
        (c["provider"], c["endpoint"]): c["secret"]
        for c in store.list_credentials("u1")
    }
    assert creds == {("openai", ""): "sk-oai", ("custom", "https://a/v1"): "sk-a"}


def test_clear_credential_removes_single_scope(store):
    """Clearing one scope leaves the others intact."""
    store.set_credential("u1", "openai", "", "sk-oai")
    store.set_credential("u1", "anthropic", "", "sk-ant")
    store.clear_credential("u1", "openai")
    assert store.get_credential("u1", "openai") is None
    assert store.get_credential("u1", "anthropic") == "sk-ant"


def test_clear_credentials_removes_all(store):
    """Clearing all credentials empties the user's list."""
    store.set_credential("u1", "openai", "", "sk-oai")
    store.clear_credentials("u1")
    assert store.list_credentials("u1") == []


def test_delete_user_chats_clears_credentials(store):
    """Deleting a user session also drops its credentials."""
    store.set_credential("u1", "openai", "", "sk-oai")
    store.delete_user_chats("u1")
    assert store.list_credentials("u1") == []
