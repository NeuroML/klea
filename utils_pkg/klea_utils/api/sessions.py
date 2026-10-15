#!/usr/bin/env python3
"""
Chat session CRUD endpoints.

NOTE: *user_id* is currently a browser-generated UUID taken from
the URL path.  For multi-user deployments this must be replaced with
an authenticated identity (JWT / OAuth) extracted from the request
context — otherwise any user can delete or rename another user's chats
by modifying the ``user_id`` in the URL.

File: klea_utils/api/sessions.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Annotated

import coolname
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from klea_utils.api.chat_common import cancel_run, thread_id_for
from klea_utils.api.sessions_db import SessionStore
from klea_utils.graph.base import BaseLangGraph

logger = logging.getLogger(__name__)


class CreateChatPayload(BaseModel):
    chat_id: str = Field(..., pattern=r"^[^:]+$")
    title: str = ""


class UpdateChatPayload(BaseModel):
    title: str


def create_sessions_router() -> APIRouter:
    """Create an APIRouter for chat session CRUD.

    Endpoints:

    ``GET /chat/{user_id}``
        List all chats for the user.

    ``POST /chat/{user_id}``
        Create a new chat.  Title is auto-generated from *coolname* if
        not provided.

    ``PATCH /chat/{user_id}/{chat_id}``
        Update a chat's metadata (title).

    ``DELETE /chat/{user_id}/{chat_id}``
        Remove a chat, its messages, and its LangGraph checkpoint history.

    ``DELETE /chat/{user_id}``
        Remove all chats, messages, and checkpoints for the user.
    """
    router = APIRouter(prefix="/chat", tags=["sessions"])

    @router.get("/{user_id}")
    async def list_chats(
        user_id: Annotated[str, Field(pattern=r"^[^:]*$")], request: Request
    ):
        store: SessionStore = request.app.state.chat_sessions
        chats = store.list_chats(user_id)
        logger.debug("list_chats(%s): %d chat(s)", user_id, len(chats))
        return chats

    @router.post("/{user_id}")
    async def create_chat(
        user_id: Annotated[str, Field(pattern=r"^[^:]*$")],
        payload: CreateChatPayload,
        request: Request,
    ):
        store: SessionStore = request.app.state.chat_sessions
        title = payload.title or coolname.generate_slug(2)
        store.create_chat(user_id, payload.chat_id, title)
        chat = store.get_chat(user_id, payload.chat_id)
        if chat is None:
            logger.error(
                "create_chat(%s, %s): store returned None after insert",
                user_id,
                payload.chat_id,
            )
            raise HTTPException(status_code=500, detail="Failed to create chat")
        logger.debug(
            "create_chat(%s, %s, title=%r): OK", user_id, payload.chat_id, title
        )
        return chat

    @router.patch("/{user_id}/{chat_id}")
    async def update_chat(
        user_id: Annotated[str, Field(pattern=r"^[^:]*$")],
        chat_id: Annotated[str, Field(pattern=r"^[^:]+$")],
        payload: UpdateChatPayload,
        request: Request,
    ):
        store: SessionStore = request.app.state.chat_sessions
        if not store.get_chat(user_id, chat_id):
            logger.warning("update_chat(%s, %s): chat not found", user_id, chat_id)
            raise HTTPException(status_code=404, detail="Chat not found")
        store.rename_chat(user_id, chat_id, payload.title)
        logger.debug(
            "update_chat(%s, %s, title=%r): OK", user_id, chat_id, payload.title
        )
        return store.get_chat(user_id, chat_id)

    @router.delete("/{user_id}/{chat_id}")
    async def delete_chat(
        user_id: Annotated[str, Field(pattern=r"^[^:]*$")],
        chat_id: Annotated[str, Field(pattern=r"^[^:]+$")],
        request: Request,
    ):
        store: SessionStore = request.app.state.chat_sessions
        graph: BaseLangGraph = request.app.state.graph
        if not store.get_chat(user_id, chat_id):
            logger.warning("delete_chat(%s, %s): chat not found", user_id, chat_id)
            raise HTTPException(status_code=404, detail="Chat not found")
        # Stop any active run for this thread before purging, so a finishing
        # run cannot write a fresh checkpoint after the purge.
        cancel_run(request, user_id, chat_id)
        # Purge the chat's LangGraph checkpoint history too (otherwise the
        # append-only checkpoint DB keeps growing with every deleted chat).
        if graph.checkpointer:
            await graph.checkpointer.adelete_thread(thread_id_for(user_id, chat_id))
        store.delete_chat(user_id, chat_id)
        logger.debug("delete_chat(%s, %s): OK", user_id, chat_id)
        return {"status": "ok", "chat_id": chat_id}

    @router.delete("/{user_id}")
    async def delete_user(
        user_id: Annotated[str, Field(pattern=r"^[^:]*$")], request: Request
    ):
        store: SessionStore = request.app.state.chat_sessions
        graph: BaseLangGraph = request.app.state.graph

        # Purge LangGraph checkpoints for every chat thread.
        removed = 0
        for chat in store.list_chats(user_id):
            thread_id = thread_id_for(user_id, chat["chat_id"])
            if graph.checkpointer:
                await graph.checkpointer.adelete_thread(thread_id)
            removed += 1

        # Purge sessions + messages.
        store.delete_user_chats(user_id)
        logger.debug("delete_user(%s): removed %d chat(s)", user_id, removed)
        return {"status": "ok", "user_id": user_id, "removed": removed}

    return router
