#!/usr/bin/env python3
"""
SSE stream handling component for Klea pages.

Contains the pure state-mutation logic (:func:`apply_stream_event`,
unit-testable without NiceGUI) and the UI-driving coroutine
(:func:`run_stream`) that consumes the backend's ``/query/stream``
events and updates the chat panel, status pane and inspector.

File: klea_utils/ui/web/nicegui/components/stream.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from datetime import datetime
from typing import Any

import httpx
from nicegui import ui

from klea_utils.api.sse import stream_events
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import ensure_chat

logger = logging.getLogger(__name__)

INSPECTOR_BUFFER_KEY = "inspector_buffer"


def apply_stream_event(chat: dict[str, Any], event: dict[str, Any]) -> str | None:
    """Apply one stream event's pure state mutations to the *chat* dict.

    Mutates *chat* in place (token usage, status sections, inspector
    buffer, and, on completion, the final message) and returns the
    action the UI layer reacts to:

    =============  =====================================================
    return value   meaning
    =============  =====================================================
    ``"usage"``    token usage totals were incremented
    ``"state"``    a status-pane section was stored
    ``"debug"``    an inspector entry was buffered
    ``"context"``  session context (e.g. mode / assurance) was stored
    ``"complete"`` the final assistant message was appended
    ``"error"``    the backend signalled an error
    ``None``       no state change (progress / info / token events)
    =============  =====================================================

    Inspector entries are buffered under :data:`INSPECTOR_BUFFER_KEY`;
    the caller clears the buffer at stream start and commits it to
    ``inspector_entries`` when the ``complete`` event arrives.

    :param chat: Chat session dict (see ``state.ensure_chat``).
    :param event: Parsed SSE event dict from ``stream_events``.
    :returns: Action string described above, or ``None``.
    """
    t = event.get("type")

    if t == "context":
        # App-defined session context (e.g. the agent's operating mode and
        # its assurance, ADR-0030), carried verbatim into the chat dict so
        # the page can render it (badges / status) without app-specific
        # knowledge of every event type.
        chat.setdefault("context", {}).update(event.get("data", {}))
        return "context"

    if t == "debug":
        data = event.get("data", {})
        chat.setdefault(INSPECTOR_BUFFER_KEY, []).append(
            {
                "type": t,
                "node": event.get("node", ""),
                "heading": data.get("heading", ""),
                "summary": data.get("summary", ""),
                "details": data.get("details", {}),
                "timing_seconds": data.get("timing_seconds", None),
            }
        )
        return "debug"

    if t == "usage":
        data = event.get("data", {})
        details = data.get("details", {})
        usage = chat.setdefault(
            "token_usage",
            {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
        )
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            usage[key] += details.get(key, 0)
        return "usage"

    if t == "state":
        data = event.get("data", {})
        node = event.get("node", "")
        chat.setdefault("state_sections", {})[node] = {
            "heading": data.get("heading", ""),
            "display": data.get("display", ""),
            "summary": data.get("summary", ""),
            "details": data.get("details", {}),
        }
        return "state"

    if t == "complete":
        message = event.get("message_for_user", "")
        stamp = datetime.now().astimezone().strftime("%X")
        chat.setdefault("messages", []).append((message, stamp, False))
        return "complete"

    if t == "error":
        return "error"

    return None


async def run_stream(ctx: PageContext, query: str, chat_id: str) -> None:
    """Stream a query's events into the UI for *chat_id*.

    Shows a progress row while streaming, commits the final answer and
    inspector data on completion, and surfaces errors as nicegui
    notifications.

    :param ctx: The shared page context.
    :param query: The user's query text.
    :param chat_id: Chat conversation identifier.
    """
    current_chat = ensure_chat(ctx.user_id, chat_id)
    logger.debug("Streaming query for chat %s", chat_id)
    ctx.is_streaming = True
    current_chat["state_sections"] = {}
    current_chat[INSPECTOR_BUFFER_KEY] = []
    ctx.refresh_status_pane()

    with ctx.stream_container:
        pg_row = ui.row().classes("w-full items-center gap-2 p-2")
        with pg_row:
            ui.spinner(type="dots").classes("w-4 h-4")
            pg_label = ui.label("").classes("text-xs text-grey-5 italic")

    try:
        async for event in stream_events(
            query, chat_id, ctx.server_url, user_id=ctx.user_id
        ):
            t = event.get("type", "?")
            logger.debug("chat=%s stream event type=%s", chat_id, t)
            if t == "progress":
                pg_label.set_text(f"{event.get('node', '')}")
                continue
            action = apply_stream_event(current_chat, event)
            if action in ("usage", "state", "context"):
                ctx.refresh_status_pane()
            elif action == "complete":
                pg_row.delete()
                logger.debug("chat=%s stream complete", chat_id)
                ctx.render_chat_area()
                ctx.is_streaming = False
                ctx.refresh_status_pane()
                current_chat["inspector_entries"] = current_chat.get(
                    INSPECTOR_BUFFER_KEY, []
                )
                current_chat["inspector_expanded"] = set()
                ctx.refresh_inspector()
                break
            elif action == "error":
                pg_row.delete()
                error_msg = event.get("message", "Unknown error")
                logger.debug("chat=%s stream error: %s", chat_id, error_msg)
                with ctx.stream_container:
                    message = f"Error: {error_msg}"
                    # Missing-model errors are actionable: point the user at
                    # the Choose models dialog so they can set a model and
                    # retry without leaving the page.
                    if "No model configured" in error_msg:
                        message += (
                            " Use the settings (gear) icon to choose a model "
                            "for this chat, then retry."
                        )
                    ui.notification(
                        message,
                        type="negative",
                        timeout=10000,
                        close_button=True,
                    )
                ctx.is_streaming = False
                ctx.refresh_status_pane()
                break
    except httpx.RequestError as e:
        pg_row.delete()
        logger.debug("chat=%s request error: %s", chat_id, e)
        with ctx.stream_container:
            ui.notification(
                f"Connection error: {e}",
                type="negative",
                timeout=10000,
                close_button=True,
            )
        ctx.is_streaming = False
        ctx.refresh_status_pane()
