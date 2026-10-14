#!/usr/bin/env python3
"""
Agent project-root display (session working directory).

This module provides the *agent-specific* project-root row that slots into the
shared status pane via :attr:`PageContext.status_extras` (ADR-0031,
ADR-0032), below the operating-mode and tool-access selectors.  It is
read-only: the checkpointed session context streams the effective
``project_root`` back as a ``context`` event (set with ``/cwd``), and the row
reflects it (falling back to the server working directory when unset).

File: klea_agent/ui/web/project_root_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_utils.ui.state import chats
from klea_utils.ui.web.nicegui.components.context import PageContext
from nicegui import ui

logger = logging.getLogger(__name__)


def attach_project_root_ui(ctx: PageContext) -> None:
    """Register the read-only project-root row as a status-pane slot.

    Must be called before
    :func:`klea_utils.ui.web.nicegui.components.status_pane.attach_status_pane`
    (the pane renders its slots at attach time and on every refresh), and
    after the mode/access slots so it renders below them.

    :param ctx: The shared page context.
    """

    def _render() -> None:
        """Render the effective project root for the active chat."""
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
        context = current_chat.get("context", {}) or {}
        root = str(context.get("project_root", "") or "").strip()
        display = root or "(server working directory)"
        logger.debug("user=%s chat=%s project_root=%s", ctx.user_id, ctx.chat_id, root)
        with ui.column().classes("w-full gap-0 pt-1"):
            label = ui.label(f"Project root: {display}").classes("text-xs")
            label.style("word-break: break-all")
            ui.tooltip(
                "Directory the file tools operate in. Change it with /cwd <path>."
            )

    ctx.status_extras.append(_render)
