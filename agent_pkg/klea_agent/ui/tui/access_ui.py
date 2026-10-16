#!/usr/bin/env python3
"""
Agent tool-access-level vocabulary for the Textual TUI (ADR-0037).

The access-level keys/labels and the helper that resolves the chat's effective
level from the checkpointed state (the graph's command node changes it; a
``context`` event refreshes the row).  The level is displayed as part of the
combined mode/access status row in
:mod:`klea_agent.ui.tui.status_ui`.

File: klea_agent/ui/tui/access_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from klea_utils.ui.state import chats
from klea_utils.ui.tui.context import TuiContext

#: Tool access-level options: value -> (display label, tooltip).
ACCESS_LEVELS: dict[str, tuple[str, str]] = {
    "full": ("Full", "All tools, including destructive ones"),
    "read_only": ("Read-only", "Only explicitly read-only, non-destructive tools"),
}

#: Fallback level when neither the chat nor the hydrated context declares one.
DEFAULT_ACCESS_LEVEL = "full"


def current_access_level(ctx: TuiContext) -> str:
    """Return the chat's effective access level (context, else pref, else default).

    :param ctx: The shared TUI context.
    :returns: A key of :data:`ACCESS_LEVELS`.
    """
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
    level = (chat.get("context") or {}).get("access_level")
    if level in ACCESS_LEVELS:
        return str(level)
    pref = chat.get("access_pref")
    if pref in ACCESS_LEVELS:
        return str(pref)
    return DEFAULT_ACCESS_LEVEL
