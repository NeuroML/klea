#!/usr/bin/env python3
"""
Tests for the Agent chat API router (app-owned chat contract).

File: tests/test_chat_api.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

# https://www.python-httpx.org/advanced/transports/#asgi-transport
# Talk to FastAPI in-process without a real server.
# httpx.AsyncClient is needed over TestClient so
# streaming responses can be consumed via
# ``client.stream()`` + ``aiter_lines()``.
from klea_agent.api.chat import create_chat_router
from klea_utils.api.sessions_db import SessionStore


@pytest.fixture
def app(tmp_path):
    """Create a minimal FastAPI app with mock graph and real SessionStore."""
    _app = FastAPI()
    _app.state.is_ready = True
    _app.state.chat_sessions = SessionStore(str(tmp_path / "sessions.db"))

    mock_graph = AsyncMock()
    mock_graph.run_graph_invoke.return_value = "mock answer"

    async def _astream_events(query, thread_id):
        yield {"type": "progress", "node": "Mocking"}
        yield {"type": "complete", "message_for_user": "mock answer"}

    mock_graph.run_graph_astream_events = _astream_events

    _app.state.graph = mock_graph
    _app.include_router(create_chat_router())
    yield _app
    _app.state.chat_sessions.close()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as _client:
        yield _client


class TestChat:
    """Chat endpoint tests for the agent chat contract."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def test_query(self, client, app):
        """POST /query calls run_graph_invoke and returns the result."""
        self.logger.info("POST /query with valid payload")
        response = await client.post(
            "/query",
            json={"query": "hello", "chat_id": "test-chat", "user_id": "test-user"},
        )
        self.logger.info(f"Status: {response.status_code}, body: {response.json()}")
        assert response.status_code == 200
        assert response.json() == {"result": "mock answer"}
        app.state.graph.run_graph_invoke.assert_awaited_once_with(
            "hello", "user_test-user:chat_test-chat"
        )
        self.logger.info("Verified run_graph_invoke was called with correct args")

    async def test_query_reuses_session(self, client, app):
        """Same chat_id/user_id does not raise."""
        self.logger.info("First request (creates session)")
        await client.post(
            "/query", json={"query": "first", "chat_id": "s1", "user_id": "u1"}
        )

        self.logger.info("Second request with same chat_id/user_id")
        resp2 = await client.post(
            "/query", json={"query": "second", "chat_id": "s1", "user_id": "u1"}
        )
        self.logger.info(f"Second request status: {resp2.status_code}")
        assert resp2.status_code == 200

    async def test_query_error_returns_500(self, app, client):
        """Graph error surfaces as an HTTP 500."""
        self.logger.info("Injecting error into run_graph_invoke")
        app.state.graph.run_graph_invoke.side_effect = Exception("boom")

        self.logger.info("POST /query with broken graph")
        response = await client.post(
            "/query",
            json={"query": "hello", "chat_id": "test", "user_id": "test-user"},
        )
        self.logger.info(f"Status: {response.status_code}")
        assert response.status_code == 500

    async def test_query_when_not_ready(self, app, client):
        """POST /query returns 503 before the server is ready."""
        app.state.is_ready = False
        response = await client.post(
            "/query",
            json={"query": "hello", "chat_id": "test", "user_id": "test-user"},
        )
        assert response.status_code == 503

    async def test_query_stream_events(self, client):
        """POST /query/stream yields SSE frames."""
        self.logger.info("POST /query/stream with valid payload")
        async with client.stream(
            "POST",
            "/query/stream",
            json={"query": "hello", "chat_id": "test-chat", "user_id": "test-user"},
        ) as response:
            assert response.status_code == 200
            content_type = response.headers["content-type"]
            self.logger.info(
                f"Stream status: {response.status_code}, content-type: {content_type}"
            )
            assert content_type.startswith("text/event-stream")

            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

        data_frames = [ln for ln in lines if ln.startswith("data: ")]
        self.logger.info(f"Got {len(data_frames)} data frames from stream")

        events = [json.loads(f[6:]) for f in data_frames]
        self.logger.info(f"First event: {events[0]}")
        self.logger.info(f"Last event: {events[-1]}")

        assert len(data_frames) >= 2
        assert events[0] == {"type": "progress", "node": "Mocking"}
        assert events[-1] == {"type": "complete", "message_for_user": "mock answer"}
        self.logger.info("Streaming events match expected structure")

    async def test_query_stream_error_yields_error_event(self, app, client):
        """Graph error during streaming yields an error SSE event."""
        self.logger.info("Injecting error into run_graph_astream_events")

        async def _broken_stream(query, thread_id):
            raise RuntimeError("stream broken")
            yield  # pragma: no cover

        app.state.graph.run_graph_astream_events = _broken_stream
        self.logger.debug("Replaced stream method with broken one")

        self.logger.info("POST /query/stream with broken graph")
        async with client.stream(
            "POST",
            "/query/stream",
            json={"query": "hello", "chat_id": "test", "user_id": "test-user"},
        ) as response:
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

        data_frames = [ln for ln in lines if ln.startswith("data: ")]
        events = [json.loads(f[6:]) for f in data_frames]
        self.logger.info(f"Got error event: {events[-1]}")
        assert events[-1] == {
            "type": "error",
            "message": "stream broken",
            "error_type": "RuntimeError",
            "node": "",
        }

    async def test_query_stream_error_not_stored(self, app, client):
        """Graph error during streaming yields an error event but nothing is persisted."""
        self.logger.info("Injecting error into run_graph_astream_events")

        async def _broken_stream(query, thread_id):
            raise RuntimeError("stream broken")
            yield  # pragma: no cover

        app.state.graph.run_graph_astream_events = _broken_stream

        self.logger.info("POST /query/stream with broken graph")
        async with client.stream(
            "POST",
            "/query/stream",
            json={"query": "hello", "chat_id": "err-chat", "user_id": "err-user"},
        ) as response:
            lines = []
            async for line in response.aiter_lines():
                lines.append(line)

        data_frames = [ln for ln in lines if ln.startswith("data: ")]
        events = [json.loads(f[6:]) for f in data_frames]
        assert events[-1]["type"] == "error"

        store: SessionStore = app.state.chat_sessions
        messages = store.get_messages("err-user", "err-chat")
        self.logger.info(f"Messages stored after error: {len(messages)}")
        assert len(messages) == 0

    async def test_query_error_not_stored(self, app, client):
        """Graph error on /query raises 500 and nothing is persisted."""
        self.logger.info("Injecting error into run_graph_invoke")
        app.state.graph.run_graph_invoke.side_effect = Exception("boom")

        self.logger.info("POST /query with broken graph")
        response = await client.post(
            "/query",
            json={"query": "hello", "chat_id": "err-chat", "user_id": "err-user"},
        )
        assert response.status_code == 500

        store: SessionStore = app.state.chat_sessions
        messages = store.get_messages("err-user", "err-chat")
        self.logger.info(f"Messages stored after error: {len(messages)}")
        assert len(messages) == 0
