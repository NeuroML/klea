#!/usr/bin/env python3
"""
Server API client for the NiceGUI frontend.

All functions accept the *server_url* as their first argument so the
caller can point them at any running Klea backend without shared state.

File: klea_utils/ui/web/nicegui/client.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from datetime import datetime

import httpx

from .state import chats, ensure_chat

logger = logging.getLogger(__name__)


async def hydrate_chats(server_url: str, user_id: str) -> None:
    """Fetch all chats and their messages from the server into the local state.

    Populates the in-memory ``chats`` dict with every conversation belonging
    to *user_id*, including the full message history for each chat.

    After this call the frontend can switch between any chat without
    additional server round-trips.  If the server has no data for this
    user yet the local store stays empty.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            logger.debug("GET /chat/%s", user_id)
            resp = await client.get(f"{server_url}/chat/{user_id}")
            logger.debug("status=%d", resp.status_code)
            if resp.status_code == 200:
                chats_from_server = resp.json()
                logger.debug(
                    "server returned %d chat(s): %s",
                    len(chats_from_server),
                    [c.get("chat_id") for c in chats_from_server],
                )
                for chat_data in chats_from_server:
                    chat_id = chat_data["chat_id"]
                    key = f"{user_id}:{chat_id}"
                    ensure_chat(user_id, chat_id)
                    chats[key]["name"] = chat_data.get("title", chat_id)
                    chats[key]["created"] = chat_data.get("created_at", 0)

                # Fetch messages for every chat returned by the server.
                for chat_data in chats_from_server:
                    chat_id = chat_data["chat_id"]
                    logger.debug("GET /chat/%s/%s/messages", user_id, chat_id)
                    msg_resp = await client.get(
                        f"{server_url}/chat/{user_id}/{chat_id}/messages"
                    )
                    if msg_resp.status_code == 200:
                        key = f"{user_id}:{chat_id}"
                        current_chat = chats.get(key)
                        if current_chat:
                            current_chat["messages"] = [
                                (
                                    msg["content"],
                                    datetime.fromtimestamp(msg["created_at"])
                                    .astimezone()
                                    .strftime("%X"),
                                    msg["role"] == "user",
                                )
                                for msg in msg_resp.json()
                            ]
                    # Session context (the agent's operating mode etc.,
                    # ADR-0032) is a projection of checkpointed graph state.
                    # Restore it so the badge/selector render correctly before
                    # the first streamed query, without app-specific knowledge.
                    logger.debug("GET /chat/%s/%s/context", user_id, chat_id)
                    ctx_resp = await client.get(
                        f"{server_url}/chat/{user_id}/{chat_id}/context"
                    )
                    if ctx_resp.status_code == 200:
                        context = ctx_resp.json().get("context")
                        if context:
                            current_chat = chats.get(key)
                            if current_chat:
                                # setdefault + update mirrors apply_stream_event,
                                # so streamed updates merge over hydrated data.
                                current_chat.setdefault("context", {}).update(context)
                                logger.debug(
                                    "hydrated context for %s: %s", key, context
                                )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to hydrate chats from server: %s", e)


async def create_chat_on_server(server_url: str, user_id: str, chat_id: str) -> None:
    """POST a new chat to the server so it persists."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            title = chats.get(f"{user_id}:{chat_id}", {}).get("name", chat_id)
            resp = await client.post(
                f"{server_url}/chat/{user_id}",
                json={"chat_id": chat_id, "title": title},
            )
            if resp.status_code == 200:
                chat_data = resp.json()
                ensure_chat(user_id, chat_id)
                chats[f"{user_id}:{chat_id}"]["name"] = chat_data.get("title", chat_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to create chat on server: %s", e)


async def delete_chat_on_server(server_url: str, user_id: str, chat_id: str) -> None:
    """DELETE the chat on the server."""
    logger.debug("DELETE /chat/%s/%s", user_id, chat_id)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(f"{server_url}/chat/{user_id}/{chat_id}")
            logger.debug("status=%d", resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to delete chat on server: %s", e)


async def rename_chat_on_server(
    server_url: str, user_id: str, chat_id: str, title: str
) -> None:
    """PATCH the chat title on the server."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.patch(
                f"{server_url}/chat/{user_id}/{chat_id}",
                json={"title": title},
            )
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to rename chat on server: %s", e)


async def set_model_override(
    server_url: str, user_id: str, chat_id: str, role: str, payload: dict
) -> bool:
    """POST a model override for a chat role."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{server_url}/chat/{user_id}/{chat_id}/models/overrides/{role}",
                json=payload,
            )
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to set model override: %s", e)
        return False


async def clear_model_override(
    server_url: str, user_id: str, chat_id: str, role: str
) -> bool:
    """DELETE the model override for a chat role."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{server_url}/chat/{user_id}/{chat_id}/models/overrides/{role}",
            )
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to clear model override: %s", e)
        return False
