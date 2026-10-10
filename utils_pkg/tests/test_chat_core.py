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

import asyncio
import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, Request
from klea_utils.api import chat_common, chat_core, overrides
from klea_utils.api.runs import ActiveRunRegistry
from klea_utils.api.sessions_db import SessionStore
from klea_utils.commands.common import Command as CommandSpec
from klea_utils.commands.common import CommandRegistry, Persists
from klea_utils.llm import LLMModel
from langgraph.types import Command


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


class _FakeInterrupt:
    """Minimal stand-in for ``langgraph.types.Interrupt``."""

    def __init__(self, value, id_):
        self.value = value
        self.id = id_


class _FakeTask:
    def __init__(self, interrupts):
        self.interrupts = tuple(interrupts)


class _FakeSnapshot:
    def __init__(self, interrupts):
        self.tasks = [_FakeTask(interrupts)] if interrupts else []


class _FakeCompiled:
    """A compiled-graph stand-in whose ``aget_state`` reports interrupts."""

    def __init__(self, interrupts=()):
        self.interrupts = list(interrupts)

    async def aget_state(self, config):
        return _FakeSnapshot(self.interrupts)


def _hitl_graph(*, before=(), after=None, result="answer"):
    """A graph whose checkpoint reports ``before`` and, after a run, ``after``."""
    graph = AsyncMock()
    graph.checkpointer = object()
    compiled = _FakeCompiled(before)
    graph.graph = compiled

    async def _run(*args, **kwargs):
        if after is not None:
            compiled.interrupts = list(after)
        return result

    graph.run_graph_invoke.side_effect = _run
    return graph, compiled


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

        response = await chat_core.stream_response(
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
            await chat_core.stream_response(
                _make_request(store, graph), query="  ", user_id="u", chat_id="c"
            )

    async def test_persists_user_row_on_start(self, store, graph):
        """The user turn is recorded when the run starts, even if it fails."""

        async def _boom(query, thread_id, *, extra_state=None, context=None):
            raise RuntimeError("boom")
            yield  # pragma: no cover

        graph.run_graph_astream_events = _boom
        response = await chat_core.stream_response(
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
        response = await chat_core.stream_response(
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
        response = await chat_core.stream_response(
            _make_request(store, graph), user_id="u", chat_id="c", resume=True
        )
        chunks = await self._collect(response)

        assert any('"resumable": false' in str(c) for c in chunks)

    async def test_rejects_query_with_resume(self, store, graph):
        """A resume must not carry a query."""
        with pytest.raises(HTTPException):
            await chat_core.stream_response(
                _make_request(store, graph),
                query="x",
                user_id="u",
                chat_id="c",
                resume=True,
            )


class TestStreamResponseHeartbeat:
    """``stream_response`` keeps the SSE connection warm with ``ping`` frames."""

    async def _collect(self, response):
        return [chunk async for chunk in response.body_iterator]

    @staticmethod
    def _frame_type(chunk: str) -> str:
        """Return the ``type`` of an SSE ``data:`` frame."""
        return json.loads(chunk.removeprefix("data: ").strip())["type"]

    async def test_ping_interleaved_during_idle_gap(self, store, graph, monkeypatch):
        """A slow node yields pings, then ``complete``; no event is lost."""
        monkeypatch.setattr(chat_core, "HEARTBEAT_INTERVAL_SECONDS", 0.05)

        async def _slow(query, thread_id, *, extra_state=None, context=None):
            yield {
                "type": "progress",
                "node": "Running tools",
                "data": {"heading": "Running tools"},
            }
            await asyncio.sleep(0.18)
            yield {"type": "complete", "message_for_user": "done"}

        graph.run_graph_astream_events = _slow
        response = await chat_core.stream_response(
            _make_request(store, graph), query="q", user_id="u", chat_id="c"
        )
        types = [self._frame_type(chunk) for chunk in await self._collect(response)]

        assert types[0] == "progress"
        assert types[-1] == "complete"
        assert types.count("ping") >= 2
        # The heartbeat adds frames but never drops or reorders the real events.
        assert [t for t in types if t != "ping"] == ["progress", "complete"]

    async def test_no_ping_when_events_are_prompt(self, store, graph, monkeypatch):
        """A node that completes quickly emits no heartbeat."""
        monkeypatch.setattr(chat_core, "HEARTBEAT_INTERVAL_SECONDS", 10.0)

        async def _fast(query, thread_id, *, extra_state=None, context=None):
            yield {"type": "complete", "message_for_user": "done"}

        graph.run_graph_astream_events = _fast
        response = await chat_core.stream_response(
            _make_request(store, graph), query="q", user_id="u", chat_id="c"
        )
        types = [self._frame_type(chunk) for chunk in await self._collect(response)]

        assert types == ["complete"]


class TestRunQueryResume:
    """Resume and turn-persistence behaviour of ``run_query`` (non-streaming)."""

    async def test_resume_invokes_with_none(self, store, graph):
        """A resume run passes ``None`` and writes no user row."""
        result = await chat_core.run_query(
            _make_request(store, graph), user_id="u", chat_id="c", resume=True
        )

        assert result == "answer"
        graph.run_graph_invoke.assert_awaited_once()
        assert graph.run_graph_invoke.await_args.args[0] is None
        assert [m["role"] for m in store.get_messages("u", "c")] == ["assistant"]

    async def test_persists_user_then_assistant(self, store, graph):
        """A successful run records the user turn and the answer."""
        await chat_core.run_query(
            _make_request(store, graph), query="hello", user_id="u", chat_id="c"
        )
        assert [m["role"] for m in store.get_messages("u", "c")] == [
            "user",
            "assistant",
        ]

    async def test_user_row_persisted_on_failure(self, store, graph):
        """A failed run still records the user turn."""
        graph.run_graph_invoke.side_effect = RuntimeError("boom")

        with pytest.raises(HTTPException):
            await chat_core.run_query(
                _make_request(store, graph), query="hello", user_id="u", chat_id="c"
            )

        assert [m["role"] for m in store.get_messages("u", "c")] == ["user"]

    async def test_requires_query_unless_resume(self, store, graph):
        """An empty query without ``resume`` is a 400."""
        with pytest.raises(HTTPException):
            await chat_core.run_query(
                _make_request(store, graph), query="", user_id="u", chat_id="c"
            )

    async def test_rejects_query_with_resume(self, store, graph):
        """A resume must not carry a query."""
        with pytest.raises(HTTPException):
            await chat_core.run_query(
                _make_request(store, graph),
                query="x",
                user_id="u",
                chat_id="c",
                resume=True,
            )

    async def test_resume_with_nothing_is_400(self, store, graph):
        """An EmptyInputError on resume is reported as a 400."""
        from langgraph.errors import EmptyInputError

        graph.run_graph_invoke.side_effect = EmptyInputError("no input")

        with pytest.raises(HTTPException) as excinfo:
            await chat_core.run_query(
                _make_request(store, graph), user_id="u", chat_id="c", resume=True
            )

        assert excinfo.value.status_code == 400


class TestHitlInterrupts:
    """HITL interrupt guard, resume and persistence (ADR-0046)."""

    async def _collect(self, response):
        return [chunk async for chunk in response.body_iterator]

    @staticmethod
    def _blocked() -> _FakeInterrupt:
        return _FakeInterrupt(
            {
                "kind": "input",
                "questions": [{"step_number": 1, "question": "which file?"}],
            },
            "i1",
        )

    async def test_plain_query_while_paused_is_409(self, store):
        """A new query is refused while the thread awaits an answer."""
        graph, _ = _hitl_graph(before=[self._blocked()], after=())
        with pytest.raises(HTTPException) as excinfo:
            await chat_core.run_query(
                _make_request(store, graph), query="hello", user_id="u", chat_id="c"
            )
        assert excinfo.value.status_code == 409
        graph.run_graph_invoke.assert_not_awaited()

    async def test_query_that_pauses_persists_the_question(self, store):
        """A run that pauses returns and persists the question."""
        graph, _ = _hitl_graph(before=(), after=[self._blocked()], result="")
        result = await chat_core.run_query(
            _make_request(store, graph), query="do x", user_id="u", chat_id="c"
        )
        assert result == "which file?"
        messages = store.get_messages("u", "c")
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "which file?"

    async def test_answer_resumes_with_command_and_writes_user_row(self, store):
        graph, _ = _hitl_graph(before=[self._blocked()], after=())
        await chat_core.run_query(
            _make_request(store, graph),
            user_id="u",
            chat_id="c",
            interrupt_response={"answers": ["a.txt"]},
        )
        sent = graph.run_graph_invoke.await_args.args[0]
        assert isinstance(sent, Command)
        assert sent.resume == {"action": "answer", "answers": ["a.txt"]}
        messages = store.get_messages("u", "c")
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "a.txt"

    async def test_review_answer_user_row_is_human_readable(self, store):
        """A review decision is persisted as prose, not the wire literal."""
        graph, _ = _hitl_graph(before=[self._blocked()], after=())
        await chat_core.run_query(
            _make_request(store, graph),
            user_id="u",
            chat_id="c",
            interrupt_response={"decision": "approve"},
        )
        messages = store.get_messages("u", "c")
        assert messages[0]["content"] == "Plan approved"

    async def test_cancel_resumes_with_cancel_sentinel(self, store):
        graph, _ = _hitl_graph(before=[self._blocked()], after=())
        await chat_core.run_query(
            _make_request(store, graph),
            user_id="u",
            chat_id="c",
            interrupt_cancel=True,
        )
        sent = graph.run_graph_invoke.await_args.args[0]
        assert sent.resume == {"action": "cancel"}
        assert [m["role"] for m in store.get_messages("u", "c")] == ["assistant"]

    async def test_stale_interrupt_id_is_409(self, store):
        graph, _ = _hitl_graph(before=[self._blocked()], after=())
        with pytest.raises(HTTPException) as excinfo:
            await chat_core.run_query(
                _make_request(store, graph),
                user_id="u",
                chat_id="c",
                interrupt_response={"answers": ["x"]},
                interrupt_id="stale",
            )
        assert excinfo.value.status_code == 409

    async def test_answer_without_pending_is_409(self, store):
        graph, _ = _hitl_graph(before=(), after=())
        with pytest.raises(HTTPException) as excinfo:
            await chat_core.run_query(
                _make_request(store, graph),
                user_id="u",
                chat_id="c",
                interrupt_response={"answers": ["x"]},
            )
        assert excinfo.value.status_code == 409

    async def test_stream_interrupt_persists_question(self, store):
        graph, _ = _hitl_graph(before=(), after=[])

        async def _stream(query, thread_id, *, extra_state=None, context=None):
            yield {
                "type": "interrupt",
                "node": "Awaiting input",
                "data": {
                    "kind": "input",
                    "questions": [{"step_number": 1, "question": "which file?"}],
                },
            }

        graph.run_graph_astream_events = _stream
        response = await chat_core.stream_response(
            _make_request(store, graph), query="do x", user_id="u", chat_id="c"
        )
        chunks = await self._collect(response)
        assert any('"type": "interrupt"' in str(chunk) for chunk in chunks)
        messages = store.get_messages("u", "c")
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[1]["content"] == "which file?"

    async def test_stream_answer_writes_user_row(self, store):
        graph, _ = _hitl_graph(before=[self._blocked()], after=[])
        seen: dict = {}

        async def _stream(query, thread_id, *, extra_state=None, context=None):
            seen["input"] = query
            yield {"type": "complete", "message_for_user": "done"}

        graph.run_graph_astream_events = _stream
        response = await chat_core.stream_response(
            _make_request(store, graph),
            user_id="u",
            chat_id="c",
            interrupt_response={"answers": ["a.txt"]},
        )
        await self._collect(response)
        assert isinstance(seen["input"], Command)
        messages = store.get_messages("u", "c")
        assert [m["role"] for m in messages] == ["user", "assistant"]
        assert messages[0]["content"] == "a.txt"
        assert messages[1]["content"] == "done"


class TestModelOverrideResolution:
    """overrides.resolve_model_overrides merges layers + injects credentials."""

    def test_chat_override_wins_role_level(self, store):
        """A chat override replaces the session default for that role."""
        store.create_chat("u", "c")
        store.set_session_override("u", "chat", {"model": "openai:a"})
        store.set_override("u", "c", "chat", {"model": "openai:b"})
        graph = SimpleNamespace(llm_models={})
        result = overrides.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"]["model"] == "openai:b"

    def test_inherits_session_default(self, store):
        """With no chat override, the session default applies."""
        store.set_session_override("u", "chat", {"model": "openai:a"})
        graph = SimpleNamespace(llm_models={})
        result = overrides.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"]["model"] == "openai:a"

    def test_injects_credential_for_override(self, store):
        """A stored provider credential is injected as api_key."""
        store.set_session_override("u", "chat", {"model": "openai:gpt-4o"})
        store.set_credential("u", "openai", "", "sk-secret")
        graph = SimpleNamespace(llm_models={})
        result = overrides.resolve_model_overrides(graph, store, "u", "c")
        assert result["chat"]["api_key"] == "sk-secret"

    def test_injects_credential_for_env_default(self, store):
        """A credential applies even without an override (env default model)."""
        graph = SimpleNamespace(
            llm_models={"chat": LLMModel(instance=None, model_name="openai:gpt-4o")}
        )
        store.set_credential("u", "openai", "", "sk-secret")
        result = overrides.resolve_model_overrides(graph, store, "u", "c")
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
        result = overrides.resolve_model_overrides(graph, store, "u", "c")
        assert result["guard"] == {"model": "openai:gpt-4o"}

    def test_migrate_legacy_overrides(self, store):
        """Legacy inline api_keys move to credentials and leave the override."""
        store.create_chat("u", "c")
        store.set_override(
            "u", "c", "chat", {"model": "openai:gpt-4o", "api_key": "sk-legacy"}
        )
        migrated = overrides.migrate_legacy_overrides(store)
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
        overrides.migrate_legacy_overrides(store)
        assert store.get_credential("u", "openai") == "sk-new"
        assert "api_key" not in store.get_overrides("u", "c")["chat"]


def _shared_request(store: SessionStore, graph) -> Request:
    """A fake request that keeps a single ``active_runs`` on its app state."""
    app = SimpleNamespace(
        state=SimpleNamespace(
            is_ready=True,
            graph=graph,
            chat_sessions=store,
            active_runs=ActiveRunRegistry(),
        )
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


class TestSingleFlightAndCancel:
    """A chat's thread is single-flight; an active run can be cancelled."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def test_concurrent_run_is_rejected_409(self, store, graph):
        """A second same-thread run while one is in flight -> 409."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow(*args, **kwargs):
            started.set()
            await release.wait()
            return "answer"

        graph.run_graph_invoke.side_effect = _slow
        request = _shared_request(store, graph)

        first = asyncio.create_task(
            chat_core.run_query(request, query="one", user_id="u", chat_id="c")
        )
        await started.wait()
        with pytest.raises(HTTPException) as excinfo:
            await chat_core.run_query(request, query="two", user_id="u", chat_id="c")
        assert excinfo.value.status_code == 409
        release.set()
        assert await first == "answer"

    async def test_run_clears_registry_after_completion(self, store, graph):
        """After a run completes the thread is free for the next query."""
        request = _shared_request(store, graph)
        await chat_core.run_query(request, query="one", user_id="u", chat_id="c")
        assert not request.app.state.active_runs.is_active("user_u:chat_c")
        await chat_core.run_query(request, query="two", user_id="u", chat_id="c")

    async def test_cancel_stops_run_and_thread_reusable(self, store, graph):
        """cancel_run cancels the active task; a later run starts fresh."""
        started = asyncio.Event()

        async def _slow(*args, **kwargs):
            started.set()
            await asyncio.sleep(10)
            return "answer"

        graph.run_graph_invoke.side_effect = _slow
        request = _shared_request(store, graph)
        first = asyncio.create_task(
            chat_core.run_query(request, query="one", user_id="u", chat_id="c")
        )
        await started.wait()
        assert chat_common.cancel_run(request, "u", "c") is True
        with pytest.raises(asyncio.CancelledError):
            await first

        # The cancelled turn persisted the user row but no assistant row.
        messages = store.get_messages("u", "c")
        assert [m["role"] for m in messages] == ["user"]

        # The thread is free and a fresh query runs to completion.
        graph.run_graph_invoke.side_effect = None
        graph.run_graph_invoke.return_value = "fresh"
        assert await chat_core.run_query(
            request, query="two", user_id="u", chat_id="c"
        ) == ("fresh")

    async def test_cancel_missing_run_is_noop(self, store, graph):
        """Cancelling a chat with no active run returns False."""
        request = _shared_request(store, graph)
        assert chat_common.cancel_run(request, "u", "nope") is False

    async def test_stream_guard_rejects_concurrent_run(self, store, graph):
        """A second streamed run for a busy thread -> 409 before streaming."""
        started = asyncio.Event()
        release = asyncio.Event()

        async def _events(query, thread_id, *, extra_state=None, context=None):
            started.set()
            await release.wait()
            yield {"type": "complete", "message_for_user": "answer"}

        graph.run_graph_astream_events = _events
        request = _shared_request(store, graph)

        response = await chat_core.stream_response(
            request, query="one", user_id="u", chat_id="c"
        )
        first_chunk: list[str | bytes | memoryview] = []

        async def _drain_one():
            async for chunk in response.body_iterator:
                first_chunk.append(chunk)
                break

        drain = asyncio.create_task(_drain_one())
        await started.wait()
        with pytest.raises(HTTPException) as excinfo:
            await chat_core.stream_response(
                request, query="two", user_id="u", chat_id="c"
            )
        assert excinfo.value.status_code == 409
        release.set()
        await drain
        assert "complete" in "".join(str(c) for c in first_chunk)

    async def test_stream_cancel_leaves_thread_reusable(self, store, graph):
        """A streamed run can be cancelled and the thread reused."""
        started = asyncio.Event()

        async def _events(query, thread_id, *, extra_state=None, context=None):
            started.set()
            await asyncio.sleep(10)
            yield {"type": "complete", "message_for_user": "answer"}

        graph.run_graph_astream_events = _events
        request = _shared_request(store, graph)

        response = await chat_core.stream_response(
            request, query="one", user_id="u", chat_id="c"
        )

        async def _drain():
            async for _ in response.body_iterator:
                pass

        task = asyncio.create_task(_drain())
        await started.wait()
        assert chat_common.cancel_run(request, "u", "c") is True
        with pytest.raises(asyncio.CancelledError):
            await task
        # No assistant row on cancel; thread is free for a new run.
        assert [m["role"] for m in store.get_messages("u", "c")] == ["user"]
        assert not request.app.state.active_runs.is_active("user_u:chat_c")


class TestCommandPersistMode:
    """``_command_persist_mode`` classifies a query for command persistence."""

    @staticmethod
    def _graph(persists: Persists = "checkpoint"):
        registry = CommandRegistry()
        registry.register(
            CommandSpec(name="mode", summary="", side="server", persists=persists)
        )
        return SimpleNamespace(command_registry=registry)

    def test_ephemeral_command(self):
        assert chat_core._command_persist_mode(self._graph(), "/mode") is False

    def test_message_command_persists(self):
        assert chat_core._command_persist_mode(self._graph("message"), "/mode") is True

    def test_plain_query_is_not_a_command(self):
        assert chat_core._command_persist_mode(self._graph(), "hello") is None

    def test_resume_is_not_a_command(self):
        assert chat_core._command_persist_mode(self._graph(), None) is None

    def test_app_without_a_registry(self):
        assert chat_core._command_persist_mode(SimpleNamespace(), "/mode") is None


class TestCommandPersistence:
    """A command turn writes no chat rows unless it persists messages."""

    async def _run(self, store, graph, query):
        registry = CommandRegistry()
        registry.register(
            CommandSpec(name="mode", summary="", side="server", persists="checkpoint")
        )
        registry.register(
            CommandSpec(name="init", summary="", side="server", persists="message")
        )
        graph.command_registry = registry
        return await chat_core.run_query(
            _make_request(store, graph), query=query, user_id="u", chat_id="c"
        )

    async def test_ephemeral_command_writes_no_rows(self, store, graph):
        await self._run(store, graph, "/mode general")
        assert store.get_messages("u", "c") == []

    async def test_message_command_writes_rows(self, store, graph):
        await self._run(store, graph, "/init")
        assert [m["role"] for m in store.get_messages("u", "c")] == [
            "user",
            "assistant",
        ]

    async def test_plain_query_still_writes_rows(self, store, graph):
        await self._run(store, graph, "hello")
        assert [m["role"] for m in store.get_messages("u", "c")] == [
            "user",
            "assistant",
        ]
