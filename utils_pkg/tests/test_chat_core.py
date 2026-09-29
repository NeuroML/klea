#!/usr/bin/env python3
"""
Tests for the shared chat plumbing's per-run context assembly (ADR-0033).

``chat_core`` assembles the per-run ``context`` dict passed to the graph
run methods: the framework-provided ``model_overrides`` slice plus any
app-defined ``context_fields`` (coerced/validated against the app's
``context_schema`` at the graph boundary, ADR-0033).  These tests drive
``run_query`` with a stubbed request so no app routing is involved.

File: tests/test_chat_core.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request
from klea_utils.api import chat_core
from klea_utils.api.sessions_db import SessionStore
from klea_utils.llm import LLMModel


def _make_request(store: SessionStore, graph) -> Request:
    """A fake FastAPI request whose ``app.state`` carries graph + store."""
    app = SimpleNamespace(
        state=SimpleNamespace(is_ready=True, graph=graph, chat_sessions=store)
    )
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": "/query",
        "raw_path": b"/query",
        "query_string": b"",
        "root_path": "",
        "headers": [],
        "client": ("test", 123),
        "server": ("test", 80),
        "scheme": "http",
        "app": app,
        "state": {},
    }
    return Request(scope)


@pytest.fixture
def store(tmp_path):
    _store = SessionStore(str(tmp_path / "sessions.db"))
    yield _store
    _store.close()


@pytest.fixture
def graph():
    _graph = AsyncMock()
    _graph.run_graph_invoke.return_value = "answer"
    return _graph


class TestRunQueryContext:
    """chat_core.run_query assembles the per-run context dict."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def test_default_context_only_model_overrides(self, store, graph):
        """Without context_fields the context carries just the overrides."""
        await chat_core.run_query(
            _make_request(store, graph), query="q", user_id="u", chat_id="c"
        )
        graph.run_graph_invoke.assert_awaited_once_with(
            "q",
            "user_u:chat_c",
            extra_state=None,
            context={"model_overrides": {}},
        )

    async def test_stored_overrides_ride_context(self, store, graph):
        """Stored per-chat overrides reach the Runtime context (ADR-0033)."""
        store.create_chat("u", "c")
        store.set_override("u", "c", "chat", {"model": "ollama:qwen3"})

        await chat_core.run_query(
            _make_request(store, graph), query="q", user_id="u", chat_id="c"
        )
        graph.run_graph_invoke.assert_awaited_once_with(
            "q",
            "user_u:chat_c",
            extra_state=None,
            context={"model_overrides": {"chat": {"model": "ollama:qwen3"}}},
        )

    async def test_context_fields_merged_with_overrides(self, store, graph):
        """App-defined per-run fields ride alongside the overrides."""
        store.create_chat("u", "c")
        store.set_override("u", "c", "chat", {"model": "ollama:qwen3"})

        await chat_core.run_query(
            _make_request(store, graph),
            query="q",
            user_id="u",
            chat_id="c",
            context_fields={"locale": "en", "project_root": "/x"},
        )
        graph.run_graph_invoke.assert_awaited_once_with(
            "q",
            "user_u:chat_c",
            extra_state=None,
            context={
                "model_overrides": {"chat": {"model": "ollama:qwen3"}},
                "locale": "en",
                "project_root": "/x",
            },
        )

    async def test_stream_response_receives_context(self, store, graph):
        """stream_response forwards the same assembled context to the graph."""
        seen: dict = {}

        async def _capture(query, thread_id, *, extra_state=None, context=None):
            seen["context"] = context
            yield {"type": "complete", "message_for_user": "answer"}

        graph.run_graph_astream_events = _capture

        response = chat_core.stream_response(
            _make_request(store, graph),
            query="q",
            user_id="u",
            chat_id="c",
            context_fields={"project_root": "/y"},
        )
        chunks = [c async for c in response.body_iterator]
        assert any("complete" in str(c) for c in chunks)
        assert seen["context"] == {"model_overrides": {}, "project_root": "/y"}


