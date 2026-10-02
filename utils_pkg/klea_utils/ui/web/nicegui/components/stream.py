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
from nicegui import background_tasks, ui

from klea_utils.api.sse import stream_events
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import ChatData, InspectorMarker, ensure_chat

logger = logging.getLogger(__name__)


def apply_stream_event(chat: ChatData, event: dict[str, Any]) -> str | None:
    """Apply one stream event's pure state mutations to the *chat* dict.

    Mutates *chat* in place (token usage, status sections, inspector
    entries, and, on completion, the final message) and returns the
    action the UI layer reacts to:

    ==============  =====================================================
    return value    meaning
    ==============  =====================================================
    ``"usage"``     token usage totals were incremented
    ``"state"``     a status-pane section was stored
    ``"inspect"``   an inspection entry was appended
    ``"context"``   session context (e.g. the operating mode) was stored
    ``"complete"``  the final assistant message was appended
    ``"error"``     the backend signalled an error
    ``None``        no state change (progress / info / token events)
    ==============  =====================================================

    Inspector entries are appended to ``inspector_entries`` as they
    arrive; the caller renders each one incrementally.  A per-query
    marker is prepended by the caller at stream start.

    :param chat: Chat session dict (see ``state.ensure_chat``).
    :param event: Parsed SSE event dict from ``stream_events``.
    :returns: Action string described above, or ``None``.
    """
    t = event.get("type")

    if t == "context":
        # App-defined session context (e.g. the agent's operating mode,
        # ADR-0030), carried verbatim into the chat dict so
        # the page can render it (status) without app-specific
        # knowledge of every event type.
        chat.setdefault("context", {}).update(event.get("data", {}))
        return "context"

    if t == "inspect":
        data = event.get("data", {})
        chat.setdefault("inspector_entries", []).append(
            {
                "type": t,
                "node": event.get("node", ""),
                "heading": data.get("heading", ""),
                "summary": data.get("summary", ""),
                "details": data.get("details", {}),
                "timing_seconds": data.get("timing_seconds", None),
            }
        )
        return "inspect"

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
        # Sections are keyed by ``data["key"]`` when set, else by node label,
        # so nodes can share one section (e.g. a live plan updated by the
        # Planner and the Evaluator) instead of each owning a duplicate.
        section_key = data.get("key") or node
        chat.setdefault("state_sections", {})[section_key] = {
            "heading": data.get("heading", ""),
            "display": data.get("display", ""),
            "summary": data.get("summary", ""),
            "details": data.get("details", {}),
            "preformatted": data.get("preformatted", False),
        }
        return "state"

    if t == "complete":
        message = event.get("message_for_user", "")
        stamp = datetime.now().astimezone().strftime("%X")
        chat.setdefault("messages", []).append(
            {"text": message, "stamp": stamp, "role": "agent", "header": ""}
        )
        return "complete"

    if t == "tool":
        # Chat-renderable tool output (e.g. a file edit's diff).  One message
        # per entry so each renders as its own full-width block, before the
        # final answer.  ``mime`` selects the renderer; ``display`` is the
        # text fallback.
        data = event.get("data", {})
        stamp = datetime.now().astimezone().strftime("%X")
        for entry in data.get("tools", []):
            if not entry.get("data") and not entry.get("display"):
                continue
            chat.setdefault("messages", []).append(
                {
                    "text": entry.get("display", ""),
                    "stamp": stamp,
                    "role": "tool",
                    "header": entry.get("header", entry.get("title", "")),
                    "mime": entry.get("mime", ""),
                    "data": entry.get("data", ""),
                    "meta": entry.get("meta", {}),
                    "is_error": bool(entry.get("is_error", False)),
                }
            )
        return "tool"

    if t == "error":
        return "error"

    return None


def _retry_stream(ctx: PageContext, query: str, chat_id: str, error_row: Any) -> None:
    """Remove the error row and resume the chat's last failed run."""
    error_row.delete()
    background_tasks.create(run_stream(ctx, query, chat_id, resume=True))


