#!/usr/bin/env python3
"""
Session-context projection endpoint.

Returns the session context for a chat -- the app-defined projection of
*checkpointed graph state* via :meth:`BaseLangGraph.context_snapshot`
(ADR-0032) -- so frontends can restore the mode selector on
hydration without waiting for the next streamed query.

Design: the mode lives in checkpointed graph state, which is the single
source of truth (ADR-0030 / ADR-0032).  This endpoint reads it from the
checkpoint directly (``graph.checkpointer`` / ``graph.graph``); nothing
is duplicated into the sessions database.  An unsent query is not a chat
yet, so a thread with no checkpoint simply reports ``null`` context --
the chat entity exists, only its projection is unset, so ``200`` with
``null`` is returned rather than a 404.

File: klea_utils/api/context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Annotated

from fastapi import APIRouter, Request
from langgraph.types import RunnableConfig
from pydantic import Field

from klea_utils.api.chat_core import _graph_and_store, thread_id_for
from klea_utils.graph.base import BaseLangGraph, _normalise_state_snapshot

logger = logging.getLogger(__name__)


def create_context_router() -> APIRouter:
    """Create an APIRouter exposing the graph-level ``context`` projection.

    ``GET /chat/{user_id}/{chat_id}/context``
        Return ``{"context": {...}}`` -- the checkpointed session context
        projected by the app's ``context_snapshot`` hook -- or
        ``{"context": null}`` when the thread has no checkpoint yet (a
        chat that never ran a query) or the graph uses no checkpointer.
    """
    router = APIRouter(prefix="/chat", tags=["context"])

    @router.get("/{user_id}/{chat_id}/context")
    async def get_chat_context(
        user_id: Annotated[str, Field(pattern=r"^[^:]*$")],
        chat_id: Annotated[str, Field(pattern=r"^[^:]+$")],
        request: Request,
    ):
        # ``_graph_and_store`` also enforces service readiness (503).
        graph: BaseLangGraph
        graph, _ = _graph_and_store(request)
        thread_id = thread_id_for(user_id, chat_id)
        logger.debug("get_chat_context(%s, %s): thread=%s", user_id, chat_id, thread_id)

        # No checkpointer (e.g. checkpoint="none") means there is no
        # checkpointed context to project -- fall through to null.
        if graph.checkpointer is None:
            logger.debug(
                "get_chat_context(%s, %s): graph has no checkpointer",
                user_id,
                chat_id,
            )
            return {"context": None}

        config: RunnableConfig = {"configurable": {"thread_id": thread_id}}
        # ``aget_tuple`` returns None for a thread that never ran.  That is
        # the "no context yet" case (an unsent query is not a chat yet), so
        # we surface null instead of projecting the state-schema defaults.
        checkpoint = await graph.checkpointer.aget_tuple(config)
        if checkpoint is None:
            logger.debug(
                "get_chat_context(%s, %s): no checkpoint yet", user_id, chat_id
            )
            return {"context": None}

        snapshot = await graph.graph.aget_state(config)
        # ``_normalise_state_snapshot`` keeps this identical to the stream
        # path (``run_graph_astream_events``), so the app hook always sees
        # a plain dict whatever the state backend renders.
        values = _normalise_state_snapshot(snapshot.values)
        context = graph.context_snapshot(values)
        logger.debug("get_chat_context(%s, %s): context=%s", user_id, chat_id, context)
        return {"context": context}

    return router
