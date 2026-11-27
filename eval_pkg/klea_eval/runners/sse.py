# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""SSE framing: turn an event-stream line stream into ordered events.

The module owns the Server-Sent Events grammar the harness needs and nothing
else: it never opens a connection, so the ordinary tests drive it from
hand-written fixtures (D2) while the HTTP adapter
(:mod:`klea_eval.runners.http`) feeds it a live ``httpx`` response.

Parsing is deliberately tolerant.  A comment line (one starting with a colon)
is skipped, the ``event``/``id``/``retry`` fields are recognised but only
``data`` is accumulated, several ``data`` lines are joined with a newline, and
a blank line dispatches the frame.  A frame the stream ends without a
trailing blank line for is still dispatched when the parser closes, so a
well-formed final frame is never lost; a frame cut off before its ``data``
line carries no payload and is dropped, so the adapter can report the
missing terminal ``complete`` event itself.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

__all__ = [
    "SSEEvent",
    "SSEParseError",
    "iter_sse_events",
    "parse_sse_lines",
]

#: Prefix of a comment line, which carries no field.
COMMENT_PREFIX = ":"

#: Field naming the event type.
EVENT_FIELD = "event"

#: Field carrying the payload.
DATA_FIELD = "data"

#: Fields that are recognised and deliberately ignored.
IGNORED_FIELDS = frozenset({"id", "retry"})

#: First characters of a data payload worth parsing as JSON.
JSON_PREFIXES = ("{", "[")

#: Separator inserted between the data lines of one frame.
DATA_SEPARATOR = "\n"


class SSEParseError(Exception):
    """Raised when the parser is handed something that is not a line stream."""


@dataclass(frozen=True, slots=True)
class SSEEvent:
    """One dispatched Server-Sent Events frame.

    ``event`` is the ``event:`` field (``None`` when absent), ``data`` is the
    joined payload text and ``payload`` is the parsed JSON object when the data
    is a JSON object, else ``None``.
    """

    event: str | None = None
    data: str = ""
    payload: dict[str, Any] | None = None


class _FrameBuilder:
    """Incremental framing shared by the synchronous and async parsers."""

    __slots__ = ("_data", "_event", "_seen")

    def __init__(self) -> None:
        self._event: str | None = None
        self._data: list[str] = []
        self._seen = False

    def feed(self, raw: str) -> SSEEvent | None:
        """Consume one line and return the frame it completes, if any."""
        line = _strip_ending(raw)
        if not line:
            return self._dispatch()
        if line.startswith(COMMENT_PREFIX):
            logger.debug("sse: skipping a comment line")
            return None
        name, value = _split_field(line)
        if name == EVENT_FIELD:
            self._event = value.strip() or None
            self._seen = True
            return None
        if name == DATA_FIELD:
            self._data.append(value)
            self._seen = True
            return None
        if name in IGNORED_FIELDS:
            self._seen = True
            return None
        logger.debug(f"sse: ignoring unknown field {name!r}")
        return None

    def close(self) -> SSEEvent | None:
        """Dispatch the frame a stream ended without a blank line for."""
        if self._seen:
            logger.debug("sse: dispatching a trailing frame without a blank line")
        return self._dispatch()

    def _dispatch(self) -> SSEEvent | None:
        """Emit the buffered frame, if it carried any data line."""
        if not self._data:
            self._reset()
            return None
        event = SSEEvent(
            event=self._event,
            data=DATA_SEPARATOR.join(self._data),
            payload=_parse_payload(self._data),
        )
        self._reset()
        logger.debug(
            f"sse: {event.event = }, {len(event.data) = }, {event.payload is not None}"
        )
        return event

    def _reset(self) -> None:
        self._event = None
        self._data = []
        self._seen = False


def parse_sse_lines(lines: Iterable[str]) -> Iterator[SSEEvent]:
    """Yield the events of a synchronous line stream.

    ``lines`` is normally ``Path.read_text().splitlines()`` in the tests; no
    network is involved.  The generator never raises on a truncated stream.
    """
    builder = _FrameBuilder()
    for raw in lines:
        event = builder.feed(raw)
        if event is not None:
            yield event
    trailing = builder.close()
    if trailing is not None:
        yield trailing


async def iter_sse_events(response: httpx.Response) -> AsyncIterator[SSEEvent]:
    """Yield the events of a live ``httpx`` streaming response.

    The response must not have been read yet.  ``asyncio.CancelledError`` from
    a cancelled turn propagates untouched; transport failures are left to the
    caller, which owns the typed error mapping.
    """
    reader = getattr(response, "aiter_lines", None)
    if not callable(reader):
        raise SSEParseError("expected an httpx.Response with aiter_lines()")
    builder = _FrameBuilder()
    async for raw in reader():
        event = builder.feed(raw)
        if event is not None:
            yield event
    trailing = builder.close()
    if trailing is not None:
        yield trailing


def _strip_ending(raw: str) -> str:
    """Return one line without its trailing line ending."""
    if not isinstance(raw, str):
        raise SSEParseError(f"expected a string line, got {type(raw).__name__}")
    return raw.rstrip("\r\n")


def _split_field(line: str) -> tuple[str, str]:
    """Split a field line into its name and value, dropping one leading space."""
    name, separator, value = line.partition(":")
    if not separator:
        return name.strip(), ""
    value = value.removeprefix(" ")
    return name.strip(), value


def _parse_payload(data_lines: list[str]) -> dict[str, Any] | None:
    """Return the JSON object a data payload carries, or ``None``."""
    text = DATA_SEPARATOR.join(data_lines).strip()
    if not text or text[0] not in JSON_PREFIXES:
        return None
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        logger.debug("sse: keeping a non-JSON data payload as text")
        return None
    if not isinstance(parsed, dict):
        logger.debug("sse: keeping a non-object JSON payload as text")
        return None
    return parsed
