#!/usr/bin/env python3
"""
Agent tool-access-level selector for the Textual TUI (ADR-0037).

The agent-specific access control that slots into the shared status panel via
:attr:`TuiContext.status_extras` (ADR-0031/ADR-0050): a compact one-line
display of the active access level (the graph's
command node changes the checkpointed access level).  The effective level
streamed back as a ``context`` event re-renders the line, so the active level
stays current (the status panel refreshes its slots on a ``context`` change).

File: klea_agent/ui/tui/access_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from klea_utils.ui.state import chats
from klea_utils.ui.tui.context import TuiContext
from textual.widgets import Static

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


class AccessSelector(Static):
    """A compact one-line display of the active access level."""

    def __init__(self, ctx: TuiContext, *, id: str | None = None) -> None:
        """Build the selector for *ctx*.

        :param ctx: The shared TUI context.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id, markup=False)
        self._ctx = ctx

    def on_mount(self) -> None:
        """Render the active access-level line."""
        label = ACCESS_LEVELS[current_access_level(self._ctx)][0]
        self.update(f"Access: {label}")


def access_status_extra(ctx: TuiContext) -> AccessSelector:
    """Return the agent access selector for the status-panel slots.

    :param ctx: The shared TUI context.
    :returns: The :class:`AccessSelector` widget.
    """
    return AccessSelector(ctx)
