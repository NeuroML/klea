#!/usr/bin/env python3
"""
Shared chat request identity and app-state helpers.

These are the pieces every chat endpoint needs regardless of its contract:
resolving the graph/session store from ``app.state``, deriving a chat's
checkpoint thread id, the per-thread single-flight registry, and cancelling
an in-flight run.  The endpoint runners themselves live in
:mod:`klea_utils.api.chat_core`.

See ``devdocs/system/api-sse-sequence.md`` and ``devdocs/system/streams.md``.

File: klea_utils/api/chat_common.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, Field

from klea_utils.api.runs import ActiveRunRegistry
from klea_utils.api.sessions_db import SessionStore

logger = logging.getLogger(__name__)


class CancelPayload(BaseModel):
    """Identity payload for ``POST /query/cancel`` (shared by both apps).

    Cancellation addresses a chat's thread, so it needs only the same
    ``(user_id, chat_id)`` identity the query payloads carry.
    """

    chat_id: str = Field(..., pattern=r"^[^:]+$")
    user_id: str = Field(default="", pattern=r"^[^:]*$")


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
