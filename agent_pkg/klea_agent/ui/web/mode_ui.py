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
from klea_utils.ui.web.nicegui.state import chats, resolve_chat_choice

logger = logging.getLogger(__name__)

# Operating-mode options: value -> (display label, tooltip)
MODES: dict[str, tuple[str, str]] = {
    "general": ("General", "Everyday assistant mode"),
    "scientific": ("Scientific", "Validated answers; requires a curated source"),
}


def attach_mode_ui(ctx: PageContext) -> None:
    """Register the per-chat mode selector as a status-pane content slot.

    Must be called before :func:`klea_utils.ui.web.nicegui.components.status_pane.attach_status_pane`
    (the pane renders its slot at attach time and on every refresh).

    Mode is a per-chat property: the buttons write the request into
    ``ctx.query_extra["mode"]`` (carried by the next query) and a
    ``mode_pref`` on the frontend chat state.  ``query_extra`` is
    page-session scoped, so it only counts as a pending request before a
    chat exists (letting the user pick a mode for the first message);
    once a chat exists that chat's preference and hydrated ``context``
    win, so switching chats restores each chat's own mode.  The
    inform-branch ``note`` is shown as the group tooltip.

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
        """Render the selector row, tracking this chat's effective mode.

        The selector mirrors the *request* (the next query will carry it,
        restored across reloads from the hydrated ``context``).  Before a
        chat exists the pending ``query_extra`` selection is used; after
        that the per-chat state decides, so each chat keeps its own mode.
        """
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
        context = current_chat.get("context", {})
        # ``query_extra`` is page-session scoped, so ``resolve_chat_choice``
        # only lets it win before a chat exists; afterwards this chat's
        # preference/context decide, keeping each chat's mode independent.
        requested = resolve_chat_choice(
            current_chat,
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
