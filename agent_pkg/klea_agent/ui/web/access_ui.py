#!/usr/bin/env python3
"""
Agent tool-access-level selector and badge (ADR-0037).

This module provides the *agent-specific* access-level UI that slots into the
shared status pane via :attr:`PageContext.status_extras` (ADR-0031, ADR-0032):
a request selector (full / read_only) that writes the next query's
``access_level`` request into ``PageContext.query_extra``, and a badge that
mirrors the *effective* level streamed back as a ``context`` event (the
checkpointed graph state -- never the raw request).

File: klea_agent/ui/web/access_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats
from nicegui import ui

logger = logging.getLogger(__name__)

# Tool access-level options: value -> (display label, tooltip)
ACCESS_LEVELS: dict[str, tuple[str, str]] = {
    "full": ("Full", "All tools, including destructive ones"),
    "read_only": ("Read-only", "Only explicitly read-only, non-destructive tools"),
}

#: Fallback level when neither the chat nor the hydrated context declares one.
DEFAULT_ACCESS_LEVEL = "full"


def attach_access_ui(ctx: PageContext) -> None:
    """Register the access-level selector + badge as a status-pane slot.

    Must be called before :func:`klea_utils.ui.web.nicegui.components.status_pane.attach_status_pane`
    (the pane renders its slots at attach time and on every refresh).

    The selector buttons set ``ctx.query_extra["access_level"]`` (the next
    query's request) and a per-chat preference kept in the frontend chat
    state; the badge reflects the effective level from the streamed
    ``context`` event, which is the checkpointed graph state.  Before the
    first stream there is no ``context`` yet, so the selector falls back to the
    request/config default.

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
        """Render the selector row and the effective-level badge.

        The selector tracks the *request* (restored across reloads from the
        hydrated ``context``, which carries the effective checkpointed level);
        the badge mirrors that effective level.  Re-syncing ``query_extra``
        matters because ``access_level`` is a plain state field: an empty
        re-request after a reload would otherwise fall back to the config
        default and change the level on the next query.
        """
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
        if not current_chat:
            return
        context = current_chat.get("context", {})
        # A live access_pref (this session's selection) wins over the
        # hydrated context; the latter restores the last effective level
        # across a page reload, when access_pref is gone.
        requested = (
            current_chat.get("access_pref")
            or context.get("access_level")
            or DEFAULT_ACCESS_LEVEL
        )

        if (
            requested in ACCESS_LEVELS
            and ctx.query_extra.get("access_level") != requested
        ):
            ctx.query_extra["access_level"] = requested
            logger.debug(
                "user=%s chat=%s sync query_extra access_level=%s",
                ctx.user_id,
                ctx.chat_id,
                requested,
            )

        effective = context.get("access_level")

        with ui.row().classes("items-center w-full gap-1"):
            ui.label("Access:").classes("text-xs font-bold text-grey-6")
            for value, (label, tooltip) in ACCESS_LEVELS.items():
                with (
                    ui.button(
                        label,
                        on_click=lambda v=value: _set_access(v),
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

        # Badge mirrors the checkpointed (effective) level, not the request
        # (ADR-0032: ``context`` is a projection of graph state).
        if effective:
            label = ACCESS_LEVELS.get(effective, (effective, ""))[0]
            with ui.row().classes("items-center w-full gap-1"):
                ui.label(f"{label} access").classes("text-xs font-bold text-primary")

    ctx.status_extras.append(_render)
