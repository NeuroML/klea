#!/usr/bin/env python3
"""
Agent status rows for the Textual TUI (ADR-0030/0037).

The agent-specific rows that slot into the shared status panel via
:attr:`TuiContext.status_extras` (ADR-0031/ADR-0050).  The operating mode and
the tool-access level share one compact line (for example
``General | Full access``), because the shorthand is self-explanatory and the
narrow status panel fits it on a single line.  The mode and access values are
the checkpointed state (the graph's command node changes them; a ``context``
event re-renders the row); ``Full access`` is shown in the theme's error
colour, mirroring the web selector's red ``Full`` choice.

File: klea_agent/ui/tui/status_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from klea_utils.ui.tui.components.selectable import SelectableStatic
from klea_utils.ui.tui.context import TuiContext
from textual.app import ComposeResult
from textual.containers import Horizontal

from klea_agent.ui.tui.access_ui import ACCESS_LEVELS, current_access_level
from klea_agent.ui.tui.mode_ui import MODES, current_mode

logger = logging.getLogger(__name__)


class ModeAccessSelector(Horizontal):
    """A compact one-line ``Mode | Access`` display.

    The access label is a separate ``SelectableStatic`` so its colour comes
    from the theme (``$error`` for full access, ``$success`` for read-only),
    updating on a theme change without a re-render.
    """

    DEFAULT_CSS = """
    ModeAccessSelector {
        height: 1;
        width: 100%;
    }
    ModeAccessSelector #mode-access-mode {
        width: auto;
    }
    ModeAccessSelector #mode-access-access.access-full {
        width: auto;
        color: $error;
    }
    ModeAccessSelector #mode-access-access.access-read-only {
        width: auto;
        color: $success;
    }
    """

    def __init__(self, ctx: TuiContext, *, id: str | None = None) -> None:
        """Build the selector for *ctx*.

        :param ctx: The shared TUI context.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        """Compose the mode cell and the access cell."""
        yield SelectableStatic("", id="mode-access-mode")
        yield SelectableStatic("", id="mode-access-access")

    def on_mount(self) -> None:
        """Render the active mode + access line."""
        self._refresh()

    def _refresh(self) -> None:
        """Render the mode and access values and tag the access cell."""
        mode = MODES[current_mode(self._ctx)][0]
        level = current_access_level(self._ctx)
        self.query_one("#mode-access-mode", SelectableStatic).update(f"{mode} | ")
        access = self.query_one("#mode-access-access", SelectableStatic)
        access.update(f"{ACCESS_LEVELS[level][0]} access")
        access.set_class(level == "full", "access-full")
        access.set_class(level == "read_only", "access-read-only")


def mode_access_status_extra(ctx: TuiContext) -> ModeAccessSelector:
    """Return the agent mode + access row for the status-panel slots.

    :param ctx: The shared TUI context.
    :returns: The :class:`ModeAccessSelector` widget.
    """
    return ModeAccessSelector(ctx)
