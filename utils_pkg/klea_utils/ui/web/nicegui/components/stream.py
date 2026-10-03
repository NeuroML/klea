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

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx
from nicegui import background_tasks

from klea_utils.api.sse import request_cancel, stream_events
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


def _retry_stream(ctx: PageContext, query: str, chat_id: str) -> None:
    """Resume the chat's last failed run (Retry action)."""
    background_tasks.create(run_stream(ctx, query, chat_id, resume=True))


def stop_stream(ctx: PageContext) -> None:
    """Stop the active run: cancel locally and ask the server to cancel.

    Cancels the local ``run_stream`` task (closing the SSE connection) and
    fires a best-effort ``/query/cancel`` so the server stops the graph run
    even if the disconnect alone would not.  Idempotent and safe to call
    when nothing is streaming.
    """
    chat_id = ctx.streaming_chat_id
    task = ctx.stream_task
    logger.debug("stop_stream(chat=%s, task=%s)", chat_id, task)
    if task is not None and not task.done():
        task.cancel()
    if chat_id:
        background_tasks.create(request_cancel(ctx.server_url, chat_id, ctx.user_id))


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
    ctx.stream_task = asyncio.current_task()
    # The Stop control calls back into this context; register it here so the
    # component needs no import of this module, and flip the button to Stop.
    ctx.stop_streaming = lambda: stop_stream(ctx)
    # Retry resumes this turn; the status region's Retry button invokes it.
    ctx.stream_retry_cb = lambda: _retry_stream(ctx, query, chat_id)
    ctx.refresh_stream_button()
    current_chat["state_sections"] = {}
    # The status region is per-chat state (rendered by the chat-area
    # component), so it is replaced wholesale on every turn and switching
    # chats restores each chat's own status.
    current_chat["status"] = {"kind": "progress", "heading": ""}
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
    ctx.refresh_stream_status()

    def _reset_streaming_state() -> None:
        """Clear the streaming flags and refresh the status pane."""
        ctx.is_streaming = False
        ctx.streaming_chat_id = ""
        ctx.stream_task = None
        ctx.refresh_stream_button()
        ctx.refresh_status_pane()

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
                # Update the live heading in place (no full re-render).
                current_chat["status"]["heading"] = heading
                if ctx.status_label is not None:
                    ctx.status_label.set_text(heading)
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
                logger.debug("chat=%s stream complete", chat_id)
                current_chat["status"] = {"kind": "idle"}
                ctx.render_chat_area()
                _reset_streaming_state()
                ctx.refresh_inspector()
                break
            elif action == "error":
                error_msg = event.get("message", "Unknown error")
                resumable = bool(event.get("resumable"))
                logger.debug(
                    "chat=%s stream error: %s (resumable=%s)",
                    chat_id,
                    error_msg,
                    resumable,
                )
                current_chat["status"] = {
                    "kind": "error",
                    "message": error_msg,
                    "resumable": resumable,
                }
                ctx.refresh_stream_status()
                _reset_streaming_state()
                break
    except asyncio.CancelledError:
        # The user pressed Stop: record a "Stopped" status for this chat and
        # re-render the region from it.  Re-raise so the task ends cancelled
        # (NiceGUI's exception handler ignores it).
        logger.debug("chat=%s stream cancelled by user", chat_id)
        current_chat["status"] = {"kind": "stopped"}
        ctx.refresh_stream_status()
        _reset_streaming_state()
        raise
    except httpx.RequestError as e:
        logger.debug("chat=%s request error: %s", chat_id, e)
        current_chat["status"] = {
            "kind": "error",
            "message": f"Connection error: {e}",
            "resumable": False,
        }
        ctx.refresh_stream_status()
        _reset_streaming_state()
