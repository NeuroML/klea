#!/usr/bin/env python3
"""
Agent tool-access-level selector for the Textual TUI (ADR-0037).

The agent-specific access control that slots into the shared status panel via
:attr:`TuiContext.status_extras` (ADR-0031/ADR-0050): a row of buttons
(full / read_only) that, when picked, dispatch ``/access <value>`` as a query
(the graph's command node changes the checkpointed access level).  The
effective level streamed back as a ``context`` event re-renders the selector,
so the active level is highlighted (the status panel refreshes its slots on a
``context`` change).

File: klea_agent/ui/tui/access_ui.py

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


class AccessSelector(Vertical):
    """A row of access-level buttons; picking one dispatches ``/access``."""

    def __init__(self, ctx: TuiContext, *, id: str | None = None) -> None:
        """Build the selector for *ctx*.

        :param ctx: The shared TUI context.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        """Compose the access-level buttons, highlighting the active level."""
        active = current_access_level(self._ctx)
        yield Static("Access:", markup=False)
        with Horizontal():
            for value, (label, _tip) in ACCESS_LEVELS.items():
                yield Button(
                    label,
                    id=f"access-{value}",
                    variant="primary" if value == active else "default",
                )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch ``/access <value>`` for the pressed button.

        :param event: The ``Button.Pressed`` event.
        """
        button_id = event.button.id or ""
        if not button_id.startswith("access-"):
            return
        value = button_id[len("access-") :]
        if value not in ACCESS_LEVELS:
            return
        logger.debug(f"access selector: dispatching /access {value}")
        self.post_message(SubmitQuery(f"/access {value}"))


def access_status_extra(ctx: TuiContext) -> AccessSelector:
    """Return the agent access selector for the status-panel slots.

    :param ctx: The shared TUI context.
    :returns: The :class:`AccessSelector` widget.
    """
    return AccessSelector(ctx)
