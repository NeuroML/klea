#!/usr/bin/env python3
"""
Shared Textual terminal client (App/Screen skeleton).

The Klea TUI is an in-process Textual app that consumes the backend's
``/query/stream`` SSE endpoint, one active session at a time (ADR-0050,
``devdocs/system/tui.md``).  This module holds the shared ``App``/``Screen``
and the two-panel layout; app-specific composition enters through
:class:`klea_utils.ui.tui.context.TuiContext`.  Streaming, rendering, HITL and
session management land in later steps.

File: klea_utils/ui/tui/app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from typing import Any, ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static

from klea_utils.ui.tui.context import TuiContext

logger = logging.getLogger(__name__)

#: Two full-height panels: chat (left, wider) and status (right).  There is no
#: separate full-width header/footer; the title and key hints live inside the
#: chat panel (``devdocs/system/tui.md``).
_CSS = """
#root {
    height: 100%;
}
#chat-panel {
    width: 2fr;
    height: 100%;
    border: round $primary;
    padding: 0 1;
}
#status-panel {
    width: 1fr;
    height: 100%;
    border: round $primary;
    padding: 0 1;
}
#chat-title {
    height: 1;
    text-style: bold;
}
#transcript {
    height: 1fr;
    border: solid $panel;
}
#turn-status {
    height: 1;
    color: $text-muted;
}
#key-hints {
    height: 1;
    color: $text-muted;
}
#status-title {
    height: 1;
    text-style: bold;
}
#status-body {
    height: 1fr;
}
"""


class KleaTuiScreen(Screen):
    """The single screen: a chat panel and a status panel side by side."""

    def __init__(self, ctx: TuiContext) -> None:
        """Build the screen for *ctx*.

        :param ctx: The shared TUI context.
        """
        super().__init__()
        self.ctx = ctx

    def compose(self) -> ComposeResult:
        """Compose the two-panel layout."""
        with Horizontal(id="root"):
            with Vertical(id="chat-panel"):
                yield Static(
                    f"{self.ctx.title} | {self.ctx.server_url}", id="chat-title"
                )
                with VerticalScroll(id="transcript"):
                    yield Static("No messages yet.", id="transcript-empty")
                yield Static("idle", id="turn-status")
                yield Input(
                    placeholder="Type a query and press Enter...", id="query-input"
                )
                yield Static("Enter send | Ctrl+Q quit", id="key-hints")
            with Vertical(id="status-panel"):
                yield Static("status", id="status-title")
                with VerticalScroll(id="status-body"):
                    yield Static("mode / access", id="status-mode")
                    yield Static("models", id="status-models")
                    yield Static("state", id="status-state")
                    yield Static("usage", id="status-usage")

    def on_mount(self) -> None:
        """Focus the query input when the screen appears."""
        self.query_one("#query-input", Input).focus()

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Echo the submitted query into the transcript (placeholder).

        Streaming is wired in a later step; this only proves the input is
        routed to the transcript panel.

        :param event: The ``Input.Submitted`` event.
        """
        query = event.value.strip()
        if not query:
            return
        transcript = self.query_one("#transcript", VerticalScroll)
        await transcript.mount(Static(f"> {query}"))
        event.input.value = ""
        transcript.scroll_end(animate=False)


class KleaTuiApp(App[None]):
    """The shared Klea Textual application.

    Holds the single run worker and app-level keybindings (added in later
    steps); for now it only mounts the two-panel screen.
    """

    CSS = _CSS
    BINDINGS: ClassVar[list[Binding]] = [Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, ctx: TuiContext) -> None:
        """Build the app for *ctx*.

        :param ctx: The shared TUI context.
        """
        super().__init__()
        self.ctx = ctx

    def on_mount(self) -> None:
        """Mount the two-panel screen."""
        self.push_screen(KleaTuiScreen(self.ctx))


def run_tui(
    *,
    url: str,
    title: str,
    app_prefix: str = "klea",
    app_name: str = "klea-tui",
    status_extras: Iterable[Callable[[TuiContext], Any]] | None = None,
) -> None:
    """Launch the shared Klea Textual TUI.

    Called by each app's ``run_*_tui`` entry (which supplies any app-specific
    status slots); the CLI resolves that entry and calls it with the server
    URL, title and log identity.

    :param url: Base URL of the backend API server.
    :param title: Application title shown in the chat panel header.
    :param app_prefix: Prompt label prefix (e.g. ``"klea-rag"``).
    :param app_name: Log identity for this frontend (e.g. ``"klea-rag-tui"``).
    :param status_extras: App-defined status-panel renderers, in order.
    """
    ctx = TuiContext(
        server_url=url,
        title=title,
        app_prefix=app_prefix,
        app_name=app_name,
        status_extras=list(status_extras or []),
    )
    logger.info("Launching Klea TUI (app=%s, url=%s, title=%s)", app_name, url, title)
    KleaTuiApp(ctx).run()
