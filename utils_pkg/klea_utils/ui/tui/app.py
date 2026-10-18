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
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static, TextArea
from textual.worker import Worker

from klea_utils.commands.common import (
    CommandRegistry,
    CommandResult,
    unescape_command,
)
from klea_utils.ui.tui.components.commands import (
    EXIT_APP_SERVICE,
    OPEN_MODELS_SERVICE,
    OPEN_SESSIONS_SERVICE,
    TUI_CAPABILITIES,
    CommandSuggestions,
    build_catalogue,
    handle_command,
)
from klea_utils.ui.tui.components.confirm import ExitConfirm
from klea_utils.ui.tui.components.hitl import (
    HitlDecision,
    HitlDecisionPanel,
    HitlPanel,
    PermissionPanel,
)
from klea_utils.ui.tui.components.input import QueryInput, QueryTextArea
from klea_utils.ui.tui.components.inspector import Inspector
from klea_utils.ui.tui.components.model_dialog import ModelDialog, ModelResult
from klea_utils.ui.tui.components.sessions import SessionAction, SessionPicker
from klea_utils.ui.tui.components.status_panel import StatusPanel
from klea_utils.ui.tui.components.transcript import Transcript
from klea_utils.ui.tui.components.turn_status import TurnStatus
from klea_utils.ui.tui.context import TuiContext
from klea_utils.ui.tui.events import SubmitQuery

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
    width: 3fr;
    height: 100%;
    background: $surface;
    padding: 0 1;
}
#status-panel {
    width: 1fr;
    height: 100%;
    background: $panel;
    padding: 0 1;
}
#chat-title {
    height: 1;
    color: $text-muted;
    text-overflow: ellipsis;
}
#chat-name {
    height: 1;
    color: $text-muted;
    text-overflow: ellipsis;
    margin-bottom: 1;
}
#transcript {
    height: 1fr;
}
#view-toggle {
    height: 1;
    text-align: center;
}
#inspector {
    height: 1fr;
}
#key-hints {
    height: 1;
    color: $text-muted;
}
#status {
    height: 1fr;
}
"""

#: Default key-hints line, shown whenever no HITL ask is active.
_KEY_HINTS_DEFAULT = (
    "Enter send | Ctrl+J newline | / commands | "
    "Ctrl+C stop | Ctrl+R retry | Ctrl+I inspector | "
    "Ctrl+Q quit"
)

#: Key-hints line per HITL ask kind, shown while its panel is active.
_ASK_KEY_HINTS = {
    "review": "Approve | Request changes | Cancel (Esc)",
    "input": "Answer | Cancel (Esc)",
    "permission": "Choose per path | Submit | Cancel (Esc)",
}


def _ask_key_hints(ask: dict[str, Any]) -> str:
    """Return the key-hints line for a pending ask.

    :param ask: The interrupt ``data``.
    :returns: The ask's hint line (the input hint as a fallback).
    """
    return _ASK_KEY_HINTS.get(str(ask.get("kind", "input")), _ASK_KEY_HINTS["input"])


def _stamp() -> str:
    """Return the local wall-clock time, formatted like the web transcript."""
    return datetime.now().astimezone().strftime("%X")


def _view_toggle_text(view: str) -> Text:
    """Build the chat-panel view toggle line, bolding the active view.

    :param view: ``"transcript"`` or ``"inspector"``.
    :returns: A Rich ``Text`` renderable for the toggle.
    """
    text = Text()
    for index, (key, label) in enumerate(
        (("transcript", "Chat"), ("inspector", "Inspect"))
    ):
        if index:
            text.append("  |  ")
        text.append(label, style="bold" if view == key else "dim")
    return text


class KleaTuiScreen(Screen):
    """The single screen: a chat panel and a status panel side by side."""

    #: A terminal sends Ctrl+I as a Tab character, and ``Screen``'s default
    #: Tab binding (``focus_next``) would otherwise swallow it.  Bind Tab here
    #: (priority) to always toggle the transcript/inspector view, so Ctrl+I /
    #: Tab works from anywhere on the screen rather than cycling focus.
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("tab", "toggle_view", "Inspector", priority=True),
    ]

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
                yield Static(_view_toggle_text("transcript"), id="view-toggle")
                yield Transcript(id="transcript")
                yield Inspector(id="inspector")
                yield TurnStatus(id="turn-status")
                yield CommandSuggestions(id="command-suggestions")
                yield QueryInput(id="query-input-frame")
                yield Static(_KEY_HINTS_DEFAULT, id="key-hints")
            with Vertical(id="status-panel"):
                yield Static(
                    f"{self.ctx.title} | {self.ctx.server_url}",
                    id="chat-title",
                    markup=False,
                )
                yield Static("", id="chat-name", markup=False)
                yield StatusPanel(ctx=self.ctx, id="status")

    async def on_mount(self) -> None:
        """Focus the query input, show the transcript, hydrate the chat."""
        self.query_one("#query-input", QueryTextArea).focus()
        await self.show_view("transcript")
        cast("KleaTuiApp", self.app).hydrate_active_chat()

    @on(QueryTextArea.Submitted)
    async def on_query_submitted(self, event: QueryTextArea.Submitted) -> None:
        """Hand the submitted input to the app (command or query).

        :param event: The :class:`QueryTextArea.Submitted` event.
        """
        query = event.value.strip()
        if not query:
            return
        self.query_one("#query-input", QueryTextArea).text = ""
        await cast("KleaTuiApp", self.app).submit(query)

    @on(TextArea.Changed)
    def on_query_changed(self, event: TextArea.Changed) -> None:
        """Refresh the command suggestions for the current input.

        :param event: The ``TextArea.Changed`` event.
        """
        app = cast("KleaTuiApp", self.app)
        suggestions = self.query_one("#command-suggestions", CommandSuggestions)
        suggestions.update_for(event.text_area.text, app.catalogue, TUI_CAPABILITIES)

    def on_option_list_option_selected(
        self, event: CommandSuggestions.OptionSelected
    ) -> None:
        """Insert the chosen command into the input (autocomplete).

        :param event: The ``OptionSelected`` event.
        """
        input_widget = self.query_one("#query-input", QueryTextArea)
        input_widget.text = f"/{event.option.id} "
        input_widget.focus()

    @property
    def transcript(self) -> Transcript:
        """Return the transcript widget."""
        return self.query_one("#transcript", Transcript)

    async def append_message(self, message: MessageData) -> None:
        """Append one MIME-aware message to the transcript.

        :param message: A message dict (see ``state.MessageData``).
        """
        await self.transcript.append(message)

    async def render_chat(self, chat: Mapping[str, Any]) -> None:
        """Rebuild the transcript and panes from *chat* (on load/switch).

        :param chat: The chat's data dict.
        """
        await self.transcript.set_messages(chat.get("messages", []))
        entries = chat.get("inspector_entries", [])
        self._inspector_entries = entries
        await self.inspector.set_entries(entries)
        self._inspector_dirty = False
        self.set_chat_header(chat)
        await self.status_panel.set_state(chat, refresh_extras=True)
        await self.show_view("transcript")

    def set_chat_header(self, chat: Mapping[str, Any]) -> None:
        """Show the active chat's name and id in the status-panel header.

        :param chat: The chat's data dict.
        """
        name = str(chat.get("name") or "").strip()
        chat_id = self.ctx.chat_id
        label = f"{name} ({chat_id})" if name and name != chat_id else chat_id
        self.query_one("#chat-name", Static).update(f"Chat: {label}")

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

    async def action_toggle_view(self) -> None:
        """Toggle the transcript/inspector view.

        Backs the priority ``tab`` binding (see :attr:`BINDINGS`): a terminal
        sends Ctrl+I as Tab, so Tab toggles the view from anywhere on the
        screen (rather than cycling focus).
        """
        await self.toggle_view()

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

    def show_composer(self, visible: bool) -> None:
        """Show or hide the query composer (input + suggestions).

        While a HITL ask is active the composer is replaced by the ask panel
        (ADR-0046), so the two never compete for input.

        :param visible: Whether the composer should be shown.
        """
        self.query_one("#query-input-frame").display = visible
        if visible:
            self.query_one("#query-input", QueryTextArea).focus()
        else:
            self.query_one("#command-suggestions", CommandSuggestions).display = False

    def set_key_hints(self, text: str) -> None:
        """Replace the key-hints line.

        :param text: The hint text to show.
        """
        self.query_one("#key-hints", Static).update(text)

    @property
    def status_panel(self) -> StatusPanel:
        """Return the status-panel widget."""
        return self.query_one("#status", StatusPanel)

    async def refresh_status(
        self, chat: Mapping[str, Any] | None, *, refresh_extras: bool = False
    ) -> None:
        """Rebuild the status panel from the active chat's state.

        :param chat: The chat's data dict, or ``None`` when there is none.
        :param refresh_extras: Also rebuild the app slots (a ``context``
            change can alter what a selector should show).
        """
        await self.status_panel.set_state(chat, refresh_extras=refresh_extras)


class KleaTuiApp(App[None]):
    """The shared Klea Textual application.

    Owns the single run slot (invariant I1): at most one ``/query/stream``
    run is in flight, and a second submit is refused while one streams.
    """

    CSS = _CSS
    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "stop", "Stop"),
        Binding("ctrl+r", "retry", "Retry"),
        Binding("ctrl+i", "toggle_view", "Inspector"),
        Binding("ctrl+s", "open_sessions", "Sessions"),
        Binding("ctrl+m", "open_models", "Models"),
        Binding("ctrl+q", "confirm_exit", "Quit"),
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
        #: The interrupt id the pending HITL panel is answering.
        self._hitl_id: str | None = None
        #: The inline panel mounted for a pending ask (``None`` when idle).
        self._hitl_panel: HitlDecisionPanel | None = None
        #: Merged session-command catalogue (client + server); loaded on mount.
        self._catalogue: CommandRegistry | None = None

    def on_mount(self) -> None:
        """Mount the two-panel screen and load the command catalogue."""
        self._screen = KleaTuiScreen(self.ctx)
        self.push_screen(self._screen)
        self._load_catalogue()

    @property
    def catalogue(self) -> CommandRegistry | None:
        """Return the merged command catalogue, or ``None`` if not loaded."""
        return self._catalogue

    @work
    async def _load_catalogue(self) -> None:
        """Fetch the server command catalogue and merge the client commands."""
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.ui.client import fetch_commands

        server_commands = await fetch_commands(self.ctx.server_url)
        self._catalogue = build_catalogue(server_commands)
        logger.debug("command catalogue loaded: %d", len(self._catalogue.all()))

    @work
    async def hydrate_active_chat(self) -> None:
        """Resolve the initial active chat (resume/continue/mint) and load it."""
        chat_id = await self._resolve_initial_chat()
        await self.load_chat(chat_id)

    async def _resolve_initial_chat(self) -> str:
        """Return the chat to open on launch.

        ``--resume <chat_id>`` wins; else ``--continue`` picks the most
        recently updated chat; else the minted id is used.
        """
        if self.ctx.resume_chat_id:
            return self.ctx.resume_chat_id
        if self.ctx.continue_session:
            # Lazy: only needed for --continue.
            from klea_utils.ui.client import fetch_chats

            chats_list = await fetch_chats(self.ctx.server_url, self.ctx.user_id)
            if chats_list:
                return str(chats_list[0]["chat_id"])
        return self.ctx.chat_id

    async def load_chat(self, chat_id: str, *, hydrate: bool = True) -> None:
        """Make *chat_id* the active session and render it.

        Refused while a run is streaming (invariant I2).  Hydration restores
        the chat's messages, session context and any pending HITL ask.

        :param chat_id: The chat to activate.
        :param hydrate: Whether to (re)fetch the chat from the server.
        """
        if self._streaming:
            self.notify(
                "Stop the current run before switching sessions.",
                severity="warning",
            )
            return
        self.ctx.chat_id = chat_id
        # Lazy: the state store is only needed once a chat is loaded.
        from klea_utils.ui.state import ensure_chat

        chat = ensure_chat(self.ctx.user_id, chat_id)
        if hydrate:
            await self._hydrate_chat(chat_id, chat)
        if self._screen is not None:
            await self._screen.render_chat(chat)
        self._present_pending(chat)

    async def _hydrate_chat(self, chat_id: str, chat: Any) -> None:
        """Restore *chat*'s messages, panes, context and pending ask."""
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.ui.client import (
            fetch_chat_context,
            fetch_chat_events,
            fetch_chat_messages,
            server_message_to_data,
        )
        from klea_utils.ui.stream_events import replay_chat_events

        messages = await fetch_chat_messages(
            self.ctx.server_url, self.ctx.user_id, chat_id
        )
        if messages:
            chat["messages"] = [server_message_to_data(msg) for msg in messages]
        # Stored UI events rebuild the inspection/status panes (ADR-0032
        # update); replayed through the shared reducer so a reloaded chat
        # matches the live session.
        events = await fetch_chat_events(self.ctx.server_url, self.ctx.user_id, chat_id)
        if events:
            replay_chat_events(chat, events)
        # Lazy: httpx and the SSE client are only needed for a live server.
        from klea_utils.api.sse import fetch_active_models

        model_info = await fetch_active_models(
            self.ctx.server_url, self.ctx.user_id, chat_id
        )
        if model_info:
            chat["model_info"] = model_info
        body = await fetch_chat_context(self.ctx.server_url, self.ctx.user_id, chat_id)
        context = body.get("context")
        if context:
            chat.setdefault("context", {}).update(context)
        # The checkpointed cumulative token totals (ADR-0032 update) restore
        # the session total without replaying the per-node ``usage`` events.
        usage = body.get("usage")
        if usage:
            chat["token_usage"] = {
                "input_tokens": usage.get("input_tokens", 0),
                "output_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }
        pending = body.get("pending_interrupt")
        if pending:
            chat["interrupt"] = pending
            chat["turn_status"] = {"kind": "awaiting_input"}

    def _present_pending(self, chat: Any) -> None:
        """Open the HITL panel when *chat* has a pending interrupt."""
        pending = chat.get("interrupt")
        if not pending:
            return
        logger.debug("re-presenting pending interrupt: chat=%s", self.ctx.chat_id)
        self._open_hitl(pending)

    async def submit(self, text: str) -> None:
        """Route submitted *text*: an escaped slash, a command, or a query.

        A ``//``-escaped input is sent as a literal ``/`` query.  A client
        command runs locally and its output is rendered; a server command (or a
        non-command) is forwarded as the query, unless a required model or API
        key is missing (then the send is blocked with a warning).

        :param text: The raw input.
        """
        unescaped = unescape_command(text)
        if unescaped is None:
            result = handle_command(
                text,
                self._catalogue,
                TUI_CAPABILITIES,
                services={
                    OPEN_SESSIONS_SERVICE: self._open_sessions_hook,
                    OPEN_MODELS_SERVICE: self._open_models_hook,
                    EXIT_APP_SERVICE: self._exit_hook,
                },
            )
            if result is not None:
                await self._render_command_result(result)
                return
            query = text
        else:
            query = unescaped
        # Client commands run before this guard (so /help works while setup is
        # incomplete); a real query is blocked until the models/keys are set.
        if self._setup_incomplete():
            logger.warning("send blocked: required models or API keys not configured")
            self.notify(
                "Select the required models and API keys before sending (Ctrl+M).",
                severity="warning",
            )
            return
        self.start_run(query)

    def _setup_incomplete(self) -> bool:
        """Whether the active chat still needs required models or API keys."""
        # Lazy: the state store and llm helpers are only needed to send.
        from klea_utils.llm import missing_required_roles
        from klea_utils.ui.state import ensure_chat, missing_credentials

        info = ensure_chat(self.ctx.user_id, self.ctx.chat_id).get("model_info") or {}
        return bool(missing_required_roles(info) or missing_credentials(info))

    def on_submit_query(self, event: SubmitQuery) -> None:
        """Submit a query posted by an app-defined widget (e.g. a selector).

        :param event: The :class:`SubmitQuery` message.
        """
        self.run_worker(self.submit(event.text))

    def _open_sessions_hook(self) -> None:
        """Command-service hook for ``/sessions`` (schedules the picker)."""
        self.run_worker(self.action_open_sessions())

    def _open_models_hook(self) -> None:
        """Command-service hook for ``/model`` (schedules the dialog)."""
        self.run_worker(self.action_open_models())

    def _exit_hook(self) -> None:
        """Command-service hook for ``/exit`` (stop the run, then quit).

        ``/exit`` is deliberate, so it quits without a confirmation.
        """
        self.run_worker(self._exit_after_stop())

    def action_confirm_exit(self) -> None:
        """Open the exit confirmation (``Ctrl+Q``)."""
        self.push_screen(ExitConfirm(), callback=self._on_exit_confirm)

    def _on_exit_confirm(self, confirmed: bool | None) -> None:
        """Quit when the exit confirmation is accepted.

        :param confirmed: ``True`` when the user chose Exit.
        """
        if confirmed:
            self.run_worker(self._exit_after_stop())

    async def _exit_after_stop(self) -> None:
        """Stop the active run (if any), then quit the application."""
        logger.debug(f"exit requested: {self.ctx.chat_id = } {self._streaming = }")
        if self._streaming:
            await self.action_stop()
        self.exit()

    async def _render_command_result(self, result: CommandResult) -> None:
        """Render a client command's result as a ``system`` transcript message.

        :param result: The command's :class:`CommandResult`.
        """
        lines = [*result.output]
        if result.error:
            lines.append(result.error)
        if lines and self._screen is not None:
            # Lazy: the state store is only needed when a command runs.
            from klea_utils.ui.state import ensure_chat

            chat = ensure_chat(self.ctx.user_id, self.ctx.chat_id)
            chat["messages"].append(
                {
                    "text": "\n".join(lines),
                    "stamp": _stamp(),
                    "role": "system",
                    "header": "",
                }
            )
            await self._screen.append_message(chat["messages"][-1])
        if result.notice:
            self.notify(result.notice)

    def start_run(
        self,
        query: str,
        *,
        resume: bool = False,
        interrupt_response: dict[str, Any] | None = None,
        interrupt_id: str | None = None,
        interrupt_cancel: bool = False,
    ) -> None:
        """Start the single run for *query*, refusing a concurrent submit.

        :param query: The user's query text (unused on a resume or an answer).
        :param resume: Resume the chat's last failed run from its checkpoint
            instead of starting a new turn.
        :param interrupt_response: Answer a pending HITL interrupt (ADR-0046).
        :param interrupt_id: Id of the interrupt being answered.
        :param interrupt_cancel: Cancel the pending interrupt instead of
            answering.
        """
        if self._streaming:
            logger.debug("Refusing submit: a run is already streaming")
            self.notify("A run is already in progress.", severity="warning")
            return
        # Reserve the slot synchronously so a rapid second submit is refused
        # before the worker task starts.
        self._streaming = True
        is_query = not resume and interrupt_response is None and not interrupt_cancel
        if is_query:
            self._last_query = query
        self._retryable = False
        self._worker = self._run_query(
            query,
            resume=resume,
            interrupt_response=interrupt_response,
            interrupt_id=interrupt_id,
            interrupt_cancel=interrupt_cancel,
        )

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

    async def action_open_sessions(self) -> None:
        """Open the session picker (Ctrl+S); refused while streaming (I2)."""
        if self._streaming:
            self.notify(
                "Stop the current run before switching sessions.",
                severity="warning",
            )
            return
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.ui.client import fetch_chats

        sessions = await fetch_chats(self.ctx.server_url, self.ctx.user_id)
        self.push_screen(
            SessionPicker(sessions, self.ctx.chat_id),
            callback=self._on_session_choice,
        )

    async def action_open_models(self) -> None:
        """Open the model dialog (Ctrl+M); refused while streaming (I2)."""
        if self._streaming:
            self.notify(
                "Stop the current run before changing models.",
                severity="warning",
            )
            return
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.api.sse import fetch_active_models
        from klea_utils.ui.state import ensure_chat

        chat = ensure_chat(self.ctx.user_id, self.ctx.chat_id)
        info = chat.get("model_info") or {}
        if not info:
            info = await fetch_active_models(
                self.ctx.server_url, self.ctx.user_id, self.ctx.chat_id
            )
            chat["model_info"] = info
        if not info:
            self.notify("No model roles available to configure.", severity="warning")
            return
        self.push_screen(
            ModelDialog(
                server_url=self.ctx.server_url,
                user_id=self.ctx.user_id,
                chat_id=self.ctx.chat_id,
                info=info,
            ),
            callback=self._on_models_closed,
        )

    def _on_models_closed(self, result: ModelResult | None) -> None:
        """Refresh the chat after the model dialog closes with changes.

        :param result: The dialog's :class:`ModelResult`, or ``None``.
        """
        if result and result.changed:
            self._refresh_models()

    @work
    async def _refresh_models(self) -> None:
        """Re-fetch the active models for the current chat."""
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.api.sse import fetch_active_models
        from klea_utils.ui.state import ensure_chat

        info = await fetch_active_models(
            self.ctx.server_url, self.ctx.user_id, self.ctx.chat_id
        )
        chat = ensure_chat(self.ctx.user_id, self.ctx.chat_id)
        chat["model_info"] = info
        if self._screen is not None:
            await self._screen.refresh_status(chat)

    def _on_session_choice(self, action: SessionAction | None) -> None:
        """Handle the picker's choice (switch, rename or delete).

        :param action: The picker's :class:`SessionAction`, or ``None``.
        """
        if action is None:
            return
        if action.kind == "switch":
            self._switch_to_chat(action.chat_id)
        elif action.kind == "rename":
            self._rename_chat(action.chat_id, action.title)
        elif action.kind == "delete":
            self._delete_chat(action.chat_id)

    @work
    async def _switch_to_chat(self, chat_id: str) -> None:
        """Load the chosen chat (reuses :meth:`load_chat`)."""
        await self.load_chat(chat_id)

    @work
    async def _rename_chat(self, chat_id: str, title: str) -> None:
        """Rename a chat on the server, then refresh the picker.

        :param chat_id: The chat to rename.
        :param title: The new title.
        """
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.ui.client import rename_chat_on_server
        from klea_utils.ui.state import ensure_chat

        await rename_chat_on_server(
            self.ctx.server_url, self.ctx.user_id, chat_id, title
        )
        chat = ensure_chat(self.ctx.user_id, chat_id)
        chat["name"] = title
        if chat_id == self.ctx.chat_id and self._screen is not None:
            self._screen.set_chat_header(chat)
        await self.action_open_sessions()

    @work
    async def _delete_chat(self, chat_id: str) -> None:
        """Delete a chat on the server and locally, then refresh the picker.

        If the deleted chat was active, the most recently updated remaining
        chat becomes active (or a fresh one is minted when none remain).

        :param chat_id: The chat to delete.
        """
        # Lazy: httpx and the server client are only needed for a live server.
        from klea_utils.ui.client import delete_chat_on_server, fetch_chats
        from klea_utils.ui.state import chats

        await delete_chat_on_server(self.ctx.server_url, self.ctx.user_id, chat_id)
        chats.pop(f"{self.ctx.user_id}:{chat_id}", None)
        if self.ctx.chat_id == chat_id:
            remaining = await fetch_chats(self.ctx.server_url, self.ctx.user_id)
            if remaining:
                await self.load_chat(str(remaining[0]["chat_id"]))
            else:
                await self._new_chat()
        await self.action_open_sessions()

    async def _new_chat(self) -> None:
        """Activate a fresh, empty chat."""
        # Lazy: coolname only mints the id.
        import coolname

        await self.load_chat(coolname.generate_slug(2), hydrate=False)

    def _open_hitl(self, ask: dict[str, Any]) -> None:
        """Mount the inline HITL panel for *ask* (review / input / permission).

        The panel is a sibling of the transcript in the chat panel, not a modal,
        so the chat stays scrollable while the ask is open (ADR-0046).

        :param ask: The interrupt ``data``.
        """
        self._hitl_id = ask.get("interrupt_id")
        screen = self._screen
        if screen is None:
            return
        if self._hitl_panel is not None:
            self._hitl_panel.remove()
        panel: HitlDecisionPanel = (
            PermissionPanel(ask) if ask.get("kind") == "permission" else HitlPanel(ask)
        )
        self._hitl_panel = panel
        screen.mount(panel, before=screen.query_one("#query-input-frame"))
        # The ask replaces the composer while it is active, so the two never
        # compete for input (the paused thread would reject a new query).
        screen.show_composer(False)
        screen.set_key_hints(_ask_key_hints(ask))

    @on(HitlDecisionPanel.Decided)
    def _on_hitl_decided(self, event: HitlDecisionPanel.Decided) -> None:
        """Remove the answered panel, restore the composer, and resume.

        :param event: The panel's ``Decided`` message.
        """
        if self._hitl_panel is not None:
            self._hitl_panel.remove()
            self._hitl_panel = None
        screen = self._screen
        if screen is not None:
            screen.show_composer(True)
            screen.set_key_hints(_KEY_HINTS_DEFAULT)
        self._on_hitl_decision(event.decision)

    def _on_hitl_decision(self, decision: HitlDecision | None) -> None:
        """Continue the paused run with the panel's answer.

        :param decision: The :class:`HitlDecision`, or ``None``.
        """
        if decision is None:
            return
        self._resume_from_hitl(decision)

    @work
    async def _resume_from_hitl(self, decision: HitlDecision) -> None:
        """Record the answer and continue the paused run (ADR-0046).

        :param decision: The :class:`HitlDecision`.
        """
        # Lazy: the state store is only needed when answering.
        from klea_utils.ui.state import ensure_chat, interrupt_display

        screen = self._screen
        chat = ensure_chat(self.ctx.user_id, self.ctx.chat_id)
        chat["messages"].append(
            {
                "text": interrupt_display(decision.response, cancel=decision.cancel),
                "stamp": _stamp(),
                "role": "user",
                "header": "",
            }
        )
        if screen is not None:
            await screen.append_message(chat["messages"][-1])
        chat.pop("interrupt", None)
        self.start_run(
            "",
            interrupt_response=decision.response,
            interrupt_id=self._hitl_id,
            interrupt_cancel=decision.cancel,
        )

    @work
    async def _run_query(
        self,
        query: str,
        *,
        resume: bool = False,
        interrupt_response: dict[str, Any] | None = None,
        interrupt_id: str | None = None,
        interrupt_cancel: bool = False,
    ) -> None:
        """Stream one run's events into the transcript and turn status.

        Feeds every event through the shared reducer
        (:func:`klea_utils.ui.stream_events.apply_stream_event`), renders
        ``progress`` in the turn-status line and ``complete`` as the final
        transcript message, and clears the run slot on every exit path.  A run
        may be a new query, a failure resume, or a HITL answer/cancel.

        :param query: The user's query text (unused on a resume or an answer).
        :param resume: Resume the chat's last failed run instead of starting a
            new turn (the query is not resent).
        :param interrupt_response: Answer a pending HITL interrupt (ADR-0046).
        :param interrupt_id: Id of the interrupt being answered.
        :param interrupt_cancel: Cancel the pending interrupt.
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
        is_query = not resume and interrupt_response is None and not interrupt_cancel
        logger.debug(
            "run start: user=%s chat=%s resume=%s",
            self.ctx.user_id,
            self.ctx.chat_id,
            resume,
        )
        if is_query:
            # A resume/answer continues an existing turn; its user message is
            # already recorded, so it is not appended again.
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
        # Each turn starts with a clean status pane (mirrors the web).
        chat["state_sections"] = {}
        await screen.refresh_status(chat)
        chat["turn_status"] = {"kind": "progress", "heading": "Working..."}
        screen.show_progress("Working...")

        try:
            async for event in stream_events(
                query,
                self.ctx.chat_id,
                self.ctx.server_url,
                user_id=self.ctx.user_id,
                resume=resume,
                interrupt_response=interrupt_response,
                interrupt_id=interrupt_id,
                interrupt_cancel=interrupt_cancel,
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
                if action in ("state", "usage", "context"):
                    await screen.refresh_status(
                        chat, refresh_extras=(action == "context")
                    )
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
                    # The run paused for HITL input (ADR-0046): stop streaming,
                    # show the status, and open the answer panel.
                    chat["turn_status"] = {"kind": "awaiting_input"}
                    screen.show_awaiting_input()
                    logger.debug("run paused for input: chat=%s", self.ctx.chat_id)
                    self._open_hitl(chat.get("interrupt") or {})
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
    user_id: str = "",
    resume: str = "",
    continue_session: bool = False,
    identity_app_name: str = "",
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
    :param user_id: The ``--user-id`` value; resolved through the ADR-0050
        seam (``--user-id`` > ``KLEA_USER_ID`` > a persisted local UUID).
    :param resume: Chat id to resume on launch (``--resume``).
    :param continue_session: Resume the most recently updated chat
        (``--continue``).
    :param identity_app_name: Canonical identity key for the persisted
        ``user_id`` file, shared with the web frontend of the same app (e.g.
        ``"klea-agent"``); falls back to *app_name* when empty.
    :param status_extras: App-defined status-panel renderers, in order.
    """
    # Lazy: coolname mints the interim chat id; identity resolves the user id.
    import coolname

    # The Textual app owns the whole screen, so console logging would corrupt
    # it: log to the rotating file only (no stdout/stderr handlers).  Lazy:
    # platformdirs/plogging are only needed on launch, keeping --help light.
    from platformdirs import PlatformDirs

    from klea_utils.plogging import setup_root_logger
    from klea_utils.proctitle import set_process_title
    from klea_utils.ui.identity import resolve_user_id

    setup_root_logger(
        app_name,
        log_dir=PlatformDirs(app_name).user_data_dir,
        console=False,
    )
    # Show the app name in ps/top instead of "python".
    set_process_title(app_name)

    resolved_user_id = resolve_user_id(
        explicit=user_id,
        server_url=url,
        app_name=identity_app_name or app_name,
    )
    ctx = TuiContext(
        server_url=url,
        title=title,
        app_prefix=app_prefix,
        app_name=app_name,
        user_id=resolved_user_id,
        chat_id=coolname.generate_slug(2),
        resume_chat_id=resume,
        continue_session=continue_session,
        status_extras=list(status_extras or []),
    )
    logger.info("Launching Klea TUI (app=%s, url=%s, title=%s)", app_name, url, title)
    KleaTuiApp(ctx).run()
