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

import asyncio
import logging
from collections.abc import Callable, Iterable, Mapping, Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, ClassVar, cast

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import Screen
from textual.widgets import Input, Static
from textual.worker import Worker

from klea_utils.ui.tui.components.inspector import Inspector
from klea_utils.ui.tui.components.transcript import Transcript
from klea_utils.ui.tui.components.turn_status import TurnStatus
from klea_utils.ui.tui.context import TuiContext

if TYPE_CHECKING:
    from klea_utils.ui.state import MessageData

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
#view-toggle {
    height: 1;
}
#inspector {
    height: 1fr;
    border: solid $panel;
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


def _view_toggle_text(view: str) -> Text:
    """Build the chat-panel view toggle line, bolding the active view.

    :param view: ``"transcript"`` or ``"inspector"``.
    :returns: A Rich ``Text`` renderable for the toggle.
    """
    text = Text()
    for index, name in enumerate(("transcript", "inspector")):
        if index:
            text.append("  |  ")
        text.append(name, style="bold" if view == name else "dim")
    return text


class KleaTuiScreen(Screen):
    """The single screen: a chat panel and a status panel side by side."""

    def __init__(self, ctx: TuiContext) -> None:
        """Build the screen for *ctx*.

        :param ctx: The shared TUI context.
        """
        super().__init__()
        self.ctx = ctx
        #: Active chat-panel view: ``"transcript"`` or ``"inspector"``.
        self._view = "transcript"
        #: The active chat's ``inspector_entries`` (shared reference).
        self._inspector_entries: Sequence[Mapping[str, Any]] = []
        #: Whether the inspector needs a rebuild when next shown.
        self._inspector_dirty = False

    def compose(self) -> ComposeResult:
        """Compose the two-panel layout."""
        with Horizontal(id="root"):
            with Vertical(id="chat-panel"):
                yield Static(
                    f"{self.ctx.title} | {self.ctx.server_url}", id="chat-title"
                )
                yield Static(_view_toggle_text("transcript"), id="view-toggle")
                yield Transcript(id="transcript")
                yield Inspector(id="inspector")
                yield TurnStatus(id="turn-status")
                yield Input(
                    placeholder="Type a query and press Enter...", id="query-input"
                )
                yield Static(
                    "Enter send | Ctrl+C stop | Ctrl+R retry | Ctrl+I view | Ctrl+Q quit",
                    id="key-hints",
                )
            with Vertical(id="status-panel"):
                yield Static("status", id="status-title")
                with VerticalScroll(id="status-body"):
                    yield Static("mode / access", id="status-mode")
                    yield Static("models", id="status-models")
                    yield Static("state", id="status-state")
                    yield Static("usage", id="status-usage")

    async def on_mount(self) -> None:
        """Focus the query input and show the transcript view."""
        self.query_one("#query-input", Input).focus()
        await self.show_view("transcript")

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        """Hand the submitted query to the app's single run worker.

        :param event: The ``Input.Submitted`` event.
        """
        query = event.value.strip()
        if not query:
            return
        event.input.value = ""
        cast("KleaTuiApp", self.app).start_run(query)

    @property
    def transcript(self) -> Transcript:
        """Return the transcript widget."""
        return self.query_one("#transcript", Transcript)

    async def append_message(self, message: MessageData) -> None:
        """Append one MIME-aware message to the transcript.

        :param message: A message dict (see ``state.MessageData``).
        """
        await self.transcript.append(message)

    @property
    def turn_status(self) -> TurnStatus:
        """Return the turn-status widget."""
        return self.query_one("#turn-status", TurnStatus)

    def show_progress(self, heading: str) -> None:
        """Show an in-flight run in the turn-status line.

        :param heading: The live progress heading.
        """
        self.turn_status.show_progress(heading)

    def show_idle(self) -> None:
        """Clear the turn-status line (turn finished)."""
        self.turn_status.show_idle()

    def show_error(self, message: str, resumable: bool) -> None:
        """Show a failed run, with a Retry hint when resumable.

        :param message: The error message.
        :param resumable: Whether a Retry is available (``Ctrl+R``).
        """
        self.turn_status.show_error(message, resumable)

    def show_stopped(self) -> None:
        """Show that the user stopped the run."""
        self.turn_status.show_stopped()

    def show_awaiting_input(self) -> None:
        """Show that the run paused for HITL input."""
        self.turn_status.show_awaiting_input()

    @property
    def inspector(self) -> Inspector:
        """Return the inspector widget."""
        return self.query_one("#inspector", Inspector)

    async def refresh_inspector(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """Refresh the inspector from the chat's ``inspector_entries``.

        Rebuilds immediately when the inspector is the active view; otherwise
        the entries are kept and the view is marked dirty for the next switch.

        :param entries: The chat's ``inspector_entries`` (shared reference).
        """
        self._inspector_entries = entries
        if self._view == "inspector":
            await self.inspector.set_entries(entries)
            self._inspector_dirty = False
        else:
            self._inspector_dirty = True

    async def toggle_view(self) -> None:
        """Switch the chat panel between the transcript and inspector views."""
        await self.show_view(
            "inspector" if self._view == "transcript" else "transcript"
        )

    async def show_view(self, view: str) -> None:
        """Show *view* in the chat panel, hiding the other.

        :param view: ``"transcript"`` or ``"inspector"``.
        """
        self._view = view
        self.transcript.display = view == "transcript"
        self.inspector.display = view == "inspector"
        self.query_one("#view-toggle", Static).update(_view_toggle_text(view))
        if view == "inspector" and self._inspector_dirty:
            await self.inspector.set_entries(self._inspector_entries)
            self._inspector_dirty = False


class KleaTuiApp(App[None]):
    """The shared Klea Textual application.

    Owns the single run slot (invariant I1): at most one ``/query/stream``
    run is in flight, and a second submit is refused while one streams.
    """

    CSS = _CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "stop", "Stop"),
        Binding("ctrl+r", "retry", "Retry"),
        Binding("ctrl+i", "toggle_view", "Transcript/Inspector"),
        Binding("ctrl+q", "quit", "Quit"),
    ]

    def __init__(self, ctx: TuiContext) -> None:
        """Build the app for *ctx*.

        :param ctx: The shared TUI context.
        """
        super().__init__()
        self.ctx = ctx
        self._screen: KleaTuiScreen | None = None
        self._streaming = False
        self._worker: Worker[None] | None = None
        self._last_query = ""
        self._retryable = False

    def on_mount(self) -> None:
        """Mount the two-panel screen."""
        self._screen = KleaTuiScreen(self.ctx)
        self.push_screen(self._screen)

    def start_run(self, query: str, *, resume: bool = False) -> None:
        """Start the single run for *query*, refusing a concurrent submit.

        :param query: The user's query text (unused on a resume).
        :param resume: Resume the chat's last failed run from its checkpoint
            instead of starting a new turn.
        """
        if self._streaming:
            logger.debug("Refusing submit: a run is already streaming")
            self.notify("A run is already in progress.", severity="warning")
            return
        # Reserve the slot synchronously so a rapid second submit is refused
        # before the worker task starts.
        self._streaming = True
        if not resume:
            self._last_query = query
        self._retryable = False
        self._worker = self._run_query(query, resume)

    async def action_stop(self) -> None:
        """Stop the active run: cancel locally and ask the server to cancel."""
        if not self._streaming:
            return
        logger.debug("stop requested: chat=%s", self.ctx.chat_id)
        if self._worker is not None:
            self._worker.cancel()
        # Lazy: httpx is only needed to reach the server.
        from klea_utils.api.sse import request_cancel

        await request_cancel(
            self.ctx.server_url, self.ctx.chat_id, user_id=self.ctx.user_id
        )

    def action_retry(self) -> None:
        """Resume the chat's last failed run from its checkpoint (Retry)."""
        if self._streaming or not self._retryable:
            return
        logger.debug("retry requested: chat=%s", self.ctx.chat_id)
        self.start_run(self._last_query, resume=True)

    async def action_toggle_view(self) -> None:
        """Switch the chat panel between the transcript and inspector views."""
        if self._screen is not None:
            await self._screen.toggle_view()

    @work
    async def _run_query(self, query: str, resume: bool = False) -> None:
        """Stream one query's events into the transcript and turn status.

        Feeds every event through the shared reducer
        (:func:`klea_utils.ui.stream_events.apply_stream_event`), renders
        ``progress`` in the turn-status line and ``complete`` as the final
        transcript message, and clears the run slot on every exit path.

        :param query: The user's query text (unused on a resume).
        :param resume: Resume the chat's last failed run instead of starting a
            new turn (the query is not resent).
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
        logger.debug(
            "run start: user=%s chat=%s resume=%s",
            self.ctx.user_id,
            self.ctx.chat_id,
            resume,
        )
        if not resume:
            # A resume re-runs the failed turn; its user message is already
            # recorded, so it is not appended again.
            chat["messages"].append(
                {"text": query, "stamp": _stamp(), "role": "user", "header": ""}
            )
            await screen.append_message(chat["messages"][-1])
            # Start a new inspector section for this query; the reducer appends
            # its entries after this marker.
            chat.setdefault("inspector_entries", []).append(
                {"type": "query", "text": query, "stamp": _stamp()}
            )
            await screen.refresh_inspector(chat["inspector_entries"])
        chat["turn_status"] = {"kind": "progress", "heading": "Working..."}
        screen.show_progress("Working...")

        try:
            async for event in stream_events(
                query,
                self.ctx.chat_id,
                self.ctx.server_url,
                user_id=self.ctx.user_id,
                resume=resume,
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
                    screen.show_progress(heading)
                    continue
                before = len(chat["messages"])
                action = apply_stream_event(chat, event)
                # Render whatever the reducer appended (tool blocks and, on
                # completion, the final message), MIME-aware.
                for message in chat["messages"][before:]:
                    await screen.append_message(message)
                if action == "inspect":
                    await screen.refresh_inspector(chat["inspector_entries"])
                    continue
                if action == "complete":
                    chat["turn_status"] = {"kind": "idle"}
                    screen.show_idle()
                    logger.debug("run complete: chat=%s", self.ctx.chat_id)
                    break
                if action == "error":
                    message = event.get("message", "Unknown error")
                    resumable = bool(event.get("resumable"))
                    self._retryable = resumable
                    chat["turn_status"] = {
                        "kind": "error",
                        "message": message,
                        "resumable": resumable,
                    }
                    screen.show_error(message, resumable)
                    logger.debug("run error: chat=%s: %s", self.ctx.chat_id, message)
                    break
                if action == "interrupt":
                    # The run paused for HITL input (ADR-0046): stop streaming
                    # and show the status; the modal/answer lands in a later
                    # step.
                    chat["turn_status"] = {"kind": "awaiting_input"}
                    screen.show_awaiting_input()
                    logger.debug("run paused for input: chat=%s", self.ctx.chat_id)
                    break
        except asyncio.CancelledError:
            # The user pressed Stop: record the status and let the worker end
            # cancelled.
            chat["turn_status"] = {"kind": "stopped"}
            screen.show_stopped()
            logger.debug("run stopped by user: chat=%s", self.ctx.chat_id)
            raise
        except httpx.HTTPError as exc:
            # A dropped/timed-out stream or a rejected request (e.g. HTTP 409).
            # The run is checkpointed server-side, so Retry is offered.
            logger.warning("run transport error: chat=%s: %s", self.ctx.chat_id, exc)
            self._retryable = True
            chat["turn_status"] = {
                "kind": "error",
                "message": f"Request failed: {exc}",
                "resumable": True,
            }
            screen.show_error(f"Request failed: {exc}", True)
        finally:
            self._streaming = False
            self._worker = None


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
