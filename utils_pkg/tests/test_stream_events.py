#!/usr/bin/env python3
"""
Tests for the shared NiceGUI stream event application logic.

The :func:`apply_stream_event` function is pure (mutates the chat dict
only), so it is unit-tested without any NiceGUI rendering.

File: tests/test_stream_events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import json
import logging

import httpx
import pytest
from klea_utils.api.sse import (
    fetch_catalogue_models,
    fetch_catalogue_models_sync,
    fetch_catalogue_providers,
    request_cancel,
    stream_events,
    stream_events_sync,
)
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.stream import (
    apply_stream_event,
    stop_stream,
)
from klea_utils.ui.web.nicegui.state import chats, ensure_chat, interrupt_display


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

    def test_progress_with_heading_ignored(self, chat):
        """A retry progress (heading payload) still mutates nothing."""
        result = apply_stream_event(
            chat,
            {
                "type": "progress",
                "node": "Planner",
                "data": {"heading": "Planner (retry 1/2: timed out)"},
            },
        )
        assert result is None
        assert chat["inspector_entries"] == []

    def test_token_ignored(self, chat):
        """A token event mutates nothing."""
        assert apply_stream_event(chat, {"type": "token", "content": "hi"}) is None

    def test_inspect_appends_inspector_entry(self, chat):
        """inspect events append to inspector_entries (rendered live)."""
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
        assert len(chat["inspector_entries"]) == 1
        entry = chat["inspector_entries"][0]
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
        message = chat["messages"][0]
        assert message["text"] == "Final answer"
        assert message["stamp"]
        assert message["role"] == "agent"

    def test_tool_event_appends_display_blocks(self, chat):
        """tool events append one full-width block per renderable entry."""
        result = apply_stream_event(
            chat,
            {
                "type": "tool",
                "node": "Running tools",
                "data": {
                    "tools": [
                        {
                            "tool": "edit_file",
                            "title": "Edit file",
                            "mime": "text/x-diff",
                            "header": "Edit file: a.txt (+1/-0)",
                            "data": "+hello",
                            "meta": {"path": "a.txt"},
                            "display": "```diff\n+hello\n```",
                            "is_error": True,
                        },
                        {"tool": "noop", "data": "", "display": ""},
                    ]
                },
            },
        )
        assert result == "tool"
        assert len(chat["messages"]) == 1  # the empty-display entry is skipped
        block = chat["messages"][0]
        assert block["role"] == "tool"
        assert block["header"] == "Edit file: a.txt (+1/-0)"
        assert block["mime"] == "text/x-diff"
        assert block["data"] == "+hello"
        assert block["meta"] == {"path": "a.txt"}
        assert block["text"] == "```diff\n+hello\n```"
        assert block["is_error"] is True

    def test_unknown_type_ignored(self, chat):
        """Unknown event types mutate nothing and return None."""
        assert apply_stream_event(chat, {"type": "mystery"}) is None

    def test_ping_ignored(self, chat):
        """A server heartbeat ping mutates nothing and returns None."""
        result = apply_stream_event(chat, {"type": "ping"})
        assert result is None
        assert chat["messages"] == []

    def test_error_action(self, chat):
        """error events map to the error action without mutation."""
        assert apply_stream_event(chat, {"type": "error", "message": "boom"}) == "error"
        assert chat["messages"] == []

    def test_interrupt_stores_ask(self, chat):
        """interrupt events store the ask (question/id) on the chat."""
        result = apply_stream_event(
            chat,
            {
                "type": "interrupt",
                "node": "Awaiting input",
                "data": {
                    "kind": "input",
                    "questions": [{"step_number": 1, "question": "which file?"}],
                    "interrupt_id": "i1",
                },
            },
        )
        assert result == "interrupt"
        assert chat["interrupt"]["interrupt_id"] == "i1"
        assert chat["interrupt"]["questions"][0]["question"] == "which file?"
        assert chat["messages"] == []


class TestInterruptDisplay:
    """``interrupt_display`` renders the user turn for an interrupt answer."""

    def test_answers_joined(self):
        assert interrupt_display({"answers": ["a", "b"]}) == "a; b"

    def test_review_decision_with_feedback(self):
        assert (
            interrupt_display({"decision": "revise", "feedback": "use v2"})
            == "Revision requested: use v2"
        )

    def test_review_approval(self):
        assert interrupt_display({"decision": "approve"}) == "Plan approved"

    def test_cancel_and_empty(self):
        assert interrupt_display(None, cancel=True) == "(cancelled)"
        assert interrupt_display(None) == "(cancelled)"


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

    async def test_resume_omits_query_and_sets_flag(self, sse_transport):
        """On resume the body carries ``resume`` and drops the query."""
        await stream_events("q", "c", "http://backend", resume=True).__anext__()
        body = json.loads(sse_transport[0].content)
        assert body == {"chat_id": "c", "user_id": "", "resume": True}

    def test_resume_omits_query_and_sets_flag_sync(self, sse_transport):
        """The synchronous variant omits the query on resume too."""
        next(stream_events_sync("q", "c", "http://backend", resume=True))
        body = json.loads(sse_transport[0].content)
        assert body == {"chat_id": "c", "user_id": "", "resume": True}

    async def test_interrupt_answer_body(self, sse_transport):
        """An interrupt answer sends ``interrupt_response`` (and the id)."""
        await stream_events(
            "",
            "c",
            "http://backend",
            interrupt_response={"answers": ["a.txt"]},
            interrupt_id="i1",
        ).__anext__()
        body = json.loads(sse_transport[0].content)
        assert body == {
            "chat_id": "c",
            "user_id": "",
            "interrupt_response": {"answers": ["a.txt"]},
            "interrupt_id": "i1",
        }

    async def test_interrupt_cancel_body(self, sse_transport):
        """An interrupt cancel sends only the ``interrupt_cancel`` flag."""
        await stream_events(
            "", "c", "http://backend", interrupt_cancel=True
        ).__anext__()
        body = json.loads(sse_transport[0].content)
        assert body == {"chat_id": "c", "user_id": "", "interrupt_cancel": True}

    def test_interrupt_cancel_body_sync(self, sse_transport):
        """The synchronous variant sends the interrupt action too."""
        next(stream_events_sync("", "c", "http://backend", interrupt_cancel=True))
        body = json.loads(sse_transport[0].content)
        assert body == {"chat_id": "c", "user_id": "", "interrupt_cancel": True}

    async def test_interrupt_frame_is_yielded(self, monkeypatch):
        """The client surfaces the interrupt frame (question/id) unchanged."""
        frame = (
            'data: {"type": "interrupt", "node": "Awaiting input", "data": '
            '{"kind": "input", "questions": [{"step_number": 1, '
            '"question": "which file?"}], "interrupt_id": "i1"}}\n\n'
        )
        response = httpx.Response(200, text=frame)
        real_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda request: response)
            return real_client(*args, **kwargs)

        monkeypatch.setattr("klea_utils.api.sse.httpx.AsyncClient", _factory)
        events = [e async for e in stream_events("q", "c", "http://backend")]
        assert events == [
            {
                "type": "interrupt",
                "node": "Awaiting input",
                "data": {
                    "kind": "input",
                    "questions": [{"step_number": 1, "question": "which file?"}],
                    "interrupt_id": "i1",
                },
            }
        ]

    async def test_ping_frame_is_yielded(self, monkeypatch):
        """The client forwards a heartbeat ping frame unchanged."""
        frame = 'data: {"type": "ping"}\n\n'
        response = httpx.Response(200, text=frame)
        real_client = httpx.AsyncClient

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda request: response)
            return real_client(*args, **kwargs)

        monkeypatch.setattr("klea_utils.api.sse.httpx.AsyncClient", _factory)
        events = [e async for e in stream_events("q", "c", "http://backend")]
        assert events == [{"type": "ping"}]


@pytest.fixture
def catalogue_transport(monkeypatch):
    """Serve fake catalogue responses; ``status`` switches to an error reply."""

    state: dict = {"requests": [], "status": 200}

    def _handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        if state["status"] != 200:
            return httpx.Response(state["status"])
        provider = request.url.params.get("provider")
        if provider:
            return httpx.Response(
                200, json={"provider": provider, "models": ["gpt-4o"]}
            )
        return httpx.Response(200, json={"providers": ["custom", "ollama"]})

    def _patch(client_cls: type):
        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_handler)
            return client_cls(*args, **kwargs)

        return _factory

    monkeypatch.setattr(
        "klea_utils.api.sse.httpx.AsyncClient", _patch(httpx.AsyncClient)
    )
    monkeypatch.setattr("klea_utils.api.sse.httpx.Client", _patch(httpx.Client))
    return state


class TestCatalogueFetch:
    """Unit tests for the model catalogue fetch helpers."""

    async def test_providers(self, catalogue_transport):
        providers = await fetch_catalogue_providers("http://backend", "u1")
        assert providers == ["custom", "ollama"]
        assert (
            str(catalogue_transport["requests"][0].url)
            == "http://backend/chat/u1/models/catalogue"
        )

    async def test_models_for_provider(self, catalogue_transport):
        models = await fetch_catalogue_models("http://backend", "u1", "openai")
        assert models == ["gpt-4o"]
        assert catalogue_transport["requests"][0].url.params["provider"] == "openai"

    def test_models_sync(self, catalogue_transport):
        assert fetch_catalogue_models_sync("http://backend", "u1", "openai") == [
            "gpt-4o"
        ]

    async def test_error_returns_empty(self, catalogue_transport):
        """A failed call gives an empty list, so the fields stay free text."""
        catalogue_transport["status"] = 500
        assert await fetch_catalogue_providers("http://backend", "u1") == []
        assert await fetch_catalogue_models("http://backend", "u1", "openai") == []


class TestRequestCancel:
    """Unit tests for the cancel client (:func:`request_cancel`)."""

    async def test_posts_identity_to_cancel_endpoint(self, sse_transport):
        """request_cancel POSTs the chat identity and returns True on <400."""
        ok = await request_cancel("http://backend", "c1", "u1")
        assert ok is True
        request = sse_transport[0]
        assert str(request.url) == "http://backend/query/cancel"
        assert json.loads(request.content) == {"chat_id": "c1", "user_id": "u1"}

    async def test_error_status_returns_false(self, monkeypatch):
        """A 4xx/5xx response yields False so the caller can stop locally."""

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(
                lambda request: httpx.Response(500)
            )
            return httpx.AsyncClient(*args, **kwargs)

        monkeypatch.setattr("klea_utils.api.sse.httpx.AsyncClient", _factory)
        assert await request_cancel("http://backend", "c1", "u1") is False

    async def test_network_error_returns_false(self, monkeypatch):
        """A transport error is swallowed and reported as False."""

        def _raise(request):
            raise httpx.ConnectError("no route")

        def _factory(*args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_raise)
            return httpx.AsyncClient(*args, **kwargs)

        monkeypatch.setattr("klea_utils.api.sse.httpx.AsyncClient", _factory)
        assert await request_cancel("http://backend", "c1", "u1") is False


class TestStopStream:
    """Unit tests for :func:`stop_stream` (the UI Stop control handler)."""

    async def test_cancels_task_and_requests_server_cancel(self, monkeypatch):
        """A live stream task is cancelled and a server cancel is fired."""
        import klea_utils.ui.web.nicegui.components.stream as stream_mod

        calls: list[tuple] = []
        created: list = []

        async def _fake_request_cancel(server_url, chat_id, user_id):
            calls.append((server_url, chat_id, user_id))
            return True

        monkeypatch.setattr(stream_mod, "request_cancel", _fake_request_cancel)
        # Capture the scheduled coroutine instead of running it on a loop.
        monkeypatch.setattr(stream_mod.background_tasks, "create", created.append)

        started = asyncio.Event()

        async def _idle():
            started.set()
            await asyncio.sleep(10)

        ctx = PageContext(server_url="http://backend", user_id="u1", chat_id="chat-1")
        ctx.streaming_chat_id = "chat-1"
        task = asyncio.create_task(_idle())
        ctx.stream_task = task
        await started.wait()

        stop_stream(ctx)

        assert task.cancelled() or task.cancelling()
        with pytest.raises(asyncio.CancelledError):
            await task
        # Run the scheduled cancel coroutine and check its arguments.
        assert len(created) == 1
        await created[0]
        assert calls == [("http://backend", "chat-1", "u1")]

    async def test_noop_when_nothing_streaming(self, monkeypatch):
        """No task and no chat id: safe no-op, no server call."""
        import klea_utils.ui.web.nicegui.components.stream as stream_mod

        calls: list[tuple] = []

        async def _fake_request_cancel(*args):
            calls.append(args)
            return True

        monkeypatch.setattr(stream_mod, "request_cancel", _fake_request_cancel)
        monkeypatch.setattr(
            stream_mod.background_tasks, "create", lambda coro: coro.close()
        )

        ctx = PageContext(server_url="http://backend", user_id="")
        stop_stream(ctx)
        assert calls == []
