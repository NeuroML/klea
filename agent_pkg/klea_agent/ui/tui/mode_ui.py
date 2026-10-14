#!/usr/bin/env python3
"""
Agent operating-mode selector for the Textual TUI (ADR-0030).

The agent-specific mode control that slots into the shared status panel via
:attr:`TuiContext.status_extras` (ADR-0031/ADR-0050): a row of buttons
(general / scientific) that, when picked, dispatch ``/mode <value>`` as a
query (the graph's command node changes the checkpointed mode).  The resolved
mode streamed back as a ``context`` event re-renders the selector, so the
active mode is highlighted (the status panel refreshes its slots on a
``context`` change).

File: klea_agent/ui/tui/mode_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from klea_utils.ui.state import chats
from klea_utils.ui.tui.context import TuiContext
from klea_utils.ui.tui.events import SubmitQuery
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

logger = logging.getLogger(__name__)

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


class ModeSelector(Vertical):
    """A row of operating-mode buttons; picking one dispatches ``/mode``."""

    def __init__(self, ctx: TuiContext, *, id: str | None = None) -> None:
        """Build the selector for *ctx*.

        :param ctx: The shared TUI context.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        """Compose the mode buttons, highlighting the active mode."""
        active = current_mode(self._ctx)
        yield Static("Mode:", markup=False)
        with Horizontal():
            for value, (label, _tip) in MODES.items():
                yield Button(
                    label,
                    id=f"mode-{value}",
                    variant="primary" if value == active else "default",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch ``/mode <value>`` for the pressed button.

        :param event: The ``Button.Pressed`` event.
        """
        button_id = event.button.id or ""
        if not button_id.startswith("mode-"):
            return
        value = button_id[len("mode-") :]
        if value not in MODES:
            return
        logger.debug(f"mode selector: dispatching /mode {value}")
        self.post_message(SubmitQuery(f"/mode {value}"))


def mode_status_extra(ctx: TuiContext) -> ModeSelector:
    """Return the agent mode selector for the status-panel slots.

    :param ctx: The shared TUI context.
    :returns: The :class:`ModeSelector` widget.
    """
    return ModeSelector(ctx)
