# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""HTTP runner adapter: drive one evaluation turn over ``/query/stream``.

The adapter builds the per-turn request, consumes the SSE response through
:func:`klea_eval.runners.sse.iter_sse_events` and buffers the result into a
:class:`TurnOutcome`: the completion text, the ordered events, the summed token
usage, the collected tool-call entries and the wall-clock latency.

Decision record (P5): async ``httpx`` against ``/query/stream`` (D1), no
polling of a blocking endpoint, and retry/backoff left to the run manager.  The
run manager expects a synchronous ``TurnExecutor``, so :meth:`HttpRunner.
run_turn` is async and :meth:`HttpRunner.as_turn_executor` bridges it with
:func:`asyncio.run` in a thread that has no running loop.

Only the standard library and ``httpx`` are imported here; the module never
imports agent code, so a third-party agent exposing the same surface is
evaluable.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

import httpx

from klea_eval.cases import Turn
from klea_eval.run_manager import RunContext, TurnExecutor
from klea_eval.runners.sse import SSEEvent, iter_sse_events

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpRunner",
    "RunnerError",
    "RunnerHTTPError",
    "RunnerStreamError",
    "RunnerTimeoutError",
    "TurnOutcome",
    "outcome_to_record",
]

#: Path of the streaming endpoint a turn is posted to.
DEFAULT_ENDPOINT = "/query/stream"

#: Read timeout, in seconds, matching the client-side stream timeout.
DEFAULT_TIMEOUT_SECONDS = 300.0

#: Token counters summed from ``usage`` events.
_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cached_tokens",
    "reasoning_tokens",
)

#: Largest slice of an error body echoed in an exception message.
_ERROR_BODY_LIMIT = 500


class RunnerError(Exception):
    """Base class for failures raised by a runner adapter."""


