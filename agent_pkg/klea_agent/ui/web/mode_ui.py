#!/usr/bin/env python3
"""
Agent operating-mode selector and badge (ADR-0030).

This module provides the *agent-specific* mode UI that slots into the
shared status pane via :attr:`PageContext.status_extras` (ADR-0031,
ADR-0032): a request selector (general / scientific) that writes the
next query's ``mode`` request into ``PageContext.query_extra``, and a
badge that mirrors the *resolved* mode streamed back as a ``context``
event (the checkpointed graph state -- never the raw request).

File: klea_agent/ui/web/mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.ui.web.nicegui.components.choice import choice_buttons
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats, resolve_choice

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
        mirrors the *resolved* mode from checkpointed state.  Before a chat
        exists the selector still renders with the default, so the mode can
        be chosen before the first message.
        """
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
        context = current_chat.get("context", {})
        # Pending request (this session's selection) wins, then the per-chat
        # preference, then the hydrated context, then the default.
        requested = resolve_choice(
            ctx.query_extra.get("mode"),
            current_chat.get("mode_pref"),
            context.get("requested"),
            MODES,
            "general",
        )

        # Keep the request sent with the next query aligned with this chat.
        # Needed because ``Mode`` is a whole-object state field (no reducer):
        # an empty query_extra after a reload would send the server default
        # and silently reset the checkpointed mode on the next query.
        if ctx.query_extra.get("mode") != requested:
            ctx.query_extra["mode"] = requested
            logger.debug(
                "user=%s chat=%s sync query_extra mode=%s",
                ctx.user_id,
                ctx.chat_id,
                requested,
            )

        note = context.get("note", "")

        choice_buttons(
            "Mode:",
            {value: label for value, (label, _tip) in MODES.items()},
            requested,
            _set_mode,
            colors={"general": "blue-5", "scientific": "green-5"},
            tooltips={value: tip for value, (_label, tip) in MODES.items()},
            info=note,
        )

    ctx.status_extras.append(_render)
