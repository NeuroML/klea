#!/usr/bin/env python3
"""
Server API client shared by the Klea frontends.

Thin httpx wrappers over the backend's session / model / credential /
command endpoints, used by the web (NiceGUI) and terminal (Textual) clients.
All functions accept the *server_url* as their first argument so the
caller can point them at any running Klea backend without shared state.

File: klea_utils/ui/client.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Mapping
from datetime import datetime
from typing import Any

import httpx

from klea_utils.ui.state import MessageData, chats, ensure_chat
from klea_utils.ui.stream_events import replay_chat_events

logger = logging.getLogger(__name__)


async def fetch_commands(server_url: str) -> list[dict[str, Any]]:
    """Fetch the server-side session-command catalogue (ADR-0047).

    :param server_url: Base URL of the backend API server.
    :returns: The command metadata dicts, or ``[]`` on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"{server_url}/commands")
            if resp.status_code == 200:
                return resp.json().get("commands", [])
            logger.warning("GET /commands status=%d", resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to fetch commands: %s", e)
    return []


async def fetch_chat_context(
    server_url: str, user_id: str, chat_id: str
) -> dict[str, Any]:
    """Fetch a chat's session context and pending interrupt (ADR-0032/0046).

    Calls ``GET /chat/{user_id}/{chat_id}/context`` and returns the body
    (``{"context": {...}, "pending_interrupt": {...}, "usage": {...}}``), so a
    frontend can restore a chat's context, token totals, and re-present a
    paused HITL ask on load/switch.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: The context body, or ``{}`` on failure.
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/context"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GET %s status=%d", url, resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to fetch chat context: %s", e)
    return {}


async def fetch_chats(server_url: str, user_id: str) -> list[dict[str, Any]]:
    """Fetch the user's chats, most recently updated first.

    Calls ``GET /chat/{user_id}`` (the server orders by ``updated_at``
    descending), so the first entry is the "continue" target.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :returns: The chat metadata dicts, or ``[]`` on failure.
    """
    url = f"{server_url}/chat/{user_id}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GET %s status=%d", url, resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to fetch chats: %s", e)
    return []


async def fetch_chat_messages(
    server_url: str, user_id: str, chat_id: str
) -> list[dict[str, Any]]:
    """Fetch a chat's persisted messages.

    Calls ``GET /chat/{user_id}/{chat_id}/messages``.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: The message dicts, or ``[]`` on failure.
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/messages"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GET %s status=%d", url, resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to fetch chat messages: %s", e)
    return []


async def fetch_chat_events(
    server_url: str, user_id: str, chat_id: str
) -> list[dict[str, Any]]:
    """Fetch a chat's persisted UI events (inspection/status panes).

    Calls ``GET /chat/{user_id}/{chat_id}/events``; the frontends replay the
    events through the shared reducer to rebuild the panes on load
    (ADR-0032 update).

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param chat_id: Chat conversation identifier.
    :returns: The event dicts, or ``[]`` on failure.
    """
    url = f"{server_url}/chat/{user_id}/{chat_id}/events"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            if resp.status_code == 200:
                return resp.json()
            logger.warning("GET %s status=%d", url, resp.status_code)
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to fetch chat events: %s", e)
    return []


def server_message_to_data(message: Mapping[str, Any]) -> MessageData:
    """Convert a server message row to the frontend ``MessageData`` shape.

    A ``tool`` row is rebuilt from its stored ``metadata`` (``header`` /
    ``mime`` / ``data`` / ``meta`` / ``is_error``) so a reloaded chat renders
    the same blocks as the live stream; other roles map to ``user``/``agent``.

    :param message: A ``GET .../messages`` row (``role``, ``content``,
        ``metadata``, ``created_at``).
    :returns: A message dict for ``ChatData["messages"]``.
    """
    stamp = datetime.fromtimestamp(message["created_at"]).astimezone().strftime("%X")
    if message.get("role") == "tool":
        meta = message.get("metadata") or {}
        return {
            "text": message["content"],
            "stamp": stamp,
            "role": "tool",
            "header": meta.get("header", ""),
            "mime": meta.get("mime", ""),
            "data": meta.get("data", ""),
            "meta": meta.get("meta", {}),
            "is_error": bool(meta.get("is_error", False)),
        }
    return {
        "text": message["content"],
        "stamp": stamp,
        "role": "user" if message.get("role") == "user" else "agent",
        "header": "",
    }


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
                                server_message_to_data(msg) for msg in msg_resp.json()
                            ]
                    # Stored UI events rebuild the inspection/status panes
                    # (ADR-0032 update); replayed through the shared reducer so
                    # a reloaded chat matches the live session.
                    logger.debug("GET /chat/%s/%s/events", user_id, chat_id)
                    evt_resp = await client.get(
                        f"{server_url}/chat/{user_id}/{chat_id}/events"
                    )
                    if evt_resp.status_code == 200:
                        current_chat = chats.get(key)
                        if current_chat:
                            replay_chat_events(current_chat, evt_resp.json())
                    # Session context (the agent's operating mode etc.,
                    # ADR-0032) is a projection of checkpointed graph state.
                    # Restore it so the selector renders correctly before
                    # the first streamed query, without app-specific knowledge.
                    logger.debug("GET /chat/%s/%s/context", user_id, chat_id)
                    ctx_resp = await client.get(
                        f"{server_url}/chat/{user_id}/{chat_id}/context"
                    )
                    if ctx_resp.status_code == 200:
                        body = ctx_resp.json()
                        context = body.get("context")
                        if context:
                            current_chat = chats.get(key)
                            if current_chat:
                                # setdefault + update mirrors apply_stream_event,
                                # so streamed updates merge over hydrated data.
                                current_chat.setdefault("context", {}).update(context)
                                logger.debug(
                                    "hydrated context for %s: %s", key, context
                                )
                        # The checkpointed cumulative token totals (ADR-0032
                        # update) restore the session total without replaying
                        # the per-node ``usage`` events.
                        usage = body.get("usage")
                        if usage:
                            current_chat = chats.get(key)
                            if current_chat:
                                current_chat["token_usage"] = {
                                    "input_tokens": usage.get("input_tokens", 0),
                                    "output_tokens": usage.get("output_tokens", 0),
                                    "total_tokens": usage.get("total_tokens", 0),
                                }
                        # A paused thread re-presents its HITL ask (ADR-0046),
                        # so a reload renders the form instead of a dead chat.
                        pending = body.get("pending_interrupt")
                        if pending:
                            current_chat = chats.get(key)
                            if current_chat:
                                current_chat["interrupt"] = pending
                                current_chat["turn_status"] = {"kind": "awaiting_input"}
                                logger.debug("hydrated pending interrupt for %s", key)
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


