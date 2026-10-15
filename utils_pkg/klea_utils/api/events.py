#!/usr/bin/env python3
"""
Chat UI-event endpoint for hydration (ADR-0032 update).

The frontends replay a chat's stored UI events (the inspection/status pane
events persisted at stream time) to rebuild those panes on load, so a
reloaded chat matches the live session without a checkpoint replay.

File: klea_utils/api/events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from fastapi import APIRouter, HTTPException, Request

from klea_utils.api.sessions_db import SessionStore

logger = logging.getLogger(__name__)


def create_events_router() -> APIRouter:
    """Create an APIRouter for a chat's stored UI events.

    ``GET /chat/{user_id}/{chat_id}/events``
        Return the chat's persisted UI events (inspection/status pane
        events), oldest first, for the frontends to replay on hydration.
    """
    router = APIRouter(prefix="/chat", tags=["events"])

    @router.get("/{user_id}/{chat_id}/events")
    async def get_events(user_id: str, chat_id: str, request: Request):
        store: SessionStore = request.app.state.chat_sessions
        if not store.get_chat(user_id, chat_id):
            logger.warning("get_events(%s, %s): chat not found", user_id, chat_id)
            raise HTTPException(status_code=404, detail="Chat not found")
        events = store.get_chat_events(user_id, chat_id)
        logger.debug("get_events(%s, %s): %d event(s)", user_id, chat_id, len(events))
        return events

    return router
