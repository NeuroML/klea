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

import asyncio
import json
import logging
import traceback
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from klea_utils.api.runs import ActiveRunRegistry
from klea_utils.api.sessions_db import SessionStore
from klea_utils.llm import credential_scope
from klea_utils.plogging import mask_sensitive

logger = logging.getLogger(__name__)


class CancelPayload(BaseModel):
    """Identity payload for ``POST /query/cancel`` (shared by both apps).

    Cancellation addresses a chat's thread, so it needs only the same
    ``(user_id, chat_id)`` identity the query payloads carry.
    """

    chat_id: str = Field(..., pattern=r"^[^:]+$")
    user_id: str = Field(default="", pattern=r"^[^:]*$")


def _active_runs(request: Request) -> ActiveRunRegistry:
    """Resolve the in-flight run registry from ``app.state``.

    :param request: The incoming request; ``request.app.state.active_runs``
        is populated by :func:`klea_utils.api.app.make_app`'s lifespan.
    :returns: The process's :class:`~klea_utils.api.runs.ActiveRunRegistry`.
    """
    registry = getattr(request.app.state, "active_runs", None)
    if registry is None:
        # Defensive: a hand-built test app may not run the shared lifespan.
        registry = ActiveRunRegistry()
        request.app.state.active_runs = registry
    return registry


def _ensure_thread_free(registry: ActiveRunRegistry, thread_id: str) -> None:
    """Reject a new run when the thread already has one in flight.

    A LangGraph checkpoint is a log, not a mutex: a second same-thread run
    would proceed concurrently and its checkpoint writes would race.  The
    registry makes a thread single-flight.

    :param registry: The process's active-run registry.
    :param thread_id: The checkpoint thread identifier.
    :raises HTTPException: 409 when a run is already active for the thread.
    """
    if registry.is_active(thread_id):
        logger.warning("Rejecting concurrent run for thread=%s", thread_id)
        raise HTTPException(
            status_code=409,
            detail="A run is already in progress for this chat.",
        )


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


def cancel_run(request: Request, user_id: str, chat_id: str) -> bool:
    """Cancel the active run for a chat's thread, if any.

    Idempotent: an absent or already-completed run is a no-op.  Cancelling
    the driving task raises ``CancelledError`` at the node's next ``await``,
    leaving the checkpoint resumable at the failed node; no assistant turn
    is persisted (only the ``complete`` event writes one).

    :param request: The incoming request; carries the active-run registry.
    :param user_id: Persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: True when a live run was found and cancellation requested.
    """
    registry = _active_runs(request)
    thread_id = thread_id_for(user_id, chat_id)
    cancelled = registry.cancel(thread_id)
    logger.info(
        "cancel_run(user_id=%s chat_id=%s) thread=%s cancelled=%s",
        user_id,
        chat_id,
        thread_id,
        cancelled,
    )
    return cancelled


def resolve_model_overrides(
    graph: Any, store: SessionStore, user_id: str, chat_id: str
) -> dict[str, dict[str, Any]]:
    """Assemble the per-run ``model_overrides`` slice.

    Merges the per-session default overrides with the per-chat overrides
    (role-level: a chat's role dict wins entirely), then injects the
    user's provider credential (looked up by the model's provider/endpoint
    scope) as ``api_key`` for every modifiable role with a resolved model.
    Locked roles are left to the graph/env so admin-managed models are not
    affected by per-user credentials.

    :param graph: The graph, whose ``llm_models`` supply the role set and
        locked (``modifiable=False``) flags, plus env-default models.
    :param store: Session store with the override and credential layers.
    :param user_id: Persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: The effective ``{role: config}`` for the runtime context.
    """
    session_overrides = store.get_session_overrides(user_id)
    chat_overrides = store.get_overrides(user_id, chat_id)

    # Role-level merge: a chat's role config replaces the session one.
    resolved: dict[str, dict[str, Any]] = {}
    for role in (*session_overrides, *chat_overrides):
        if role in chat_overrides:
            resolved[role] = dict(chat_overrides[role])
        elif role in session_overrides:
            resolved[role] = dict(session_overrides[role])

    llm_models = getattr(graph, "llm_models", None)
    if not isinstance(llm_models, Mapping):
        # Tests / graphs without a role table: no metadata to inject against.
        llm_models = {}

    for role in dict.fromkeys([*resolved, *llm_models]):
        role_entry = llm_models.get(role)
        if role_entry is not None and not getattr(role_entry, "modifiable", True):
            logger.debug(f"Skipping credential injection for locked {role = }")
            continue
        role_config = resolved.get(role)
        model_name = (role_config or {}).get("model") or (
            getattr(role_entry, "model_name", "") if role_entry else ""
        )
        if not model_name:
            continue
        scope = credential_scope(model_name)
        if not scope.provider:
            continue
        secret = store.get_credential(user_id, scope.provider, scope.endpoint or "")
        if not secret:
            continue
        if role_config is None:
            role_config = {}
            resolved[role] = role_config
        # Do not clobber a legacy inline key; the startup migration should
        # have moved it, but be conservative.
        role_config.setdefault("api_key", secret)
        store.touch_credential(user_id, scope.provider, scope.endpoint or "")
        logger.debug(f"Injected credential for {role = }\n{scope = }")

    return resolved


