#!/usr/bin/env python3
"""
Exit-confirmation modal for the Klea TUI.

``Ctrl+Q`` opens this before quitting (mirroring the web header's exit button,
which confirms first), because a keypress is easy to hit by accident.  The
``/exit`` command skips it: typing the command is deliberate.

File: klea_utils/ui/tui/components/confirm.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from typing import ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button

from klea_utils.ui.exit_confirm import EXIT_CONFIRM_MESSAGE, EXIT_CONFIRM_TITLE
from klea_utils.ui.tui.components.buttons import action_button
from klea_utils.ui.tui.components.selectable import SelectableStatic

logger = logging.getLogger(__name__)


class ExitConfirm(ModalScreen[bool]):
    """A confirmation modal for quitting the app (``True`` to exit)."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    ExitConfirm {
        align: center middle;
    }
    #exit-dialog {
        width: 60%;
        height: auto;
        border: round $error;
        padding: 1 2;
        background: $surface;
    }
    #exit-title {
        text-style: bold;
        height: 1;
    }
    #exit-body {
        height: auto;
        padding-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the prompt and Cancel / Exit."""
        with Vertical(id="exit-dialog"):
            yield SelectableStatic(EXIT_CONFIRM_TITLE, id="exit-title")
            yield SelectableStatic(
                EXIT_CONFIRM_MESSAGE,
                id="exit-body",
                markup=False,
            )
            with Horizontal(classes="actions"):
                yield action_button("Cancel", id="exit-cancel", variant="default")
                yield action_button("Exit", id="exit-confirm", variant="error")

    def on_mount(self) -> None:
        """Focus Cancel, so a stray Enter does not quit."""
        self.query_one("#exit-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm or cancel the exit.

        :param event: The ``Button.Pressed`` event.
        """
        if event.button.id == "exit-confirm":
            self.dismiss(True)
        elif event.button.id == "exit-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Dismiss without quitting (Escape or Cancel)."""
        self.dismiss(False)
