#!/usr/bin/env python3
"""
Turn-status widget for the Klea TUI.

The line under the transcript shows the active turn's state (ADR-0050,
``devdocs/system/tui.md``): an animated spinner and heading while a run
streams, an error with a Retry hint, a "stopped" note after a user cancel,
or an awaiting-input note while the run is paused for HITL.  It mirrors the
web chat-area turn-status region.

File: klea_utils/ui/tui/components/turn_status.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

logger = logging.getLogger(__name__)

#: ASCII spinner frames (repo convention: no non-ASCII in code), advanced by
#: the widget's interval timer while a run streams.
_SPINNER_FRAMES = "|/-\\"
_SPINNER_INTERVAL = 0.1


class TurnStatus(Widget):
    """The per-turn status line under the transcript."""

    DEFAULT_CSS = """
    TurnStatus {
        layout: horizontal;
        height: 1;
    }
    TurnStatus #turn-spinner {
        width: 2;
    }
    TurnStatus #turn-label {
        width: 1fr;
        color: $text-muted;
    }
    """

    def __init__(self, *, id: str | None = None) -> None:
        """Build an idle turn-status line.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        self._frame = 0
        self._timer: Timer | None = None

    def compose(self) -> ComposeResult:
        """Compose the spinner cell and the label."""
        yield Static("", id="turn-spinner")
        yield Static("", id="turn-label")

    def _tick(self) -> None:
        """Advance the spinner by one frame."""
        self._frame = (self._frame + 1) % len(_SPINNER_FRAMES)
        self.query_one("#turn-spinner", Static).update(_SPINNER_FRAMES[self._frame])

    def _start_spinner(self) -> None:
        """Start the spinner animation if it is not already running."""
        if self._timer is None:
            self._timer = self.set_interval(_SPINNER_INTERVAL, self._tick)

    def _stop_spinner(self) -> None:
        """Stop the spinner animation and clear its cell."""
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
        self.query_one("#turn-spinner", Static).update("")

    def _label(self, text: str) -> None:
        """Set the status label text.

        :param text: The label text (empty for idle).
        """
        self.query_one("#turn-label", Static).update(text)

    def show_progress(self, heading: str) -> None:
        """Show an in-flight run: spinner plus live heading.

        :param heading: The live progress heading.
        """
        self._start_spinner()
        self._label(heading)

    def show_idle(self) -> None:
        """Clear the status line (turn finished)."""
        self._stop_spinner()
        self._label("")

    def show_error(self, message: str, resumable: bool) -> None:
        """Show a failed run, with a Retry hint when it can be resumed.

        :param message: The error message.
        :param resumable: Whether a Retry is available (``Ctrl+R``).
        """
        self._stop_spinner()
        hint = "  [Ctrl+R retry]" if resumable else ""
        self._label(f"error: {message}{hint}")

    def show_stopped(self) -> None:
        """Show that the user stopped the run."""
        self._stop_spinner()
        self._label("stopped")

    def show_awaiting_input(self) -> None:
        """Show that the run paused for HITL input (ADR-0046)."""
        self._stop_spinner()
        self._label("awaiting input")