class TestStreamResponseResume:
    """Resume and turn-persistence behaviour of ``stream_response``."""

    async def _collect(self, response):
        return [c async for c in response.body_iterator]

    async def test_requires_query_unless_resume(self, store, graph):
        """An empty query without ``resume`` is a 400."""
        with pytest.raises(HTTPException):
            chat_core.stream_response(
                _make_request(store, graph), query="  ", user_id="u", chat_id="c"
            )

    async def test_persists_user_row_on_start(self, store, graph):
        """The user turn is recorded when the run starts, even if it fails."""

        async def _boom(query, thread_id, *, extra_state=None, context=None):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        graph.run_graph_astream_events = _boom
        response = chat_core.stream_response(
            _make_request(store, graph), query="hello", user_id="u", chat_id="c"
        )
        chunks = await self._collect(response)

        assert any("error" in str(c) for c in chunks)
        messages = store.get_messages("u", "c")
        assert [m["role"] for m in messages] == ["user"]
        assert messages[0]["content"] == "hello"

    async def test_resume_invokes_with_none(self, store, graph):
        """A resume run passes ``None`` and writes no user row."""
        seen: dict = {}

        async def _capture(query, thread_id, *, extra_state=None, context=None):
            seen["query"] = query
            yield {"type": "complete", "message_for_user": "answer"}

        graph.run_graph_astream_events = _capture
        response = chat_core.stream_response(
            _make_request(store, graph), user_id="u", chat_id="c", resume=True
        )
        await self._collect(response)

        assert seen["query"] is None
        assert [m["role"] for m in store.get_messages("u", "c")] == ["assistant"]

    async def test_resume_with_nothing_is_not_resumable(self, store, graph):
        """An EmptyInputError on resume yields a non-resumable error frame."""
        from langgraph.errors import EmptyInputError

        async def _empty(query, thread_id, *, extra_state=None, context=None):
            raise EmptyInputError("no input")
            yield  # pragma: no cover

        graph.run_graph_astream_events = _empty
        response = chat_core.stream_response(
            _make_request(store, graph), user_id="u", chat_id="c", resume=True
        )
        chunks = await self._collect(response)

        assert any('"resumable": false' in str(c) for c in chunks)


class TestModelOverrideResolution:
    """chat_core.resolve_model_overrides merges layers + injects credentials."""

    def test_chat_override_wins_role_level(self, store):
        """A chat override replaces the session default for that role."""
        store.create_chat("u", "c")
        store.set_session_override("u", "chat", {"model": "openai:a"})
        store.set_override("u", "c", "chat", {"model": "openai:b"})
        graph = SimpleNamespace(llm_models={})
        result = chat_core.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"]["model"] == "openai:b"

    def test_inherits_session_default(self, store):
        """With no chat override, the session default applies."""
        store.set_session_override("u", "chat", {"model": "openai:a"})
        graph = SimpleNamespace(llm_models={})
        result = chat_core.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"]["model"] == "openai:a"

    def test_injects_credential_for_override(self, store):
        """A stored provider credential is injected as api_key."""
        store.set_session_override("u", "chat", {"model": "openai:gpt-4o"})
        store.set_credential("u", "openai", "", "sk-secret")
        graph = SimpleNamespace(llm_models={})
        result = chat_core.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"]["api_key"] == "sk-secret"

    def test_injects_credential_for_env_default(self, store):
        """A credential applies even without an override (env default model)."""
        graph = SimpleNamespace(
            llm_models={"chat": LLMModel(instance=None, model_name="openai:gpt-4o")}
        )
        store.set_credential("u", "openai", "", "sk-secret")
        result = chat_core.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"] == {"api_key": "sk-secret"}

    def test_skips_locked_role(self, store):
        """Locked roles are left to the graph/env (no user credential)."""
        store.set_session_override("u", "guard", {"model": "openai:gpt-4o"})
        store.set_credential("u", "openai", "", "sk-secret")
        graph = SimpleNamespace(
            llm_models={
                "guard": LLMModel(
                    instance=None, model_name="openai:gpt-4o", modifiable=False
                )
            }
        )
        result = chat_core.resolve_model_overrides(graph, store, "u", "c")
        assert result["guard"] == {"model": "openai:gpt-4o"}

    def test_migrate_legacy_overrides(self, store):
        """Legacy inline api_keys move to credentials and leave the override."""
        store.create_chat("u", "c")
        store.set_override(
            "u", "c", "chat", {"model": "openai:gpt-4o", "api_key": "sk-legacy"}
        )
        migrated = chat_core.migrate_legacy_overrides(store)
        assert migrated == 1
        assert store.get_overrides("u", "c")["chat"] == {"model": "openai:gpt-4o"}
        assert store.get_credential("u", "openai") == "sk-legacy"

    def test_migrate_keeps_existing_credential(self, store):
        """An existing provider credential is not overwritten by migration."""
        store.create_chat("u", "c")
        store.set_override(
            "u", "c", "chat", {"model": "openai:gpt-4o", "api_key": "sk-legacy"}
        )
        store.set_credential("u", "openai", "", "sk-new")
        chat_core.migrate_legacy_overrides(store)
        assert store.get_credential("u", "openai") == "sk-new"
        assert "api_key" not in store.get_overrides("u", "c")["chat"]
