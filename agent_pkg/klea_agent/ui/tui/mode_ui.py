#!/usr/bin/env python3
"""
Agent operating-mode selector for the Textual TUI (ADR-0030).

The agent-specific mode control that slots into the shared status panel via
:attr:`TuiContext.status_extras` (ADR-0031/ADR-0050): a compact one-line
display of the active mode (the graph's command node
changes the checkpointed mode).  The resolved mode streamed back as a
``context`` event re-renders the line, so the active mode stays current (the
status panel refreshes its slots on a ``context`` change).

File: klea_agent/ui/tui/mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from klea_utils.ui.state import chats
from klea_utils.ui.tui.context import TuiContext
from textual.widgets import Static

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


class ModeSelector(Static):
    """A compact one-line display of the active mode."""

    def __init__(self, ctx: TuiContext, *, id: str | None = None) -> None:
        """Build the selector for *ctx*.

        :param ctx: The shared TUI context.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id, markup=False)
        self._ctx = ctx

    def on_mount(self) -> None:
        """Render the active mode line."""
        self.update(f"Mode: {MODES[current_mode(self._ctx)][0]}")


def mode_status_extra(ctx: TuiContext) -> ModeSelector:
    """Return the agent mode selector for the status-panel slots.

    :param ctx: The shared TUI context.
    :returns: The :class:`ModeSelector` widget.
    """
    return ModeSelector(ctx)
