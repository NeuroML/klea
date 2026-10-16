#!/usr/bin/env python3
"""
Query input widget for the Klea TUI.

The chat panel's query entry: a multi-line, soft-wrapping text area that grows
with its content up to a capped height (then scrolls), mirroring the web
client's autogrowing textarea.  The entry sits at the bottom of a themed frame
(so the cursor is on the bottom line and the box grows upward, like the web
composer).  Enter submits the query; Ctrl+J (Textual's ``newline`` key) or
Shift+Enter inserts a newline, so a pasted multi-line block can be edited
before sending.  Ctrl+J is the portable choice: it is a distinct key
(``\\x0a``) sent by every terminal, whereas Shift+Enter is only distinguishable
on terminals that implement the extended (Kitty) keyboard protocol.

File: klea_utils/ui/tui/components/input.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from textual import events
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widgets import TextArea

logger = logging.getLogger(__name__)


class QueryInput(Vertical):
    """The query composer: a themed frame with the entry bottom-aligned.

    The frame keeps a minimum of three rows and grows with the text; because
    the entry is aligned to the bottom, the cursor starts on the bottom line
    and the box grows upward (the web composer's behaviour).  The background
    lives on the frame (``$primary 15%``, theme-aware, matching the user
    transcript blocks) rather than as a border, so the whole padded area reads
    as the input surface.
    """

    DEFAULT_CSS = """
    QueryInput {
        height: auto;
        min-height: 3;
        max-height: 8;
        background: $primary 15%;
        align: left bottom;
        margin-bottom: 1;
    }
    QueryInput QueryTextArea {
        height: auto;
        max-height: 8;
        border: none;
        background: transparent;
    }
    QueryInput QueryTextArea:focus {
        border: none;
    }
    """

    def compose(self) -> ComposeResult:
        """Compose the bottom-aligned query entry."""
        yield QueryTextArea(id="query-input")


class QueryTextArea(TextArea):
    """Multi-line, autogrowing query entry.

    Soft-wraps and grows with its text up to ``max-height`` (then scrolls).
    Enter submits; Ctrl+J (or Shift+Enter on a supporting terminal) inserts a
    newline.
    """

    #: Keys that insert a newline.  ``ctrl+j`` is Textual's ``newline`` alias
    #: and a distinct byte every terminal sends; ``shift+enter`` needs the
    #: extended keyboard protocol, so it cannot replace it.
    _NEWLINE_KEYS = frozenset({"ctrl+j", "newline", "shift+enter"})

    class Submitted(Message):
        """Posted when the user presses Enter to submit the query."""

        def __init__(self, value: str) -> None:
            """Store the submitted text.

            :param value: The current text of the text area.
            """
            super().__init__()
            self.value = value

    def __init__(self, *, id: str | None = None) -> None:
        """Build the query entry.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(
            soft_wrap=True,
            placeholder="Type a query or /command, then Enter...",
            id=id,
        )

    async def _on_key(self, event: events.Key) -> None:
        """Route Enter to submit and a newline key to insert.

        :param event: The key event.
        """
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self.text))
            return
        if event.key in self._NEWLINE_KEYS:
            # TextArea only maps a bare "enter" to a newline; insert it here
            # for Ctrl+J / Shift+Enter so a multi-line entry stays editable.
            event.stop()
            event.prevent_default()
            self.insert("\n")
            return
        await super()._on_key(event)