class RunnerHTTPError(RunnerError):
    """Raised when the endpoint answers with a 4xx or 5xx status."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class RunnerStreamError(RunnerError):
    """Raised when a stream reports an error or ends without completing."""


class RunnerTimeoutError(RunnerError):
    """Raised when the endpoint does not answer within the read timeout."""


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """The buffered result of one executed turn."""

    text: str = ""
    events: tuple[SSEEvent, ...] = ()
    usage: dict[str, int] = field(default_factory=dict)
    tool_calls: tuple[dict[str, Any], ...] = ()
    latency_ms: float = 0.0
    status: str = "completed"
    error: str | None = None


class HttpRunner:
    """Drive one evaluation turn at a time against the agent endpoint."""

    def __init__(
        self,
        base_url: str,
        endpoint: str = DEFAULT_ENDPOINT,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        text = str(base_url or "").strip()
        if not text:
            raise RunnerError("base_url must be a non-empty string")

        self.base_url = text.rstrip("/")
        self.endpoint = _checked_endpoint(endpoint)
        self.timeout = _checked_timeout(timeout)
        self.headers: dict[str, str] = dict(headers) if headers else {}
        self._client = client
        self._transport = transport
        logger.debug(
            f"{self.base_url = }, {self.endpoint = }, {self.timeout = }, "
            f"{client = }, {transport = }"
        )

    @property
    def url(self) -> str:
        """The absolute endpoint URL one turn is posted to."""
        return f"{self.base_url}{self.endpoint}"

    async def run_turn(self, context: RunContext, turn: Turn) -> TurnOutcome:
        """Send one turn and buffer its streamed response.

        A client created here is closed here; an injected client belongs to its
        owner and is left open.  ``asyncio.CancelledError`` is never swallowed.
        """
        if not isinstance(context, RunContext):
            raise RunnerError(f"expected a RunContext, got {type(context).__name__}")

        payload = _build_payload(context, turn)
        owner = self._client is None
        client = self._client
        if client is None:
            client = httpx.AsyncClient(
                timeout=self.timeout,
                transport=self._transport,
                headers=self.headers or None,
            )

        started = time.perf_counter()
        try:
            outcome = await self._stream_turn(client, payload)
        finally:
            if owner:
                await client.aclose()

        elapsed = (time.perf_counter() - started) * 1000.0
        outcome = replace(outcome, latency_ms=round(elapsed, 3))
        logger.debug(
            f"{context.run_id = }, {turn.turn_number = }, "
            f"{outcome.latency_ms = }, {outcome.status = }, "
            f"{len(outcome.events) = }, {outcome.usage = }"
        )
        return outcome

    def as_turn_executor(self) -> TurnExecutor:
        """Return the synchronous turn executor the run manager expects."""

        def execute(context: RunContext, turn: Turn) -> Mapping[str, Any]:
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                pass
            else:
                raise RunnerError(
                    "as_turn_executor needs a thread without a running event loop"
                )
            outcome = asyncio.run(self.run_turn(context, turn))
            record = outcome_to_record(outcome)
            logger.debug(f"{context.run_id = }, {sorted(record) = }")
            return record

        return execute

    async def _stream_turn(
        self, client: httpx.AsyncClient, payload: Mapping[str, Any]
    ) -> TurnOutcome:
        """POST the turn, map transport failures and buffer the stream."""
        logger.debug(f"{self.url = }, {payload = }")
        try:
            async with client.stream(
                "POST", self.url, json=dict(payload), headers=self.headers or None
            ) as response:
                if response.status_code >= 400:
                    body = await _read_body(response)
                    raise RunnerHTTPError(
                        f"endpoint answered {response.status_code}: {body}",
                        response.status_code,
                    )
                return await _consume_stream(response)
        except httpx.TimeoutException as exc:
            raise RunnerTimeoutError(
                f"endpoint timed out after {self.timeout}s: {exc}"
            ) from exc
        except httpx.HTTPError as exc:
            raise RunnerError(f"endpoint request failed: {exc}") from exc


def outcome_to_record(outcome: TurnOutcome) -> dict[str, Any]:
    """Map a :class:`TurnOutcome` onto a run-manager metric record.

    The token counters are emitted both nested under ``usage`` and flat at the
    top level, so the metrics collector can read either shape.
    """
    record: dict[str, Any] = {
        "output": outcome.text,
        "status": outcome.status,
        "latency_ms": round(outcome.latency_ms, 3),
        "usage": dict(outcome.usage),
        "tool_calls": [dict(call) for call in outcome.tool_calls],
        "tool_error_count": sum(
            1 for call in outcome.tool_calls if _is_error_tool(call)
        ),
        "event_count": len(outcome.events),
    }
    record.update(outcome.usage)
    if outcome.error is not None:
        record["error"] = outcome.error
    return record


async def _consume_stream(response: httpx.Response) -> TurnOutcome:
    """Read one SSE stream into a buffered :class:`TurnOutcome`."""
    events: list[SSEEvent] = []
    text = ""
    usage: dict[str, int] = {}
    tool_calls: list[dict[str, Any]] = []
    terminal = False

    async for event in iter_sse_events(response):
        kind = _event_type(event)
        if kind == "ping":
            logger.debug("http_runner: skipping a heartbeat event")
            continue

        events.append(event)
        if kind == "complete":
            terminal = True
            text = _message_for_user(event)
        elif kind == "usage":
            _accumulate_usage(usage, event)
        elif kind == "tool":
            tool_calls.extend(_tool_entries(event))
        elif kind == "error":
            raise RunnerStreamError(_stream_error_message(event))
        elif kind == "interrupt":
            raise RunnerStreamError(
                "stream paused for human input; resuming is out of scope for P5"
            )

    if not terminal:
        raise RunnerStreamError("stream ended without a complete event")

    _derive_total(usage)
    logger.debug(f"{len(events) = }, {len(tool_calls) = }, {usage = }, {terminal = }")
    return TurnOutcome(
        text=text,
        events=tuple(events),
        usage=usage,
        tool_calls=tuple(tool_calls),
        status="completed",
    )


def _event_payload(event: SSEEvent) -> Mapping[str, Any]:
    """Return the event payload as a mapping, or an empty mapping."""
    payload = event.payload
    return payload if isinstance(payload, Mapping) else {}


def _event_type(event: SSEEvent) -> str:
    """Return the event type, from the ``event:`` field then the payload."""
    kind = (event.event or "").strip().lower()
    if kind:
        return kind
    raw = _event_payload(event).get("type")
    return str(raw).strip().lower() if raw else ""


def _message_for_user(event: SSEEvent) -> str:
    """Return the completion text carried by a ``complete`` event."""
    payload = _event_payload(event)
    value = payload.get("message_for_user")
    if isinstance(value, str):
        return value
    data = payload.get("data")
    if isinstance(data, Mapping):
        nested = data.get("message_for_user")
        if isinstance(nested, str):
            return nested
    return ""


def _stream_error_message(event: SSEEvent) -> str:
    """Return the message of an ``error`` event, with a fallback."""
    value = _event_payload(event).get("message")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "the endpoint reported an error event"


def _accumulate_usage(usage: dict[str, int], event: SSEEvent) -> None:
    """Sum the token counters of one ``usage`` event into ``usage``."""
    payload = _event_payload(event)
    data = payload.get("data")
    details = data.get("details") if isinstance(data, Mapping) else None
    source = details if isinstance(details, Mapping) else data
    if not isinstance(source, Mapping):
        return

    for key in _TOKEN_KEYS:
        value = source.get(key)
        if isinstance(value, bool) or not isinstance(value, int):
            continue
        usage[key] = usage.get(key, 0) + value


def _derive_total(usage: dict[str, int]) -> None:
    """Fill in ``total_tokens`` when only the two halves were reported."""
    if usage.get("total_tokens"):
        return
    total = usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
    if total:
        usage["total_tokens"] = total


def _tool_entries(event: SSEEvent) -> list[dict[str, Any]]:
    """Return the tool-call entries carried by one ``tool`` event."""
    payload = _event_payload(event)
    data = payload.get("data")
    tools = data.get("tools") if isinstance(data, Mapping) else None
    if not isinstance(tools, (list, tuple)):
        return []
    return [dict(entry) for entry in tools if isinstance(entry, Mapping)]


def _is_error_tool(call: Mapping[str, Any]) -> bool:
    """Return whether a collected tool entry records a failure."""
    if call.get("is_error"):
        return True
    meta = call.get("meta")
    return bool(isinstance(meta, Mapping) and meta.get("is_error"))


def _build_payload(context: RunContext, turn: Turn) -> dict[str, Any]:
    """Build the JSON body of one turn request."""
    payload: dict[str, Any] = {
        "run_id": context.run_id,
        "user_id": context.user_id,
        "chat_id": context.chat_id,
        "query": _turn_input(turn),
    }

    role = getattr(turn, "role", None)
    if isinstance(role, str) and role:
        payload["role"] = role

    number = getattr(turn, "turn_number", None)
    if isinstance(number, int) and not isinstance(number, bool):
        payload["turn_number"] = number

    return payload


def _turn_input(turn: Turn) -> str:
    """Return the text a turn asks the agent to act on."""
    for name in ("input", "content", "query"):
        value = getattr(turn, name, None)
        if isinstance(value, str) and value.strip():
            return value
    number = getattr(turn, "turn_number", "?")
    raise RunnerError(f"turn {number} carries no input text")


async def _read_body(response: httpx.Response) -> str:
    """Read and bound an error response body for an exception message."""
    try:
        raw = await response.aread()
    except httpx.HTTPError:
        return ""
    return raw.decode("utf-8", "replace").strip()[:_ERROR_BODY_LIMIT]


def _checked_endpoint(endpoint: str) -> str:
    """Return a leading-slash endpoint path."""
    text = str(endpoint or "").strip()
    if not text:
        raise RunnerError("endpoint must be a non-empty string")
    return text if text.startswith("/") else f"/{text}"


def _checked_timeout(timeout: float) -> float:
    """Return a positive timeout in seconds."""
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
        raise RunnerError("timeout must be a number of seconds")
    value = float(timeout)
    if value <= 0:
        raise RunnerError("timeout must be positive")
    return value
