#!/usr/bin/env python3
"""
SSE stream handling component for Klea pages.

Contains the pure state-mutation logic (:func:`apply_stream_event`,
unit-testable without NiceGUI) and the UI-driving coroutine
(:func:`run_stream`) that consumes the backend's ``/query/stream``
events and updates the chat panel, status pane and inspector.

See ``devdocs/system/streams.md`` and ``devdocs/system/api-sse-sequence.md``.

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

    if t == "interrupt":
        # The run paused for HITL input (ADR-0046): store the ask so the
        # status region can render the form; cleared when the next turn starts.
        chat["interrupt"] = event.get("data", {})
        return "interrupt"

    if t == "error":
        return "error"

    return None


def _retry_stream(ctx: PageContext, query: str, chat_id: str) -> None:
    """Resume the chat's last failed run (Retry action)."""
    task = background_tasks.create(run_stream(ctx, query, chat_id, resume=True))
    ctx.stream_tasks[f"{ctx.user_id}:{chat_id}"] = task


def stop_stream(ctx: PageContext, chat_id: str) -> None:
    """Stop *chat_id*'s active run: cancel locally and ask the server to cancel.

    Cancels the chat's ``run_stream`` task (closing the SSE connection) and
    fires a best-effort ``/query/cancel`` so the server stops the graph run
    even if the disconnect alone would not.  Idempotent and safe to call when
    the chat is not streaming.

    :param ctx: The shared page context.
    :param chat_id: Chat whose run should stop.
    """
    task = ctx.stream_tasks.get(f"{ctx.user_id}:{chat_id}")
    logger.debug("stop_stream(chat=%s, task=%s)", chat_id, task)
    if task is not None and not task.done():
        task.cancel()
    if chat_id:
        background_tasks.create(request_cancel(ctx.server_url, chat_id, ctx.user_id))


