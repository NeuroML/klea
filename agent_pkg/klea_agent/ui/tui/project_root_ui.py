#!/usr/bin/env python3
"""
Agent project-root display for the Textual TUI.

The agent-specific project-root row that slots into the shared status panel via
:attr:`TuiContext.status_extras` (ADR-0031/ADR-0050), below the mode and access
selectors.  It is read-only: the checkpointed session context streams the
effective ``project_root`` back as a ``context`` event, and
the row reflects it (falling back to the server working directory when unset).

File: klea_agent/ui/tui/project_root_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from klea_utils.ui.state import chats
from klea_utils.ui.tui.context import TuiContext
from textual.widgets import Static

logger = logging.getLogger(__name__)


def current_project_root(ctx: TuiContext) -> str:
    """Return the chat's effective project root, or ``""`` when unset.

    :param ctx: The shared TUI context.
    :returns: The checkpointed ``project_root`` from the chat's context.
    """
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
    return str((chat.get("context") or {}).get("project_root", "") or "").strip()


def project_root_status_extra(ctx: TuiContext) -> Static:
    """Return the read-only project-root row for the status-panel slots.

    :param ctx: The shared TUI context.
    :returns: A :class:`~textual.widgets.Static` line.
    """
    root = current_project_root(ctx)
    return Static(
        f"Project root: {root or '(server working directory)'}",
        markup=False,
    )