async def set_session_model_override(
    server_url: str, user_id: str, role: str, payload: dict
) -> bool:
    """POST a per-session default model override for a role."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{server_url}/chat/{user_id}/models/overrides/{role}",
                json=payload,
            )
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to set default model override: %s", e)
        return False


async def clear_session_model_override(
    server_url: str, user_id: str, role: str
) -> bool:
    """DELETE the per-session default model override for a role."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{server_url}/chat/{user_id}/models/overrides/{role}",
            )
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to clear default model override: %s", e)
        return False


async def set_credential(
    server_url: str,
    user_id: str,
    provider: str,
    secret: str,
    endpoint: str = "",
) -> bool:
    """PUT a provider credential for the user. The secret is not logged."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.put(
                f"{server_url}/credentials/{user_id}",
                json={
                    "provider": provider,
                    "endpoint": endpoint,
                    "secret": secret,
                },
            )
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to set credential for provider=%s: %s", provider, e)
        return False


async def clear_credential(
    server_url: str, user_id: str, provider: str, endpoint: str = ""
) -> bool:
    """DELETE the stored credential for a provider(+endpoint)."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.delete(
                f"{server_url}/credentials/{user_id}/{provider}",
                params={"endpoint": endpoint},
            )
            return resp.status_code == 200
    except Exception as e:  # noqa: BLE001
        logger.warning("Failed to clear credential for provider=%s: %s", provider, e)
        return False
