#!/usr/bin/env python3
"""
Shared chat endpoint runners.

This module provides the *generic* plumbing the chat endpoints
(``/query`` and ``/query/stream``) all need: readiness checks, session
persistence, per-request model overrides, SSE framing, and error
handling.  The API contract itself is app-specific -- each app's
``api/chat.py`` defines its own ``ChatPayload`` model and its own
endpoint functions, wired through the runners here, so the router can
expose whichever fields the app contract needs (e.g. the agent's
``mode``) without growing this shared module.

The ``enrich`` hook lets an app inject app-level events into the SSE
stream (for example a ``context`` event carrying the agent operating
mode), while the shared framing stays here.

The ordered request/lifecycle view of this module is documented in
``devdocs/system/api-sse-sequence.md``; the emitted event catalogue is
``devdocs/system/streams.md``.  Keep both in sync when the contract changes.

Supporting helpers live in focused modules:
:mod:`klea_utils.api.chat_common` (app-state, thread identity, cancel),
:mod:`klea_utils.api.overrides` (model overrides/credentials), and
:mod:`klea_utils.api.hitl` (human-in-the-loop, ADR-0046).

File: klea_utils/api/chat_core.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import asyncio
import contextlib
import json
import logging
import traceback
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from klea_utils.api.chat_common import (
    _active_runs,
    _ensure_thread_free,
    _graph_and_store,
    thread_id_for,
)
from klea_utils.api.hitl import (
    _interrupt_event_question,
    _interrupt_question,
    _pending_interrupts,
    _prepare_chat_request,
)
from klea_utils.api.overrides import resolve_model_overrides
from klea_utils.api.sessions_db import SessionStore
from klea_utils.plogging import mask_sensitive

logger = logging.getLogger(__name__)

#: Idle gap, in seconds, after which :func:`_heartbeat` emits a ``ping`` SSE
#: frame.  A long-running tool (e.g. ``run_command``) emits no graph events
#: while it runs, so without a heartbeat the client's idle read timeout (300 s
#: in ``sse.py``) and intermediary proxies drop the stream.  15 s is well below
#: both.
HEARTBEAT_INTERVAL_SECONDS = 15.0


# ---------------------------------------------------------------------------
# SSE framing
# ---------------------------------------------------------------------------


def _error_frame(message: str, error_type: str, *, resumable: bool) -> str:
    """Return one SSE ``error`` frame, with a ``resumable`` hint.

    ``resumable`` tells the client it can retry the same run from the
    checkpoint (see the graph fault-tolerance note); it is false when there
    is nothing to resume.
    """
    return (
        "data: "
        + json.dumps(
            {
                "type": "error",
                "message": message,
                "error_type": error_type,
                "node": "",
                "resumable": resumable,
            }
        )
        + "\n\n"
    )


async def _heartbeat(
    events: AsyncIterator[dict], interval: float
) -> AsyncGenerator[dict, None]:
    """Yield *events*, interleaving ``ping`` frames during idle gaps.

    The graph emits nothing while a long-running node (e.g. a tool) is
    executing, so the client's idle read timeout would drop the stream.  This
    wrapper keeps the connection warm: it reads the next graph event in a
    separate task and, when none arrives within *interval* seconds, yields a
    ``{"type": "ping"}`` frame before waiting again.

    The in-flight ``__anext__`` task is preserved across timeouts and never
    cancelled by the heartbeat itself (so the running node/tool is not
    interrupted); ``asyncio.wait`` is used rather than ``asyncio.wait_for``/
    ``asyncio.timeout``, which would cancel it.  On early close (client
    disconnect or Stop) the pending read is cancelled and the source generator
    is closed, so cancellation still propagates into the graph.

    :param events: The graph's (or ``enrich``-wrapped) event async iterator.
    :param interval: Seconds of inactivity before a ``ping`` is emitted.
    :yields: The source events, plus periodic ``ping`` events.
    """
    aiter = events.__aiter__()
    pending = asyncio.ensure_future(aiter.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({pending}, timeout=interval)
            if not done:
                logger.debug(
                    "SSE heartbeat: no event for %.1fs, emitting ping", interval
                )
                yield {"type": "ping"}
                continue
            try:
                event = pending.result()
            except StopAsyncIteration:
                break
            yield event
            pending = asyncio.ensure_future(aiter.__anext__())
    finally:
        if not pending.done():
            pending.cancel()
        with contextlib.suppress(asyncio.CancelledError, StopAsyncIteration):
            await pending
        aclose = getattr(aiter, "aclose", None)
        if aclose is not None:
            # Best-effort: never let a failing close mask the real outcome.
            with contextlib.suppress(Exception):
                await aclose()


# ---------------------------------------------------------------------------
# Endpoint runners
# ---------------------------------------------------------------------------


async def run_query(
    request: Request,
    *,
    query: str | None = None,
    user_id: str,
    chat_id: str,
    resume: bool = False,
    interrupt_response: Mapping[str, Any] | None = None,
    interrupt_cancel: bool = False,
    interrupt_id: str | None = None,
    extra_state: dict[str, Any] | None = None,
    context_fields: dict[str, Any] | None = None,
) -> str:
    """Run the graph via ``run_graph_invoke`` and persist the exchange.

    Applies the stored per-chat model overrides for the duration of the
    call (via the LangGraph Runtime context, ADR-0033), maps graph errors
    onto HTTP status codes, and writes the user turn when the run starts and
    the assistant reply on success (so a failed turn is recorded and a
    resume completes it without duplication).

    :param request: Request carrying ``app.state.graph`` / ``chat_sessions``
    :param query: User query text; required unless *resume* is true, and
        must be empty when *resume* is true
    :param user_id: Persistent user identifier
    :param chat_id: Chat conversation identifier
    :param resume: Resume the thread's last failed run from its checkpoint
        (invoked with ``None``; no user turn is written)
    :param interrupt_response: Answer a pending HITL interrupt (ADR-0046); the
        mapping is passed as ``Command(resume=...)`` and recorded as the user
        turn.
    :param interrupt_cancel: Cancel a pending HITL interrupt (a terminal run).
    :param interrupt_id: Optional id of the interrupt being answered; a
        mismatch with the pending interrupt is rejected as stale.
    :param extra_state: Optional app-specific initial state fields passed
        to the graph invocation (e.g. the agent's operating ``mode``
        request).  Ignored on resume.
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
    :raises HTTPException: 400 on a missing/extra query, an empty resume,
        or ``ValueError``; 503 on ``RuntimeError``; 500 on any other failure
    """
    # Lazy: BaseLangGraph is the base class for all graphs; EmptyInputError is
    # only needed when a resume finds nothing to continue.
    from langgraph.errors import EmptyInputError

    from klea_utils.graph.base import BaseLangGraph

    graph: BaseLangGraph
    store: SessionStore
    graph, store = _graph_and_store(request)
    thread_id = thread_id_for(user_id, chat_id)
    logger.debug(
        "run_query(user_id=%s chat_id=%s) thread=%s resume=%s context_fields=%s",
        user_id,
        chat_id,
        thread_id,
        resume,
        list((context_fields or {}).keys()),
    )

    _action, graph_input, user_turn = await _prepare_chat_request(
        graph,
        thread_id=thread_id,
        query=query,
        resume=resume,
        interrupt_response=interrupt_response,
        interrupt_cancel=interrupt_cancel,
        interrupt_id=interrupt_id,
    )

    store.create_chat(user_id, chat_id)
    if user_turn:
        # Record the user turn when the run starts (not only on success), so
        # a turn that fails mid-run is still visible and retryable.
        store.add_message(user_id, chat_id, "user", user_turn)

    # Per-run runtime context (ADR-0033): the framework provides the stored
    # per-chat ``model_overrides`` slice; apps may add their own fields via
    # ``context_fields``.  The plain dict is coerced/validated against the
    # app's ``context_schema`` at the graph boundary (passing a model
    # *instance* here would skip that coercion, so chat_core stays
    # agnostic -- apps that diverge override the node layer, not the
    # runner).
    overrides = resolve_model_overrides(graph, store, user_id, chat_id)
    context: dict[str, Any] = {
        **(context_fields or {}),
        "model_overrides": overrides or {},
    }
    logger.debug("run_query: assembled runtime context=%s", mask_sensitive(context))

    # Single-flight per thread: reject a second run while one is in flight,
    # then register this task so a concurrent cancel can reach it.  Cleared
    # once the run returns (or raises), so the thread is reusable.
    registry = _active_runs(request)
    _ensure_thread_free(registry, thread_id)
    task = asyncio.current_task()
    if task is not None:
        registry.register(thread_id, task)
    try:
        result = await graph.run_graph_invoke(
            graph_input,
            thread_id,
            extra_state=extra_state,
            context=context,
        )
        pending = await _pending_interrupts(graph, thread_id)
        if pending:
            # The run paused for human input: persist the question and return
            # it (there is no final answer yet).
            question = _interrupt_question(pending[0])
            store.add_message(user_id, chat_id, "assistant", question)
            logger.info(
                "run_query(user_id=%s chat_id=%s): paused for input",
                user_id,
                chat_id,
            )
            return question
        message = result if isinstance(result, str) else str(result)
        store.add_message(user_id, chat_id, "assistant", message)
        logger.info(
            "run_query(user_id=%s chat_id=%s): answer %d chars",
            user_id,
            chat_id,
            len(message),
        )
    except EmptyInputError as e:
        logger.warning(
            "run_query: resume requested but nothing to resume for thread=%s: %s",
            thread_id,
            e,
        )
        raise HTTPException(status_code=400, detail="Nothing to resume for this chat.")
    except ValueError as e:
        logger.warning(f"Bad request: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        logger.warning(f"Service not ready: {e}")
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:  # noqa: BLE001
        logger.error(f"{e}\n{traceback.format_exc()}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        registry.clear(thread_id)

    return message


async def stream_response(
    request: Request,
    *,
    query: str | None = None,
    user_id: str,
    chat_id: str,
    resume: bool = False,
    interrupt_response: Mapping[str, Any] | None = None,
    interrupt_cancel: bool = False,
    interrupt_id: str | None = None,
    enrich: Callable[[AsyncIterator[dict]], AsyncIterator[dict]] | None = None,
    extra_state: dict[str, Any] | None = None,
    context_fields: dict[str, Any] | None = None,
) -> StreamingResponse:
    """Return a ``/query/stream`` SSE response for the graph's events.

    Applies the stored per-chat model overrides for the duration of the
    stream and writes the user turn when the run starts and the assistant
    reply on the ``complete`` event, so a turn that fails mid-run is still
    recorded and can be retried.  Graph failures become ``error`` SSE events
    (with a ``resumable`` hint) instead of dropping the stream.

    :param request: Request carrying ``app.state.graph`` / ``chat_sessions``
    :param query: User query text; required unless *resume* is true, and
        must be empty when *resume* is true
    :param user_id: Persistent user identifier
    :param chat_id: Chat conversation identifier
    :param resume: Resume the thread's last failed run from its checkpoint.
        The graph is invoked with ``None`` (no query) and the failed node is
        re-run; no user turn is written.
    :param interrupt_response: Answer a pending HITL interrupt (ADR-0046); the
        mapping is passed as ``Command(resume=...)`` and recorded as the user
        turn.
    :param interrupt_cancel: Cancel a pending HITL interrupt (a terminal run).
    :param interrupt_id: Optional id of the interrupt being answered; a
        mismatch with the pending interrupt is rejected as stale.
    :param enrich: Optional async-generator wrapper applied to the raw
        ``run_graph_astream_events`` event stream before framing.  Apps
        use it to inject app-specific events (e.g. a ``context`` event
        with the operating mode) or filter events.  When
        ``None``, every graph event is emitted unchanged.
    :param extra_state: Optional app-specific initial state fields passed
        to the graph invocation (e.g. the agent's operating ``mode``
        request).  Ignored on resume.
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
    # Lazy: BaseLangGraph is the base class for all graphs; EmptyInputError is
    # only needed when a resume finds nothing to continue.
    from langgraph.errors import EmptyInputError

    from klea_utils.graph.base import BaseLangGraph

    graph: BaseLangGraph
    store: SessionStore
    graph, store = _graph_and_store(request)
    thread_id = thread_id_for(user_id, chat_id)
    logger.debug(
        "stream_response(user_id=%s chat_id=%s) thread=%s resume=%s context_fields=%s",
        user_id,
        chat_id,
        thread_id,
        resume,
        list((context_fields or {}).keys()),
    )

    _action, graph_input, user_turn = await _prepare_chat_request(
        graph,
        thread_id=thread_id,
        query=query,
        resume=resume,
        interrupt_response=interrupt_response,
        interrupt_cancel=interrupt_cancel,
        interrupt_id=interrupt_id,
    )

    store.create_chat(user_id, chat_id)
    if user_turn:
        # Record the user turn when the run starts (not on completion), so a
        # turn that fails mid-run is still visible and retryable; the
        # assistant row is written on ``complete``.
        store.add_message(user_id, chat_id, "user", user_turn)

    # Per-run runtime context (ADR-0033): same assembly as run_query -- the
    # framework's ``model_overrides`` slice plus any app ``context_fields``,
    # forwarded as a plain dict for boundary coercion/validation.
    overrides = resolve_model_overrides(graph, store, user_id, chat_id)
    context: dict[str, Any] = {
        **(context_fields or {}),
        "model_overrides": overrides or {},
    }
    logger.debug(
        "stream_response: assembled runtime context=%s", mask_sensitive(context)
    )

    # Single-flight per thread, checked before the response is returned so a
    # concurrent request gets a clean 409 rather than a second graph run.  The
    # task is registered inside ``event_stream`` (the generator runs in its
    # own task, which is the handle a cancel must reach) and cleared in its
    # ``finally``; ``register`` also adds a done-callback as a leak backstop.
    registry = _active_runs(request)
    _ensure_thread_free(registry, thread_id)

    async def event_stream():
        logger.debug("stream_response: starting event stream for thread=%s", thread_id)
        task = asyncio.current_task()
        if task is not None:
            registry.register(thread_id, task)
        try:
            raw_events = graph.run_graph_astream_events(
                graph_input,
                thread_id,
                extra_state=extra_state,
                context=context,
            )
            events = raw_events if enrich is None else enrich(raw_events)
            # Keep the SSE connection warm while a long-running node (e.g. a
            # tool) emits nothing, so the client's idle read timeout does not
            # drop the stream mid-task.
            events = _heartbeat(events, HEARTBEAT_INTERVAL_SECONDS)
            # ``aclosing`` guarantees the heartbeat wrapper (and the graph
            # generator beneath it) is closed on every exit path, including the
            # generator finalisation Starlette performs after a client
            # disconnect.
            async with contextlib.aclosing(events) as stream_events:
                async for event in stream_events:
                    t = event.get("type")
                    if t == "interrupt":
                        # The run paused for human input: persist the question
                        # so a reloaded chat shows it (the answer is a later
                        # user turn).
                        store.add_message(
                            user_id,
                            chat_id,
                            "assistant",
                            _interrupt_event_question(event),
                        )
                        logger.info(
                            "stream_response(user_id=%s chat_id=%s): paused for input",
                            user_id,
                            chat_id,
                        )
                    elif t == "complete":
                        store.add_message(
                            user_id,
                            chat_id,
                            "assistant",
                            event.get("message_for_user", ""),
                        )
                        logger.info(
                            "stream_response(user_id=%s chat_id=%s): complete",
                            user_id,
                            chat_id,
                        )
                    yield f"data: {json.dumps(event)}\n\n"
        except EmptyInputError as e:
            logger.warning(
                "stream_response: resume requested but nothing to resume for "
                "thread=%s: %s",
                thread_id,
                e,
            )
            yield _error_frame(
                "Nothing to resume for this chat.",
                type(e).__name__,
                resumable=False,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"{e}\n{traceback.format_exc()}")
            yield _error_frame(str(e), type(e).__name__, resumable=True)
        finally:
            registry.clear(thread_id)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
