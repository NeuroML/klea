#!/usr/bin/env python3
"""
Shared Textual terminal client.

The Klea TUI is an in-process Textual app that consumes the backend's
``/query/stream`` SSE endpoint, one active session at a time (ADR-0050,
``devdocs/system/tui.md``).  This module holds the shared ``App``/``Screen``,
the two-panel layout, and the single run worker that feeds the shared stream
reducer.  App-specific composition enters through
:class:`klea_utils.ui.tui.context.TuiContext`.

Streaming currently renders ``progress`` (the turn-status line) and
``complete`` (the final message); richer rendering, turn-status states, HITL
and session management land in later steps.

File: klea_utils/ui/tui/app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Any, ClassVar, cast

from textual import work
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


def _stamp() -> str:
    """Return the local wall-clock time, formatted like the web transcript."""
    return datetime.now().astimezone().strftime("%X")


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
        """Hand the submitted query to the app's single run worker.

        :param event: The ``Input.Submitted`` event.
        """
        query = event.value.strip()
        if not query:
            return
        event.input.value = ""
        cast("KleaTuiApp", self.app).start_run(query)

    async def append_message(self, role: str, text: str) -> None:
        """Append one transcript line and scroll to the bottom.

        Rendering is plain text for now; MIME-aware rendering lands in a
        later step.  User lines are prefixed with ``"> "``.

        :param role: ``"user"`` or ``"agent"``.
        :param text: The message text to display.
        """
        for placeholder in self.query("#transcript-empty"):
            placeholder.remove()
        transcript = self.query_one("#transcript", VerticalScroll)
        prefix = "> " if role == "user" else ""
        await transcript.mount(Static(f"{prefix}{text}"))
        transcript.scroll_end(animate=False)

    def set_turn_status(self, text: str) -> None:
        """Update the turn-status line under the transcript.

        :param text: The status text (empty for idle).
        """
        self.query_one("#turn-status", Static).update(text)


class KleaTuiApp(App[None]):
    """The shared Klea Textual application.

    Owns the single run slot (invariant I1): at most one ``/query/stream``
    run is in flight, and a second submit is refused while one streams.
    """

    CSS = _CSS
    BINDINGS: ClassVar[list[Binding]] = [Binding("ctrl+q", "quit", "Quit")]

    def __init__(self, ctx: TuiContext) -> None:
        """Build the app for *ctx*.

        :param ctx: The shared TUI context.
        """
        super().__init__()
        self.ctx = ctx
        self._screen: KleaTuiScreen | None = None
        self._streaming = False

    def on_mount(self) -> None:
        """Mount the two-panel screen."""
        self._screen = KleaTuiScreen(self.ctx)
        self.push_screen(self._screen)

    def start_run(self, query: str) -> None:
        """Start the single run for *query*, refusing a concurrent submit.

        :param query: The user's query text.
        """
        if self._streaming:
            logger.debug("Refusing submit: a run is already streaming")
            self.notify("A run is already in progress.", severity="warning")
            return
        # Reserve the slot synchronously so a rapid second submit is refused
        # before the worker task starts.
        self._streaming = True
        self._run_query(query)

    @work
    async def _run_query(self, query: str) -> None:
        """Stream one query's events into the transcript and turn status.

        Feeds every event through the shared reducer
        (:func:`klea_utils.ui.stream_events.apply_stream_event`), renders
        ``progress`` in the turn-status line and ``complete`` as the final
        transcript message, and clears the run slot on every exit path.

        :param query: The user's query text.
        """
        # Lazy: httpx and the LLM chain are only needed for a live run.
        import httpx

        from klea_utils.api.sse import stream_events
        from klea_utils.ui.state import ensure_chat
        from klea_utils.ui.stream_events import apply_stream_event

        screen = self._screen
        if screen is None:
            self._streaming = False
            return

        chat = ensure_chat(self.ctx.user_id, self.ctx.chat_id)
        logger.debug("run start: user=%s chat=%s", self.ctx.user_id, self.ctx.chat_id)
        chat["messages"].append(
            {"text": query, "stamp": _stamp(), "role": "user", "header": ""}
        )
        await screen.append_message("user", query)
        chat["turn_status"] = {"kind": "progress", "heading": "Working..."}
        screen.set_turn_status("Working...")

        try:
            async for event in stream_events(
                query,
                self.ctx.chat_id,
                self.ctx.server_url,
                user_id=self.ctx.user_id,
            ):
                etype = event.get("type")
                if etype == "ping":
                    # Server heartbeat: keeps the connection warm, nothing to
                    # render.
                    continue
                if etype == "progress":
                    heading = (event.get("data") or {}).get("heading") or event.get(
                        "node", ""
                    )
                    chat["turn_status"] = {"kind": "progress", "heading": heading}
                    screen.set_turn_status(heading)
                    continue
                action = apply_stream_event(chat, event)
                if action == "complete":
                    message = chat["messages"][-1]
                    await screen.append_message("agent", message.get("text", ""))
                    chat["turn_status"] = {"kind": "idle"}
                    screen.set_turn_status("")
                    logger.debug("run complete: chat=%s", self.ctx.chat_id)
                    break
                if action == "error":
                    message = event.get("message", "Unknown error")
                    chat["turn_status"] = {
                        "kind": "error",
                        "message": message,
                        "resumable": bool(event.get("resumable")),
                    }
                    screen.set_turn_status(f"error: {message}")
                    logger.debug("run error: chat=%s: %s", self.ctx.chat_id, message)
                    break
        except httpx.HTTPError as exc:
            # A dropped/timed-out stream or a rejected request (e.g. HTTP 409).
            # Full error handling (Retry, Stop) lands in a later step.
            logger.warning("run transport error: chat=%s: %s", self.ctx.chat_id, exc)
            chat["turn_status"] = {
                "kind": "error",
                "message": f"Request failed: {exc}",
                "resumable": True,
            }
            screen.set_turn_status(f"error: {exc}")
        finally:
            self._streaming = False


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
    # Lazy: only needed to mint the interim chat id until Phase 5 wires the
    # session store / picker.
    import coolname

    ctx = TuiContext(
        server_url=url,
        title=title,
        app_prefix=app_prefix,
        app_name=app_name,
        chat_id=coolname.generate_slug(2),
        status_extras=list(status_extras or []),
    )
    logger.info("Launching Klea TUI (app=%s, url=%s, title=%s)", app_name, url, title)
    KleaTuiApp(ctx).run()
