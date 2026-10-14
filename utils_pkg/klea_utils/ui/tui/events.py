#!/usr/bin/env python3
"""
Shared Textual messages for the Klea TUI.

App-defined widgets (e.g. the agent's mode/access selectors) need to ask the
shared app to run a query without importing it.  They post a
:class:`SubmitQuery`, which :class:`~klea_utils.ui.tui.app.KleaTuiApp` handles
by routing the text through :meth:`~klea_utils.ui.tui.app.KleaTuiApp.submit`
(so commands and the setup gating still apply).

File: klea_utils/ui/tui/events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from textual.message import Message


class SubmitQuery(Message):
    """Ask the app to submit *text* (a query or a ``/command``).

    :param text: The text to submit.
    """

    def __init__(self, text: str) -> None:
        """Store the text to submit."""
        self.text = text
        super().__init__()
