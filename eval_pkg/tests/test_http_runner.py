# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the SSE parser and the HTTP runner adapter.

Every test drives hand-written fixtures or an in-memory transport, so the
suite makes no network call: the parser is fed plain line lists and the
adapter is given a stub transport that returns an async byte stream.  The one
live check is marked ``localonly`` and skips unless ``KLEA_EVAL_BASE_URL`` is
set.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from klea_eval.identity import derive_identity
from klea_eval.run_manager import RunContext
from klea_eval.runners.http import (
    HttpRunner,
    RunnerError,
    RunnerHTTPError,
    RunnerStreamError,
    RunnerTimeoutError,
    TurnOutcome,
    outcome_to_record,
)
from klea_eval.runners.sse import SSEEvent, parse_sse_lines

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sse"


@dataclass
class FakeTurn:
    """Minimal stand-in for a loaded case turn."""

    turn_number: int = 1
    role: str = "user"
    input: str = "Write notes.txt"


class _BodyStream(httpx.AsyncByteStream):
    """An async byte stream holding a whole fixture body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield self._body

    async def aclose(self) -> None:
        return None


class _StubTransport(httpx.AsyncBaseTransport):
    """An async transport that answers every request from a callable."""

    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self._handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        response = self._handler(request)
        response.request = request
        return response


def _fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _sse_response(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(
        status,
        stream=_BodyStream(text.encode("utf-8")),
        headers={"content-type": "text/event-stream"},
    )


def _stream_handler(
    text: str, status: int = 200
) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        return _sse_response(text, status=status)

    return handler


def _make_runner(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[HttpRunner, _StubTransport]:
    transport = _StubTransport(handler)
    runner = HttpRunner("http://test.local", transport=transport)
    return runner, transport


def _context(tmp_path: Path) -> RunContext:
    return RunContext(
        identity=derive_identity("case-1", "0"),
        harness_seed="0",
        workspace_root=tmp_path,
        run_dir=tmp_path / "run",
        fixture_base=tmp_path,
    )


def _run_turn(runner: HttpRunner, context: RunContext, turn: Any) -> TurnOutcome:
    return asyncio.run(runner.run_turn(context, turn))


def _frame(event: str, payload: dict[str, object]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload)}\n\n"


def test_parse_complete_fixture_keeps_event_order() -> None:
    events = list(parse_sse_lines(_fixture("complete_turn.sse").splitlines()))
    assert [event.event for event in events] == [
        "progress",
        "usage",
        "inspect",
        "tool",
        "complete",
    ]
    assert all(isinstance(event, SSEEvent) for event in events)
    assert events[0].payload == {
        "type": "progress",
        "node": "planner",
        "data": {"heading": "Planning the work"},
    }
    assert events[-1].payload == {
        "type": "complete",
        "message_for_user": "Done: wrote notes.txt.",
    }


def test_parse_skips_comments_and_joins_multiline_data() -> None:
    lines = [
        ": a comment",
        "id: 7",
        "retry: 100",
        "",
        "event: note",
        "data: first",
        "data: second",
        "",
        "",
    ]
    events = list(parse_sse_lines(lines))
    assert len(events) == 1
    assert events[0].event == "note"
    assert events[0].data == "first\nsecond"
    assert events[0].payload is None


def test_parse_comment_only_stream_yields_no_events() -> None:
    assert list(parse_sse_lines([": one", ": two"])) == []


def test_parse_truncated_fixture_yields_delivered_events() -> None:
    events = list(parse_sse_lines(_fixture("truncated_turn.sse").splitlines()))
    assert [event.event for event in events] == ["progress", "usage"]


def test_parse_tolerates_malformed_json_payload() -> None:
    events = list(parse_sse_lines(["event: note", "data: {not json}", ""]))
    assert len(events) == 1
    assert events[0].payload is None
    assert events[0].data == "{not json}"


def test_parse_dispatches_a_trailing_frame_without_a_blank_line() -> None:
    """A stream that ends after its last data line keeps that frame."""
    events = list(parse_sse_lines(["event: note", "data: hi"]))
    assert len(events) == 1
    assert events[0].event == "note"
    assert events[0].data == "hi"


def test_parse_drops_a_frame_cut_before_its_data_line() -> None:
    """A trailing frame that never carried data dispatches nothing."""
    events = list(parse_sse_lines(["event: note"]))
    assert events == []


def test_http_runner_posts_a_turn_to_the_stream_endpoint(tmp_path: Path) -> None:
    runner, transport = _make_runner(_stream_handler(_fixture("complete_turn.sse")))
    context = _context(tmp_path)
    _run_turn(runner, context, FakeTurn())

    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "http://test.local/query/stream"
    sent = json.loads(request.content)
    assert sent["run_id"] == context.run_id
    assert sent["user_id"] == context.user_id
    assert sent["chat_id"] == context.chat_id
    assert sent["query"] == "Write notes.txt"
    assert sent["role"] == "user"
    assert sent["turn_number"] == 1


def test_http_runner_buffers_a_complete_turn(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(_fixture("complete_turn.sse")))
    outcome = _run_turn(runner, _context(tmp_path), FakeTurn())

    assert [event.event for event in outcome.events] == [
        "progress",
        "usage",
        "inspect",
        "tool",
        "complete",
    ]
    assert outcome.text == "Done: wrote notes.txt."
    assert outcome.status == "completed"
    assert outcome.latency_ms >= 0.0
    assert outcome.usage == {
        "input_tokens": 120,
        "output_tokens": 40,
        "total_tokens": 160,
        "cached_tokens": 20,
        "reasoning_tokens": 0,
    }
    assert len(outcome.tool_calls) == 1
    assert outcome.tool_calls[0]["tool"] == "write_file"
    assert outcome.tool_calls[0]["meta"] == {}


def test_http_runner_sums_usage_details(tmp_path: Path) -> None:
    stream = (
        _frame(
            "usage",
            {
                "type": "usage",
                "data": {"details": {"input_tokens": 10, "output_tokens": 5}},
            },
        )
        + _frame(
            "usage",
            {
                "type": "usage",
                "data": {"details": {"input_tokens": 2, "output_tokens": 3}},
            },
        )
        + _frame("complete", {"type": "complete", "message_for_user": "ok"})
    )
    runner, _ = _make_runner(_stream_handler(stream))
    outcome = _run_turn(runner, _context(tmp_path), FakeTurn())
    assert outcome.usage == {
        "input_tokens": 12,
        "output_tokens": 8,
        "total_tokens": 20,
    }


def test_http_runner_ignores_ping(tmp_path: Path) -> None:
    stream = _frame("ping", {"type": "ping"}) + _frame(
        "complete", {"type": "complete", "message_for_user": "ok"}
    )
    runner, _ = _make_runner(_stream_handler(stream))
    outcome = _run_turn(runner, _context(tmp_path), FakeTurn())
    assert [event.event for event in outcome.events] == ["complete"]


def test_http_runner_raises_on_an_error_event(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(_fixture("error_turn.sse")))
    with pytest.raises(RunnerStreamError, match="run_command failed"):
        _run_turn(runner, _context(tmp_path), FakeTurn())


def test_http_runner_raises_on_an_interrupt_event(tmp_path: Path) -> None:
    stream = _frame(
        "interrupt",
        {"type": "interrupt", "node": "review", "data": {"kind": "review"}},
    )
    runner, _ = _make_runner(_stream_handler(stream))
    with pytest.raises(RunnerStreamError, match="human input"):
        _run_turn(runner, _context(tmp_path), FakeTurn())


def test_http_runner_requires_a_complete_event(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(_fixture("truncated_turn.sse")))
    with pytest.raises(RunnerStreamError, match="without a complete event"):
        _run_turn(runner, _context(tmp_path), FakeTurn())


def test_http_runner_reports_a_malformed_complete_payload(tmp_path: Path) -> None:
    stream = (FIXTURE_DIR.parent / "malformed_complete_turn.sse").read_text(
        encoding="utf-8"
    )
    runner, _ = _make_runner(_stream_handler(stream))
    with pytest.raises(RunnerStreamError) as excinfo:
        _run_turn(runner, _context(tmp_path), FakeTurn())
    assert str(excinfo.value).strip()


def test_http_runner_reports_a_truncated_complete_payload(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(_fixture("truncated_turn.sse")))
    with pytest.raises(RunnerStreamError) as excinfo:
        _run_turn(runner, _context(tmp_path), FakeTurn())
    assert str(excinfo.value).strip()


@pytest.mark.parametrize("status", [400, 503])
def test_http_runner_maps_an_error_status(tmp_path: Path, status: int) -> None:
    runner, _ = _make_runner(_stream_handler("boom", status=status))
    with pytest.raises(RunnerHTTPError) as excinfo:
        _run_turn(runner, _context(tmp_path), FakeTurn())
    assert excinfo.value.status_code == status
    assert "boom" in str(excinfo.value)


def test_http_runner_maps_a_timeout(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    runner, _ = _make_runner(handler)
    with pytest.raises(RunnerTimeoutError):
        _run_turn(runner, _context(tmp_path), FakeTurn())


def test_outcome_to_record_flattens_usage_and_counts_tool_errors() -> None:
    outcome = TurnOutcome(
        text="done",
        usage={"input_tokens": 3, "output_tokens": 2, "total_tokens": 5},
        tool_calls=(
            {"tool": "a", "meta": {"is_error": True}},
            {"tool": "b"},
        ),
        latency_ms=12.5,
    )
    record = outcome_to_record(outcome)
    assert record["output"] == "done"
    assert record["status"] == "completed"
    assert record["event_count"] == 0
    assert record["tool_error_count"] == 1
    assert record["input_tokens"] == 3
    assert record["usage"] == {
        "input_tokens": 3,
        "output_tokens": 2,
        "total_tokens": 5,
    }
    assert record["latency_ms"] == 12.5


def test_as_turn_executor_returns_a_metric_record(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(_fixture("complete_turn.sse")))
    turn: Any = FakeTurn()
    record = runner.as_turn_executor()(_context(tmp_path), turn)
    assert record is not None
    assert record["output"] == "Done: wrote notes.txt."
    assert record["status"] == "completed"
    assert record["event_count"] == 5
    assert record["input_tokens"] == 120
    assert record["output_tokens"] == 40
    assert record["total_tokens"] == 160
    assert record["usage"]["cached_tokens"] == 20
    assert record["tool_calls"][0]["tool"] == "write_file"
    assert record["tool_error_count"] == 0
    assert record["latency_ms"] >= 0.0


def test_as_turn_executor_rejects_a_running_loop(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(_fixture("complete_turn.sse")))
    executor = runner.as_turn_executor()
    context = _context(tmp_path)
    turn: Any = FakeTurn()

    async def call() -> None:
        executor(context, turn)

    with pytest.raises(RunnerError, match="running event loop"):
        asyncio.run(call())


def test_http_runner_leaves_an_injected_client_open(tmp_path: Path) -> None:
    async def scenario() -> None:
        transport = _StubTransport(_stream_handler(_fixture("complete_turn.sse")))
        client = httpx.AsyncClient(transport=transport)
        turn: Any = FakeTurn()
        try:
            runner = HttpRunner("http://test.local", client=client)
            await runner.run_turn(_context(tmp_path), turn)
            assert not client.is_closed
        finally:
            await client.aclose()

    asyncio.run(scenario())


def test_http_runner_validates_configuration() -> None:
    with pytest.raises(RunnerError):
        HttpRunner("")
    with pytest.raises(RunnerError):
        HttpRunner("http://test.local", timeout=0)
    runner = HttpRunner("http://test.local/", endpoint="query/stream", timeout=5)
    assert runner.url == "http://test.local/query/stream"
    assert runner.timeout == 5.0


def test_http_runner_requires_turn_input(tmp_path: Path) -> None:
    runner, _ = _make_runner(_stream_handler(""))
    turn: Any = FakeTurn(input="")
    with pytest.raises(RunnerError, match="carries no input text"):
        _run_turn(runner, _context(tmp_path), turn)


@pytest.mark.localonly
@pytest.mark.skipif(
    not os.environ.get("KLEA_EVAL_BASE_URL"),
    reason="needs KLEA_EVAL_BASE_URL pointing at a live server",
)
def test_live_turn_against_a_local_server(tmp_path: Path) -> None:
    turn: Any = FakeTurn(input="hello")
    runner = HttpRunner(os.environ["KLEA_EVAL_BASE_URL"])
    outcome = asyncio.run(runner.run_turn(_context(tmp_path), turn))
    assert outcome.status == "completed"
