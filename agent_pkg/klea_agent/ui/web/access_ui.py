#!/usr/bin/env python3
"""
Agent tool-access-level selector (ADR-0037).

This module provides the *agent-specific* access-level UI that slots into the
shared status pane via :attr:`PageContext.status_extras` (ADR-0031, ADR-0032):
a request selector (full / read_only) that writes the next query's
``access_level`` request into ``PageContext.query_extra``; the *effective*
level streamed back as a ``context`` event (the checkpointed graph state --
never the raw request) seeds the selector.

File: klea_agent/ui/web/access_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.ui.web.nicegui.components.choice import choice_buttons
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats, resolve_chat_choice

logger = logging.getLogger(__name__)

# Tool access-level options: value -> (display label, tooltip)
ACCESS_LEVELS: dict[str, tuple[str, str]] = {
    "full": ("Full", "All tools, including destructive ones"),
    "read_only": ("Read-only", "Only explicitly read-only, non-destructive tools"),
}

#: Fallback level when neither the chat nor the hydrated context declares one.
DEFAULT_ACCESS_LEVEL = "full"


def attach_access_ui(ctx: PageContext) -> None:
    """Register the per-chat access-level selector as a status-pane slot.

    Must be called before :func:`klea_utils.ui.web.nicegui.components.status_pane.attach_status_pane`
    (the pane renders its slots at attach time and on every refresh).

    Access is a per-chat property: the buttons write the request into
    ``ctx.query_extra["access_level"]`` (carried by the next query) and
    an ``access_pref`` on the frontend chat state.  ``query_extra`` is
    page-session scoped, so it only counts as a pending request before a
    chat exists (letting the user pick a level for the first message);
    once a chat exists that chat's preference and hydrated ``context``
    win, so switching chats restores each chat's own level.

    :param ctx: The shared page context.
    """
    level_keys = list(ACCESS_LEVELS)

    def _set_access(level: str) -> None:
        """Persist the user's request and refresh the pane."""
        if level not in level_keys:
            logger.warning("Ignoring unknown access level request: %s", level)
            return
        ctx.query_extra["access_level"] = level
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
        if current_chat:
            current_chat["access_pref"] = level
        ctx.refresh_status_pane()
        logger.debug("user=%s requested access_level=%s", ctx.user_id, level)

    def _render() -> None:
        """Render the selector row, tracking this chat's effective level.

        The selector mirrors the *request* (restored across reloads from
        the hydrated ``context``, which carries the checkpointed level).
        Before a chat exists the pending ``query_extra`` selection is
        used; after that the per-chat state decides, so each chat keeps
        its own level.
        """
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
        context = current_chat.get("context", {})
        # ``query_extra`` is page-session scoped, so ``resolve_chat_choice``
        # only lets it win before a chat exists; afterwards this chat's
        # preference/context decide, keeping each chat's level independent.
        requested = resolve_chat_choice(
            current_chat,
            ctx.query_extra.get("access_level"),
            current_chat.get("access_pref"),
            context.get("access_level"),
            ACCESS_LEVELS,
            DEFAULT_ACCESS_LEVEL,
        )

        if ctx.query_extra.get("access_level") != requested:
            ctx.query_extra["access_level"] = requested
            logger.debug(
                "user=%s chat=%s sync query_extra access_level=%s",
                ctx.user_id,
                ctx.chat_id,
                requested,
            )

        choice_buttons(
            "Access:",
            {value: label for value, (label, _tip) in ACCESS_LEVELS.items()},
            requested,
            _set_access,
            colors={"full": "red-5", "read_only": "green-5"},
            tooltips={value: tip for value, (_label, tip) in ACCESS_LEVELS.items()},
        )

    ctx.status_extras.append(_render)
