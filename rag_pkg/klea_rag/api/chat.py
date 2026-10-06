#!/usr/bin/env python3
"""
RAG chat API contract.

This is the *RAG's* API contract for the chat endpoints.  It defines
the request payload and routes ``/query`` / ``/query/stream`` through
the shared ``klea_utils.api.chat_core`` plumbing (session persistence,
model overrides, SSE framing).  RAG-specific contract changes (if any)
live here, not in klea_utils (ADR-0031).

File: klea_rag/api/chat.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from fastapi import APIRouter, Request, Response
from klea_utils.api import chat_common, chat_core
from pydantic import BaseModel, Field


class ChatPayload(BaseModel):
    query: str = Field(..., min_length=1)
    chat_id: str = Field(..., pattern=r"^[^:]+$")
    user_id: str = Field(default="", pattern=r"^[^:]*$")


def create_chat_router() -> APIRouter:
    """Create an APIRouter with ``/query`` and ``/query/stream`` endpoints.

    The RAG's chat contract: request body is :class:`ChatPayload`,
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
        )
        return {"result": message}

    @router.post("/query/stream")
    async def query_stream(request: Request, payload: ChatPayload):
        return await chat_core.stream_response(
            request,
            query=payload.query,
            user_id=payload.user_id,
            chat_id=payload.chat_id,
        )

    @router.post("/query/cancel", status_code=204)
    async def query_cancel(
        request: Request, payload: chat_common.CancelPayload
    ) -> Response:
        """Cancel the chat's active run (idempotent; 204 always)."""
        chat_common.cancel_run(request, payload.user_id, payload.chat_id)
        return Response(status_code=204)

    return router
