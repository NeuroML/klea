#!/usr/bin/env python3
"""
Tests for the shared NiceGUI stream event application logic.

The :func:`apply_stream_event` function is pure (mutates the chat dict
only), so it is unit-tested without any NiceGUI rendering.

File: tests/test_stream_events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging

import httpx
import pytest
from klea_utils.api.sse import stream_events, stream_events_sync
from klea_utils.ui.web.nicegui.components.stream import (
    INSPECTOR_BUFFER_KEY,
    apply_stream_event,
)
from klea_utils.ui.web.nicegui.state import chats, ensure_chat


@pytest.fixture
def chat():
    """A fresh chat dict via the shared ``ensure_chat`` store."""
    chats.clear()
    return ensure_chat("test-user", "test-chat")


class TestApplyStreamEvent:
    """Unit tests for :func:`apply_stream_event`."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def test_progress_ignored(self, chat):
        """Progress events mutate nothing and return None."""
        result = apply_stream_event(chat, {"type": "progress", "node": "Planner"})
        assert result is None
        assert chat["messages"] == []
        assert chat["token_usage"]["total_tokens"] == 0

    def test_token_ignored(self, chat):
        """A token event mutates nothing."""
        assert apply_stream_event(chat, {"type": "token", "content": "hi"}) is None

    def test_inspect_buffers_inspector_entry(self, chat):
        """inspect events append to the inspector buffer."""
        result = apply_stream_event(
            chat,
            {
                "type": "inspect",
                "node": "Planner",
                "data": {
                    "heading": "Plan",
                    "summary": "Made a plan",
                    "details": {"steps": 3},
                    "timing_seconds": 1.2,
                },
            },
        )
        assert result == "inspect"
        assert len(chat[INSPECTOR_BUFFER_KEY]) == 1
        entry = chat[INSPECTOR_BUFFER_KEY][0]
        assert entry["heading"] == "Plan"
        assert entry["summary"] == "Made a plan"
        assert entry["details"] == {"steps": 3}
        assert entry["timing_seconds"] == 1.2
        assert entry["node"] == "Planner"

    def test_usage_accumulates_tokens(self, chat):
        """usage events accumulate token totals across events."""
        assert (
            apply_stream_event(
                chat,
                {
                    "type": "usage",
                    "node": "Planner",
                    "data": {
                        "details": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        }
                    },
                },
            )
            == "usage"
        )
        assert (
            apply_stream_event(
                chat,
                {
                    "type": "usage",
                    "node": "Answer",
                    "data": {
                        "details": {
                            "input_tokens": 2,
                            "output_tokens": 8,
                            "total_tokens": 10,
                        }
                    },
                },
            )
            == "usage"
        )
        assert chat["token_usage"] == {
            "input_tokens": 12,
            "output_tokens": 13,
            "total_tokens": 25,
        }

    def test_state_section_stored(self, chat):
        """state events store per-node status-pane sections."""
        result = apply_stream_event(
            chat,
            {
                "type": "state",
                "node": "Retrieve",
                "data": {
                    "heading": "Retrieval",
                    "display": "- 2 docs",
                    "summary": "",
                    "details": {},
                },
            },
        )
        assert result == "state"
        assert chat["state_sections"]["Retrieve"] == {
            "heading": "Retrieval",
            "display": "- 2 docs",
            "summary": "",
            "details": {},
            "preformatted": False,
        }

    def test_state_section_shared_key_updates_in_place(self, chat):
        """A non-empty key lets different nodes update one section in place."""
        apply_stream_event(
            chat,
            {
                "type": "state",
                "node": "Planning",
                "data": {
                    "heading": "Plan",
                    "summary": "1 step(s)",
                    "key": "plan",
                },
            },
        )
        apply_stream_event(
            chat,
            {
                "type": "state",
                "node": "Evaluating",
                "data": {
                    "heading": "Plan",
                    "summary": "2 step(s)",
                    "key": "plan",
                },
            },
        )
        assert list(chat["state_sections"].keys()) == ["plan"]
        assert chat["state_sections"]["plan"]["summary"] == "2 step(s)"

    def test_state_section_carries_preformatted(self, chat):
        """A preformatted flag is preserved so the pane can render <pre>."""
        apply_stream_event(
            chat,
            {
                "type": "state",
                "node": "Planning",
                "data": {
                    "heading": "Plan",
                    "display": "[*] Step 1: a",
                    "key": "plan",
                    "preformatted": True,
                },
            },
        )
        assert chat["state_sections"]["plan"]["preformatted"] is True

    def test_context_event_stored(self, chat):
        """context events store session context (e.g. the operating mode)."""
        result = apply_stream_event(
            chat,
            {"type": "context", "data": {"mode": "scientific", "assurance": "unknown"}},
        )
        assert result == "context"
        assert chat["context"] == {"mode": "scientific", "assurance": "unknown"}
        # Subsequent events merge, not replace.
        apply_stream_event(chat, {"type": "context", "data": {"note": "no source"}})
        assert chat["context"]["mode"] == "scientific"
        assert chat["context"]["note"] == "no source"

    def test_complete_appends_message(self, chat):
        """complete events append the final assistant message."""
        result = apply_stream_event(
            chat, {"type": "complete", "message_for_user": "Final answer"}
        )
        assert result == "complete"
        assert len(chat["messages"]) == 1
        text, stamp, is_user = chat["messages"][0]
        assert text == "Final answer"
        assert stamp
        assert is_user is False

    def test_unknown_type_ignored(self, chat):
        """Unknown event types mutate nothing and return None."""
        assert apply_stream_event(chat, {"type": "mystery"}) is None

    def test_error_action(self, chat):
        """error events map to the error action without mutation."""
        assert apply_stream_event(chat, {"type": "error", "message": "boom"}) == "error"
        assert chat["messages"] == []


