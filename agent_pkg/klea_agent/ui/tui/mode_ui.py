#!/usr/bin/env python3
"""
Agent operating-mode vocabulary for the Textual TUI (ADR-0030).

The mode keys/labels and the helper that resolves the chat's effective mode
from the checkpointed state (the graph's command node changes it; a
``context`` event refreshes the row).  The mode is displayed as part of the
combined mode/access status row in
:mod:`klea_agent.ui.tui.status_ui`.

File: klea_agent/ui/tui/mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from klea_utils.ui.state import chats
from klea_utils.ui.tui.context import TuiContext

#: Operating-mode options: value -> (display label, tooltip).
MODES: dict[str, tuple[str, str]] = {
    "general": ("General", "Everyday assistant mode"),
    "scientific": ("Scientific", "Validated answers; requires a curated source"),
}

#: Fallback mode when the chat has no resolved/preferred mode yet.
DEFAULT_MODE = "general"


def current_mode(ctx: TuiContext) -> str:
    """Return the chat's effective mode (resolved, else preferred, else default).

    :param ctx: The shared TUI context.
    :returns: A key of :data:`MODES`.
    """
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
    requested = (chat.get("context") or {}).get("requested")
    if requested in MODES:
        return str(requested)
    pref = chat.get("mode_pref")
    if pref in MODES:
        return str(pref)
    return DEFAULT_MODE
