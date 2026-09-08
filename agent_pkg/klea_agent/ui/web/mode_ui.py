#!/usr/bin/env python3
"""
Agent operating-mode selector and badge (ADR-0030).

This module provides the *agent-specific* mode UI that slots into the
shared status pane via :attr:`PageContext.status_extra` (ADR-0031,
ADR-0032): a request selector (general / scientific) that writes the
next query's ``mode`` request into ``PageContext.query_extra``, and a
badge that mirrors the *resolved* mode streamed back as a ``context``
event (the checkpointed graph state -- never the raw request).

File: klea_agent/ui/web/mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats
from nicegui import ui

logger = logging.getLogger(__name__)

# Operating-mode options: value -> (display label, tooltip)
MODES: dict[str, tuple[str, str]] = {
    "general": ("General", "Everyday assistant mode"),
    "scientific": ("Scientific", "Validated answers; requires a curated source"),
}


def attach_mode_ui(ctx: PageContext) -> None:
    """Register the mode selector + badge as the status-pane content slot.

    Must be called before :func:`klea_utils.ui.web.nicegui.components.status_pane.attach_status_pane`
    (the pane renders its slot at attach time and on every refresh).

    The selector buttons set ``ctx.query_extra["mode"]`` (the next
    query's request) and a per-chat preference kept in the frontend chat
    state; the badge reflects the authoritative checkpointed mode from
    the streamed ``context`` event, with the inform-branch ``note``
    shown alongside.  Before the first stream there is no ``context``
    yet, so the badge falls back to the requested mode.

    :param ctx: The shared page context.
    """
    mode_keys = list(MODES)

    def _set_mode(mode: str) -> None:
        """Persist the user's request and refresh the pane."""
        if mode not in mode_keys:
            logger.warning("Ignoring unknown mode request: %s", mode)
            return
        ctx.query_extra["mode"] = mode
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
        if current_chat:
            current_chat["mode_pref"] = mode
        ctx.refresh_status_pane()
        logger.debug("user=%s requested mode=%s", ctx.user_id, mode)

    def _render() -> None:
        """Render the selector row and the resolved-mode badge.

        The selector tracks the *request* (next query will carry it,
        restored across reloads from the hydrated ``context``); the badge
        mirrors the *resolved* mode from checkpointed state.  Before the
        first stream both are absent, so the request falls back to the
        general default.
        """
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
        if not current_chat:
            return
        context = current_chat.get("context", {})
        # A live mode_pref (this session's selection) wins over the
        # hydrated context; the latter restores the last request across a
        # page reload, when mode_pref is gone.
        requested = (
            current_chat.get("mode_pref") or context.get("requested") or "general"
        )

        # Keep the request sent with the next query aligned with this chat.
        # Needed because ``Mode`` is a whole-object state field (no reducer):
        # an empty query_extra after a reload would send the server default
        # and silently reset the checkpointed mode on the next query.
        if requested in MODES and ctx.query_extra.get("mode") != requested:
            ctx.query_extra["mode"] = requested
            logger.debug(
                "user=%s chat=%s sync query_extra mode=%s",
                ctx.user_id,
                ctx.chat_id,
                requested,
            )

        resolved = context.get("mode")
        note = context.get("note", "")

        with ui.row().classes("items-center w-full gap-1"):
            ui.label("Mode:").classes("text-xs font-bold text-grey-6")
            for value, (label, tooltip) in MODES.items():
                with (
                    ui.button(
                        label,
                        on_click=lambda v=value: _set_mode(v),
                    )
                    .props(
                        "flat dense text-xs "
                        + (
                            "bg-primary text-white"
                            if requested == value
                            else "text-grey-6"
                        )
                    )
                    .classes("px-2")
                ):
                    if requested == value:
                        ui.tooltip(f"Currently requested: {label}. {tooltip}")

        # Badge mirrors the checkpointed (resolved) mode, not the request
        # (ADR-0032: ``context`` is a projection of graph state).
        if resolved:
            label = MODES.get(resolved, (resolved, ""))[0]
            with ui.row().classes("items-center w-full gap-1"):
                ui.label(f"{label} mode").classes("text-xs font-bold text-primary")
                if note:
                    ui.label(note).classes("text-xs text-grey-6 italic")

    ctx.status_extra = _render
