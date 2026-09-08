#!/usr/bin/env python3
"""
Tests for the session-context projection endpoint.

``GET /chat/{user_id}/{chat_id}/context`` (ADR-0032) returns the
checkpointed session context projected by the app's ``context_snapshot``
hook.  These tests drive the endpoint with a fake graph so no LangGraph
or model setup is involved.

File: tests/test_api_context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport
from klea_utils.api.context import create_context_router
from klea_utils.api.sessions_db import SessionStore
from pydantic import BaseModel


class _StateValue(BaseModel):
    """Pydantic-backed state values, to exercise ``_normalise_state_snapshot``."""

    mode: dict


class _StateSnapshot:
    """Shapes like ``langgraph.types.StateSnapshot.values``."""

    def __init__(self, values):
        self.values = values


class _FakeCompiledGraph:
    """Shapes like ``CompiledStateGraph.aget_state``."""

    def __init__(self, values):
        self._values = values
        self.aget_state_calls = 0

    async def aget_state(self, config):
        self.aget_state_calls += 1
        return _StateSnapshot(self._values)


class _FakeCheckpointer:
    """Shapes like ``BaseCheckpointSaver.aget_tuple`` (None = thread never ran)."""

    def __init__(self, exists: bool):
        self._exists = exists
        self.aget_tuple_calls = 0

    async def aget_tuple(self, config):
        self.aget_tuple_calls += 1
        # The values come from the fake compiled graph, not this tuple; a
        # sentinel only signals "a checkpoint exists" vs "never ran".
        return object() if self._exists else None


class _FakeGraph:
    """Minimal ``BaseLangGraph`` stand-in exposing only the touched surface."""

    def __init__(self, *, checkpointer, compiled):
        self.checkpointer = checkpointer
        self.graph = compiled
        self.snapshot_states: list[dict] = []

    def context_snapshot(self, state: dict) -> dict | None:
        self.snapshot_states.append(state)
        mode = state.get("mode", {})
        return {
            "mode": mode.get("resolved", "general"),
            "requested": mode.get("requested", "general"),
            "note": mode.get("note", ""),
        }


@pytest.fixture
def app(tmp_path):
    """Minimal FastAPI app with the context router and a real SessionStore."""
    _app = FastAPI()
    _app.state.is_ready = True
    _app.state.chat_sessions = SessionStore(str(tmp_path / "sessions.db"))
    _app.include_router(create_context_router())
    yield _app
    _app.state.chat_sessions.close()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as _client:
        yield _client


class TestContextProjection:
    """``GET /chat/{user}/{chat}/context`` happy paths."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def _fake(self, *, checkpointer_exists=True, values) -> _FakeGraph:
        return _FakeGraph(
            checkpointer=_FakeCheckpointer(exists=checkpointer_exists),
            compiled=_FakeCompiledGraph(values=values),
        )

    async def test_projects_checkpointed_context(self, app, client):
        """A thread with a checkpoint projects via the app's hook."""
        values = {
            "mode": {
                "requested": "scientific",
                "resolved": "scientific",
                "note": "source ready",
            }
        }
        fake = self._fake(values=values)
        app.state.graph = fake

        resp = await client.get("/chat/alice/chat-1/context")
        self.logger.debug("status=%d body=%s", resp.status_code, resp.text)
        assert resp.status_code == 200
        assert resp.json() == {
            "context": {
                "mode": "scientific",
                "requested": "scientific",
                "note": "source ready",
            }
        }
        # The hook received the raw values dict, untouched by normalization.
        assert fake.snapshot_states == [values]
        assert fake.graph.aget_state_calls == 1

    async def test_pydantic_values_normalised_before_hook(self, app, client):
        """Pydantic-backed values are model_dump'd so the hook sees a dict."""
        fake = self._fake(
            values=_StateValue(
                mode={
                    "requested": "general",
                    "resolved": "general",
                    "note": "",
                }
            )
        )
        app.state.graph = fake

        resp = await client.get("/chat/alice/chat-1/context")
        assert resp.status_code == 200
        assert fake.snapshot_states == [
            {
                "mode": {
                    "requested": "general",
                    "resolved": "general",
                    "note": "",
                }
            }
        ]

    async def test_no_checkpoint_yet_returns_null(self, app, client):
        """A thread that never ran a query has no context (200 + null)."""
        fake = self._fake(checkpointer_exists=False, values={})
        app.state.graph = fake

        resp = await client.get("/chat/alice/fresh-chat/context")
        assert resp.status_code == 200
        assert resp.json() == {"context": None}
        # The compiled graph is never read for an untouched thread.
        assert fake.graph.aget_state_calls == 0

    async def test_no_checkpointer_returns_null(self, app, client):
        """A graph without a checkpointer (checkpoint="none") has no context."""
        fake = _FakeGraph(checkpointer=None, compiled=_FakeCompiledGraph(values={}))
        app.state.graph = fake

        resp = await client.get("/chat/alice/chat-1/context")
        assert resp.status_code == 200
        assert resp.json() == {"context": None}

    async def test_not_ready_returns_503(self, app, client):
        """The endpoint honours the shared service-readiness gate."""
        app.state.is_ready = False

        resp = await client.get("/chat/alice/chat-1/context")
        assert resp.status_code == 503
