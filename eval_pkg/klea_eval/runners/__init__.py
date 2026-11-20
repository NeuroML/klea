# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Runner adapters: SSE framing plus the HTTP turn driver.

:mod:`klea_eval.runners.sse` owns the Server-Sent Events framing: it turns the
``/query/stream`` byte stream into ordered events and never talks to a network.
:mod:`klea_eval.runners.http` owns the Execute stage's network surface
(ADR-0058, ADR-0060): one turn is one ``POST`` whose SSE response terminates at
``complete``/``error``/``interrupt`` (``devdocs/system/streams.md``).

Both modules stay offline-testable: hand-written fixtures drive the parser
(D2) and an injected ``httpx`` client or transport drives the adapter, so no
live server is needed for the ordinary tests.  Retry and backoff stay in
:mod:`klea_eval.run_manager`; the adapter only raises typed errors.
"""

from __future__ import annotations

import logging

from klea_eval.runners.http import (
    DEFAULT_ENDPOINT,
    DEFAULT_TIMEOUT_SECONDS,
    HttpRunner,
    RunnerError,
    RunnerHTTPError,
    RunnerStreamError,
    RunnerTimeoutError,
    TurnOutcome,
    outcome_to_record,
)
from klea_eval.runners.sse import (
    SSEEvent,
    SSEParseError,
    iter_sse_events,
    parse_sse_lines,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ENDPOINT",
    "DEFAULT_TIMEOUT_SECONDS",
    "HttpRunner",
    "RunnerError",
    "RunnerHTTPError",
    "RunnerStreamError",
    "RunnerTimeoutError",
    "SSEEvent",
    "SSEParseError",
    "TurnOutcome",
    "iter_sse_events",
    "outcome_to_record",
    "parse_sse_lines",
]