def migrate_legacy_overrides(store: SessionStore) -> int:
    """Move legacy per-chat plaintext ``api_key`` values into credentials.

    Pre-credential deployments stored the key inline in a chat's role
    override.  Move each into ``user_credentials`` (scoped to the model's
    provider) and strip it from the override, so the unused-key TTL
    applies and override payloads no longer carry secrets.  Returns the
    number of roles migrated.
    """
    migrated = 0
    for row in store.all_chat_overrides():
        user_id = row["user_id"]
        chat_id = row["chat_id"]
        for role, role_config in row["overrides"].items():
            if not isinstance(role_config, dict) or "api_key" not in role_config:
                continue
            secret = role_config.get("api_key")
            stripped = {
                key: value for key, value in role_config.items() if key != "api_key"
            }
            scope = credential_scope(role_config.get("model", ""))
            if scope.provider and secret:
                existing = store.get_credential(
                    user_id, scope.provider, scope.endpoint or ""
                )
                if existing is None:
                    store.set_credential(
                        user_id, scope.provider, scope.endpoint or "", secret
                    )
            if stripped:
                store.set_override(user_id, chat_id, role, stripped)
            else:
                store.clear_override(user_id, chat_id, role)
            migrated += 1
            logger.info(
                "Migrated legacy api_key: user=%s chat=%s role=%s",
                user_id,
                chat_id,
                role,
            )
    if migrated:
        logger.info("Migrate legacy overrides: %d role(s) migrated", migrated)
    return migrated


async def run_query(
    request: Request,
    *,
    query: str | None = None,
    user_id: str,
    chat_id: str,
    resume: bool = False,
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

    user_message = (query or "").strip()
    if resume and user_message:
        raise HTTPException(
            status_code=400, detail="query must be empty when resume is true"
        )
    if not resume and not user_message:
        raise HTTPException(
            status_code=400, detail="query is required unless resume is true"
        )

    store.create_chat(user_id, chat_id)
    if not resume:
        # Record the user turn when the run starts (not only on success), so
        # a turn that fails mid-run is still visible and retryable.
        store.add_message(user_id, chat_id, "user", user_message)

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
            None if resume else user_message,
            thread_id,
            extra_state=extra_state,
            context=context,
        )
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


def stream_response(
    request: Request,
    *,
    query: str | None = None,
    user_id: str,
    chat_id: str,
    resume: bool = False,
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

    user_message = (query or "").strip()
    if resume and user_message:
        raise HTTPException(
            status_code=400, detail="query must be empty when resume is true"
        )
    if not resume and not user_message:
        raise HTTPException(
            status_code=400, detail="query is required unless resume is true"
        )

    store.create_chat(user_id, chat_id)
    if not resume:
        # Record the user turn when the run starts (not on completion), so a
        # turn that fails mid-run is still visible and retryable; the
        # assistant row is written on ``complete``.
        store.add_message(user_id, chat_id, "user", user_message)

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
                None if resume else user_message,
                thread_id,
                extra_state=extra_state,
                context=context,
            )
            events = raw_events if enrich is None else enrich(raw_events)
            async for event in events:
                t = event.get("type")
                if t == "complete":
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