def _sse_response() -> httpx.Response:
    """A one-event SSE response body."""
    return httpx.Response(
        200,
        text='data: {"type": "complete", "message_for_user": "ok"}\n\n',
    )


def _sse_transport(requests: list[httpx.Request]) -> httpx.MockTransport:
    """A MockTransport that records requests and serves :func:`_sse_response`."""

    def _handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return _sse_response()

    return httpx.MockTransport(_handler)


@pytest.fixture
def sse_transport(monkeypatch):
    """Patch the sse module's httpx clients with a recording MockTransport."""

    captured: list[httpx.Request] = []

    def _patch(client_cls: type):
        def _factory(*args, **kwargs):
            kwargs["transport"] = _sse_transport(captured)
            return client_cls(*args, **kwargs)

        return _factory

    monkeypatch.setattr(
        "klea_utils.api.sse.httpx.AsyncClient", _patch(httpx.AsyncClient)
    )
    monkeypatch.setattr("klea_utils.api.sse.httpx.Client", _patch(httpx.Client))
    return captured


class TestStreamEventsClient:
    """Unit tests for the SSE client (:func:`stream_events` / ``_sync``)."""

    async def test_extra_merged_into_body(self, sse_transport):
        """``extra`` fields are merged into the /query/stream POST body."""
        events = [
            e
            async for e in stream_events(
                "question", "chat-1", "http://backend", extra={"mode": "scientific"}
            )
        ]
        body = json.loads(sse_transport[0].content)
        assert body["query"] == "question"
        assert body["chat_id"] == "chat-1"
        assert body["user_id"] == ""
        assert body["mode"] == "scientific"
        assert events == [{"type": "complete", "message_for_user": "ok"}]

    async def test_no_extra_leaves_body_unchanged(self, sse_transport):
        """Without ``extra`` the body has only the base fields."""
        await stream_events("q", "c", "http://backend").__anext__()
        body = json.loads(sse_transport[0].content)
        assert body == {"query": "q", "chat_id": "c", "user_id": ""}

    def test_extra_merged_sync(self, sse_transport):
        """The synchronous variant merges ``extra`` the same way."""
        events = list(
            stream_events_sync(
                "question", "chat-1", "http://backend", extra={"mode": "scientific"}
            )
        )
        body = json.loads(sse_transport[0].content)
        assert body["mode"] == "scientific"
        assert events == [{"type": "complete", "message_for_user": "ok"}]
