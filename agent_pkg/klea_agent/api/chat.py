#!/usr/bin/env python3
"""
Agent chat API contract.

This is the *agent's* API contract for the chat endpoints.  It defines
the request payload and routes ``/query`` / ``/query/stream`` through
the shared ``klea_utils.api.chat_core`` plumbing (session persistence,
model overrides, SSE framing).  Agent-specific state (e.g. an operating
``mode`` field on the payload) will be added here, not in klea_utils
(ADR-0031).

File: klea_agent/api/chat.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Literal

from fastapi import APIRouter, Request
from klea_utils.api import chat_core
from pydantic import BaseModel, Field


class ChatPayload(BaseModel):
    query: str = Field(..., min_length=1)
    chat_id: str = Field(..., pattern=r"^[^:]+$")
    user_id: str = Field(default="", pattern=r"^[^:]*$")
    # Operating-mode request passed into the graph's initial state
    # (ADR-0030); the resolved mode comes back as a ``context``
    # event on the stream.
    mode: Literal["general", "scientific"] = Field(
        default="general",
        description="Requested operating mode (scientific requires a curated source)",
    )


def _extra_state(payload: ChatPayload) -> dict[str, dict[str, str]]:
    """Build the graph's initial-state extras from the payload.

    :param payload: The validated chat payload.
    :returns: Extra state fields passed to :class:`~klea_utils.graph.base.BaseLangGraph`
        invocation methods (``mode.requested``).
    """
    return {"mode": {"requested": payload.mode}}


def create_chat_router() -> APIRouter:
    """Create an APIRouter with ``/query`` and ``/query/stream`` endpoints.

    The agent's chat contract: request body is :class:`ChatPayload`,
    streaming events come from the shared ``klea_utils.api.chat_core``
    plumbing.  The router reads the graph instance and session store
    from ``request.app.state`` (set by :func:`klea_utils.api.app.make_app`).
    """
    router = APIRouter()

    @router.post("/query")
    async def query(request: Request, payload: ChatPayload):
        message = await chat_core.run_query(
            request,
            query=payload.query,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            extra_state=_extra_state(payload),
        )
        return {"result": message}

    @router.post("/query/stream")
    async def query_stream(request: Request, payload: ChatPayload):
        return chat_core.stream_response(
            request,
            query=payload.query,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
            extra_state=_extra_state(payload),
        )

    return router
