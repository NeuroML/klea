#!/usr/bin/env python3
"""
Shared chat endpoint plumbing for Klea packages.

This module provides the *generic* plumbing the chat endpoints
(``/query`` and ``/query/stream``) all need: readiness checks, session
persistence, per-request model overrides, SSE framing, and error
handling.  The API contract itself is app-specific -- each app's
``api/chat.py`` defines its own ``ChatPayload`` model and its own
endpoint functions, wired through the helpers here, so the router can
expose whichever fields the app contract needs (e.g. the agent's
``mode``) without growing this shared module.

The ``enrich`` hook lets an app inject app-level events into the SSE
stream (for example a ``context`` event carrying the agent operating
mode), while the shared framing stays here.

File: klea_utils/api/chat_core.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import traceback
from collections.abc import AsyncIterator, Callable
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from klea_utils.api.sessions_db import SessionStore

logger = logging.getLogger(__name__)


def _graph_and_store(request: Request) -> tuple[Any, SessionStore]:
    """Resolve the graph and session store from ``app.state``.

    Raises HTTP 503 when the server has not finished starting up or the
    graph is missing (the lifespan built by
    :func:`klea_utils.api.app.make_app` populates both).

    :param request: The incoming request; ``request.app.state`` carries
        ``is_ready``, ``graph`` and ``chat_sessions``.
    :returns: ``(graph, SessionStore)``
    """
    if (
        not getattr(request.app.state, "is_ready", False)
        or not getattr(request.app.state, "graph", None)
        or getattr(request.app.state.graph, "graph", None) is None
    ):
        raise HTTPException(status_code=503, detail="Service not ready")
    return request.app.state.graph, request.app.state.chat_sessions


def thread_id_for(user_id: str, chat_id: str) -> str:
    """Return the checkpoint thread id for a ``{user_id}:{chat_id}`` pair."""
    return f"user_{user_id}:chat_{chat_id}"


async def run_query(
    request: Request,
    *,
    query: str,
    user_id: str,
    chat_id: str,
    extra_state: dict[str, Any] | None = None,
    context_fields: dict[str, Any] | None = None,
) -> str:
    """Run the graph via ``run_graph_invoke`` and persist the exchange.

    Applies the stored per-chat model overrides for the duration of the
    call (via the LangGraph Runtime context, ADR-0033), maps graph errors
    onto HTTP status codes, and writes the user query + assistant answer
    to the session store.

    :param request: Request carrying ``app.state.graph`` / ``chat_sessions``
    :param query: User query text
    :param user_id: Persistent user identifier
    :param chat_id: Chat conversation identifier
    :param extra_state: Optional app-specific initial state fields passed
        to the graph invocation (e.g. the agent's operating ``mode``
        request).
    :param context_fields: Optional app-defined per-run context fields that
        the plumbing forwards together with the framework-provided
        ``model_overrides`` slice (ADR-0033).  The assembled dict is
        coerced/validated against the app's registered ``context_schema``
        at the graph boundary; ``KleaRunContext`` is ``extra="allow"`` (or
        the app subclasses it for typed fields).  Apps wire frontend
        payload fields through this generic hook instead of forking
        ``chat_core``.
    :returns: The assistant's answer text

    :note: ``POST /query`` returns only the answer string; the session
        context (operating mode etc., ADR-0032) is not included.  It is
        not produced on the bare ``ainvoke`` path (``run_graph_invoke``)
        -- fetch it via ``/query/stream`` ``context`` events or the
        hydration endpoint ``GET /chat/{user_id}/{chat_id}/context``.
    :raises HTTPException: 400 on ``ValueError``, 503 on ``RuntimeError``,
        500 on any other failure
    """
    # Lazy: BaseLangGraph is the base class for all graphs.
    from klea_utils.graph.base import BaseLangGraph

    graph: BaseLangGraph
    store: SessionStore
    graph, store = _graph_and_store(request)
    thread_id = thread_id_for(user_id, chat_id)

    store.create_chat(user_id, chat_id)
    # Per-run runtime context (ADR-0033): the framework provides the stored
    # per-chat ``model_overrides`` slice; apps may add their own fields via
    # ``context_fields``.  The plain dict is coerced/validated against the
    # app's ``context_schema`` at the graph boundary (passing a model
    # *instance* here would skip that coercion, so chat_core stays
    # agnostic -- apps that diverge override the node layer, not the
    # runner).
    overrides = store.get_overrides(user_id, chat_id)
    context: dict[str, Any] = {
        **(context_fields or {}),
        "model_overrides": overrides or {},
    }
    try:
        result = await graph.run_graph_invoke(
            query, thread_id, extra_state=extra_state, context=context
        )
        message = result if isinstance(result, str) else str(result)
        store.add_message(user_id, chat_id, "user", query)
        store.add_message(user_id, chat_id, "assistant", message)
    except ValueError as e:
        logger.warning(f"Bad request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.warning(f"Service not ready: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"{e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))

    return message


def stream_response(
    request: Request,
    *,
    query: str,
    user_id: str,
    chat_id: str,
    enrich: Callable[[AsyncIterator[dict]], AsyncIterator[dict]] | None = None,
    extra_state: dict[str, Any] | None = None,
    context_fields: dict[str, Any] | None = None,
) -> StreamingResponse:
    """Return a ``/query/stream`` SSE response for the graph's events.

    Applies the stored per-chat model overrides for the duration of the
    stream, persists the user query + final assistant answer on the
    ``complete`` event, and converts graph failures into ``error`` SSE
    events instead of dropping the stream.

    :param request: Request carrying ``app.state.graph`` / ``chat_sessions``
    :param query: User query text
    :param user_id: Persistent user identifier
    :param chat_id: Chat conversation identifier
    :param enrich: Optional async-generator wrapper applied to the raw
        ``run_graph_astream_events`` event stream before framing.  Apps
        use it to inject app-specific events (e.g. a ``context`` event
        with the operating mode) or filter events.  When
        ``None``, every graph event is emitted unchanged.
    :param extra_state: Optional app-specific initial state fields passed
        to the graph invocation (e.g. the agent's operating ``mode``
        request).
    :param context_fields: Optional app-defined per-run context fields that
        the plumbing forwards together with the framework-provided
        ``model_overrides`` slice (ADR-0033).  The assembled dict is
        coerced/validated against the app's registered ``context_schema``
        at the graph boundary; ``KleaRunContext`` is ``extra="allow"`` (or
        the app subclasses it for typed fields).  Apps wire frontend
        payload fields through this generic hook instead of forking
        ``chat_core``.
    :returns: A :class:`fastapi.responses.StreamingResponse` SSE stream
    """
    # Lazy: BaseLangGraph is the base class for all graphs.
    from klea_utils.graph.base import BaseLangGraph

    graph: BaseLangGraph
    store: SessionStore
    graph, store = _graph_and_store(request)
    thread_id = thread_id_for(user_id, chat_id)

    store.create_chat(user_id, chat_id)
    # Per-run runtime context (ADR-0033): same assembly as run_query -- the
    # framework's ``model_overrides`` slice plus any app ``context_fields``,
    # forwarded as a plain dict for boundary coercion/validation.
    overrides = store.get_overrides(user_id, chat_id)
    context: dict[str, Any] = {
        **(context_fields or {}),
        "model_overrides": overrides or {},
    }

    async def event_stream():
        try:
            raw_events = graph.run_graph_astream_events(
                query, thread_id, extra_state=extra_state, context=context
            )
            events = raw_events if enrich is None else enrich(raw_events)
            async for event in events:
                t = event.get("type")
                if t == "complete":
                    store.add_message(user_id, chat_id, "user", query)
                    store.add_message(
                        user_id,
                        chat_id,
                        "assistant",
                        event.get("message_for_user", ""),
                    )
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:  # noqa: BLE001
            logger.error(f"{e}\n{traceback.format_exc()}")
            error_event = json.dumps(
                {
                    "type": "error",
                    "message": str(e),
                    "error_type": type(e).__name__,
                    "node": "",
                }
            )
            yield f"data: {error_event}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