async def run_stream(
    ctx: PageContext,
    query: str,
    chat_id: str,
    resume: bool = False,
    interrupt_response: dict[str, Any] | None = None,
    interrupt_id: str | None = None,
    interrupt_cancel: bool = False,
) -> None:
    """Stream a query's events into the UI for *chat_id*.

    Shows a progress row while streaming, commits the final answer and
    inspector data on completion, and surfaces errors inline (with a Retry
    action when the run can be resumed from its checkpoint).  When the run
    pauses for human input it renders the ask (the status region builds the
    form) and stops streaming until the user answers or cancels.

    :param ctx: The shared page context.
    :param query: The user's query text (unused when resuming/answering).
    :param chat_id: Chat conversation identifier.
    :param resume: Resume the chat's last failed run instead of starting a
        new turn (the query is not resent).
    :param interrupt_response: Answer a pending HITL interrupt (ADR-0046).
    :param interrupt_id: Id of the interrupt being answered.
    :param interrupt_cancel: Cancel the pending interrupt instead of answering.
    """
    current_chat = ensure_chat(ctx.user_id, chat_id)
    is_query = not resume and interrupt_response is None and not interrupt_cancel
    logger.debug(
        "Streaming chat %s (resume=%s interrupt=%s)",
        chat_id,
        resume,
        "cancel" if interrupt_cancel else interrupt_response is not None,
    )
    key = f"{ctx.user_id}:{chat_id}"

    def _active() -> bool:
        """Whether *chat_id* is the chat currently shown on the page.

        Chat data is always updated; the DOM is only touched when the run's
        chat is the visible one, so a background run cannot repaint the chat
        the user is looking at.
        """
        return chat_id == ctx.chat_id

    # Per-chat run registry (page-scoped): the callers register the created
    # task, so a run is visible for gating before this coroutine starts;
    # ``setdefault`` keeps their handle when they raced ahead.
    ctx.stream_tasks.setdefault(key, asyncio.current_task())
    # Retry resumes this turn; the status region's Retry button invokes it.
    # Kept after the run (below) so a failed run's Retry action survives.
    ctx.retry_cbs[key] = lambda: _retry_stream(ctx, query, chat_id)
    if _active():
        ctx.refresh_stream_button()
    current_chat["state_sections"] = {}
    # The status region is per-chat state (rendered by the chat-area
    # component), so it is replaced wholesale on every turn and switching
    # chats restores each chat's own status.
    current_chat["turn_status"] = {"kind": "progress", "heading": ""}
    current_chat.pop("interrupt", None)
    if not is_query:
        # Continuing the same turn (resume or an interrupt answer/cancel):
        # keep the existing inspector section and re-activate it so appended
        # entries land in the right place.
        if _active():
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
    if _active():
        ctx.refresh_status_pane()
        ctx.refresh_turn_status()

    def _reset_streaming_state() -> None:
        """Drop this chat's run entry and refresh the send control/status."""
        ctx.stream_tasks.pop(key, None)
        if _active():
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
            interrupt_response=interrupt_response,
            interrupt_id=interrupt_id,
            interrupt_cancel=interrupt_cancel,
        ):
            t = event.get("type", "?")
            logger.debug("chat=%s stream event type=%s", chat_id, t)
            if t == "ping":
                # Periodic server heartbeat while a long-running node emits no
                # events: the connection is alive, nothing to render.
                continue
            if t == "progress":
                heading = (event.get("data") or {}).get("heading") or event.get(
                    "node", ""
                )
                # Update the live heading in place (no full re-render).
                current_chat["turn_status"]["heading"] = heading
                if _active() and ctx.turn_status_label is not None:
                    ctx.turn_status_label.set_text(heading)
                continue
            action = apply_stream_event(current_chat, event)
            if action in ("usage", "state", "context"):
                if _active():
                    ctx.refresh_status_pane()
            elif action == "inspect":
                entry = current_chat["inspector_entries"][-1]
                ctx.append_inspector(chat_id, entry)
            elif action == "tool":
                if _active():
                    ctx.render_chat_area()
            elif action == "interrupt":
                # The run paused for HITL input: show the ask and stop
                # streaming until the user answers or cancels (ADR-0046).
                logger.debug("chat=%s paused for input", chat_id)
                current_chat["turn_status"] = {"kind": "awaiting_input"}
                if _active():
                    ctx.refresh_turn_status()
                _reset_streaming_state()
                break
            elif action == "complete":
                logger.debug("chat=%s stream complete", chat_id)
                current_chat["turn_status"] = {"kind": "idle"}
                current_chat.pop("interrupt", None)
                if _active():
                    ctx.render_chat_area()
                _reset_streaming_state()
                if _active():
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
                current_chat["turn_status"] = {
                    "kind": "error",
                    "message": error_msg,
                    "resumable": resumable,
                }
                if _active():
                    ctx.refresh_turn_status()
                _reset_streaming_state()
                break
    except asyncio.CancelledError:
        # The user pressed Stop: record a "Stopped" status for this chat and
        # re-render the region from it.  Re-raise so the task ends cancelled
        # (NiceGUI's exception handler ignores it).
        logger.debug("chat=%s stream cancelled by user", chat_id)
        current_chat["turn_status"] = {"kind": "stopped"}
        if _active():
            ctx.refresh_turn_status()
        _reset_streaming_state()
        raise
    except httpx.HTTPStatusError as e:
        # The backend rejected the streaming request (e.g. 409 while a run is
        # still active, or 400 for a resume with nothing to continue).  Surface
        # the status instead of letting it escape the background task.
        logger.debug("chat=%s HTTP status error: %s", chat_id, e)
        current_chat["turn_status"] = {
            "kind": "error",
            "message": f"Request failed ({e.response.status_code}): {e}",
            "resumable": False,
        }
        if _active():
            ctx.refresh_turn_status()
        _reset_streaming_state()
    except httpx.RequestError as e:
        # Transport failure: a dropped connection or idle timeout.  The run is
        # checkpointed server-side, so offer a resume.
        logger.debug("chat=%s request error: %s", chat_id, e)
        current_chat["turn_status"] = {
            "kind": "error",
            "message": f"Connection error: {e}",
            "resumable": True,
        }
        if _active():
            ctx.refresh_turn_status()
        _reset_streaming_state()
    finally:
        # Clear this chat's run entry on every exit path (including an
        # unexpected exception) so it never leaks a stale task handle.  The
        # retry callback is kept so a failed run's Retry action survives.
        ctx.stream_tasks.pop(key, None)