async def run_stream(
    ctx: PageContext, query: str, chat_id: str, resume: bool = False
) -> None:
    """Stream a query's events into the UI for *chat_id*.

    Shows a progress row while streaming, commits the final answer and
    inspector data on completion, and surfaces errors inline (with a Retry
    action when the run can be resumed from its checkpoint).

    :param ctx: The shared page context.
    :param query: The user's query text (unused when resuming).
    :param chat_id: Chat conversation identifier.
    :param resume: Resume the chat's last failed run instead of starting a
        new turn (the query is not resent).
    """
    current_chat = ensure_chat(ctx.user_id, chat_id)
    logger.debug("Streaming query for chat %s (resume=%s)", chat_id, resume)
    ctx.is_streaming = True
    ctx.streaming_chat_id = chat_id
    current_chat["state_sections"] = {}
    if resume:
        # Continuing the same turn: keep the existing inspector section and
        # re-activate it so appended entries land in the right place.
        ctx.refresh_inspector()
    else:
        # Start a new inspector section for this query.  Entries are appended
        # live; sections (and their entries) are kept for the session.
        marker: InspectorMarker = {
            "type": "query",
            "text": query,
            "stamp": datetime.now().astimezone().strftime("%X"),
        }
        current_chat["inspector_entries"].append(marker)
        ctx.begin_inspector_section(chat_id, marker)
    ctx.refresh_status_pane()

    # run_stream runs in a background task, which has no ambient slot; enter
    # the stream container explicitly before creating elements (see the slot
    # warning on PageContext).
    with ctx.stream_container:
        pg_row = ui.row().classes("w-full items-center gap-2 p-2")
        with pg_row:
            ui.spinner(type="dots").classes("w-4 h-4")
            pg_label = ui.label("").classes("text-xs text-grey-5 italic")

    try:
        async for event in stream_events(
            query,
            chat_id,
            ctx.server_url,
            user_id=ctx.user_id,
            extra=ctx.query_extra or None,
            resume=resume,
        ):
            t = event.get("type", "?")
            logger.debug("chat=%s stream event type=%s", chat_id, t)
            if t == "progress":
                heading = (event.get("data") or {}).get("heading") or event.get(
                    "node", ""
                )
                pg_label.set_text(heading)
                continue
            action = apply_stream_event(current_chat, event)
            if action in ("usage", "state", "context"):
                ctx.refresh_status_pane()
            elif action == "inspect":
                entry = current_chat["inspector_entries"][-1]
                ctx.append_inspector(chat_id, entry)
            elif action == "tool":
                ctx.render_chat_area()
            elif action == "complete":
                pg_row.delete()
                logger.debug("chat=%s stream complete", chat_id)
                ctx.render_chat_area()
                ctx.is_streaming = False
                ctx.streaming_chat_id = ""
                ctx.refresh_status_pane()
                ctx.refresh_inspector()
                break
            elif action == "error":
                pg_row.delete()
                error_msg = event.get("message", "Unknown error")
                resumable = bool(event.get("resumable"))
                logger.debug(
                    "chat=%s stream error: %s (resumable=%s)",
                    chat_id,
                    error_msg,
                    resumable,
                )
                with ctx.stream_container:
                    error_row = ui.row().classes("w-full items-center gap-2 p-2")
                    with error_row:
                        ui.icon("error").classes("text-negative")
                        message = f"Error: {error_msg}"
                        # Missing-model errors are actionable: point the user
                        # at the Choose models dialog so they can set a model
                        # and retry without leaving the page.
                        if "No model configured" in error_msg:
                            message += (
                                " Use the settings (gear) icon to choose a "
                                "model for this chat, then retry."
                            )
                        ui.label(message).classes("text-xs text-negative flex-grow")
                        if resumable:
                            ui.button(
                                "Retry",
                                on_click=lambda row=error_row: _retry_stream(
                                    ctx, query, chat_id, row
                                ),
                            ).props("flat dense color=primary")
                ctx.is_streaming = False
                ctx.streaming_chat_id = ""
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
        ctx.streaming_chat_id = ""
        ctx.refresh_status_pane()
