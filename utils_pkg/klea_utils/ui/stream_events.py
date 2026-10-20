#!/usr/bin/env python3
"""
Framework-agnostic stream-event reducer shared by the Klea frontends.

The ``/query/stream`` SSE events are turned into frontend state by
:func:`apply_stream_event`, a pure function: it mutates the chat dict it is
given and returns an action string, touching no UI, no HTTP and no NiceGUI.
The web (NiceGUI) and terminal (Textual) clients consume the same reducer, so
the event-to-state contract lives in one place; the UI-driving coroutines
(``run_stream`` / ``stop_stream``) stay in each frontend.

See ``devdocs/system/streams.md`` and ``devdocs/system/tui.md``.

File: klea_utils/ui/stream_events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from klea_utils.ui.state import ChatData

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
    ``"notice"``    a user-facing progress notice was appended
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

    if t == "notice":
        data = event.get("data", {})
        content = str(
            data.get("summary") or (data.get("details") or {}).get("content") or ""
        ).strip()
        if not content:
            return None
        stamp = datetime.now().astimezone().strftime("%X")
        chat.setdefault("messages", []).append(
            {
                "text": content,
                "stamp": stamp,
                "role": "notice",
                "header": event.get("node", ""),
                "kind": (data.get("details") or {}).get("kind", "progress"),
            }
        )
        return "notice"

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


def replay_chat_events(chat: ChatData, events: Iterable[dict[str, Any]]) -> None:
    """Rebuild the pane state from a chat's stored UI events (ADR-0032 update).

    Mirrors the live stream so a reloaded chat shows the same inspection /
    status panes: a ``query`` event opens a new inspector section (the same
    marker the live path inserts at run start), and every other event is
    reduced through :func:`apply_stream_event`.  The transcript is hydrated
    separately from the persisted messages.

    :param chat: The chat session dict (see ``state.ensure_chat``).
    :param events: The stored events (``GET .../events``), oldest first.
    """
    for event in events:
        if event.get("type") == "query":
            chat.setdefault("inspector_entries", []).append(
                {
                    "type": "query",
                    "text": event.get("text", ""),
                    "stamp": event.get("stamp", ""),
                }
            )
            continue
        apply_stream_event(chat, event)
