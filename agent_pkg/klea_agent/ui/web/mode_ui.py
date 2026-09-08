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
        """Render the selector row and the resolved-mode badge."""
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
        if not current_chat:
            return
        context = current_chat.get("context", {})
        requested = current_chat.get("mode_pref", "general")
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
