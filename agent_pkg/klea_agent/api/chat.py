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

from typing import Any, Literal

from fastapi import APIRouter, Request, Response
from klea_utils.api import chat_core
from klea_utils.mcp.access import AccessLevel
from pydantic import BaseModel, Field, model_validator


class ChatPayload(BaseModel):
    query: str = Field(
        default="",
        description="User query text; omit (leave empty) when resuming",
    )
    chat_id: str = Field(..., pattern=r"^[^:]+$")
    user_id: str = Field(default="", pattern=r"^[^:]*$")
    # Resume the chat's last failed run from its checkpoint instead of
    # starting a new turn: the graph is invoked with ``None`` (no query) and
    # the failed node re-runs.  A resume carries no query.
    resume: bool = Field(
        default=False,
        description="Resume the last failed run for this chat (no query)",
    )
    # Operating-mode request passed into the graph's initial state
    # (ADR-0030); the resolved mode comes back as a ``context``
    # event on the stream.
    mode: Literal["general", "scientific"] = Field(
        default="general",
        description="Requested operating mode (scientific requires a curated source)",
    )
    # Optional per-request tool access level (ADR-0037); ``None`` leaves the
    # app config ``general.access_level`` in charge.
    access_level: AccessLevel | None = Field(
        default=None,
        description="Tool access level override: 'read_only' or 'full' (ADR-0037)",
    )

    @model_validator(mode="after")
    def _validate_query(self) -> "ChatPayload":
        """Require a query unless resuming; a resume must not carry one."""
        text = self.query.strip()
        if self.resume:
            if text:
                raise ValueError("query must be empty when resume is true")
        elif not text:
            raise ValueError("query is required unless resume is true")
        return self


def _extra_state(payload: ChatPayload) -> dict[str, Any]:
    """Build the graph's initial-state extras from the payload.

    :param payload: The validated chat payload.
    :returns: Extra state fields passed to :class:`~klea_utils.graph.base.BaseLangGraph`
        invocation methods (``mode.requested``, optional ``access_level``).
    """
    extra: dict[str, Any] = {"mode": {"requested": payload.mode}}
    if payload.access_level is not None:
        extra["access_level"] = payload.access_level
    return extra


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
            resume=payload.resume,
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
            resume=payload.resume,
            extra_state=_extra_state(payload),
        )

    @router.post("/query/cancel", status_code=204)
    async def query_cancel(
        request: Request, payload: chat_core.CancelPayload
    ) -> Response:
        """Cancel the chat's active run (idempotent; 204 always)."""
        chat_core.cancel_run(request, payload.user_id, payload.chat_id)
        return Response(status_code=204)

    return router
