#!/usr/bin/env python3
"""
Session picker modal for the Klea TUI (ADR-0050).

Summoned with ``Ctrl+S`` (or later ``/sessions``), it lists the user's chats
(most recently updated first) and switches to the chosen one.  Rename/delete
land in later steps.  The app fetches the list and passes it in; the modal
returns a :class:`SessionAction`.

File: klea_utils/ui/tui/components/sessions.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, ListItem, ListView, Static

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SessionAction:
    """The picker's outcome.

    :param kind: ``"switch"``, ``"rename"``, ``"delete"`` or ``"cancel"``.
    :param chat_id: The chosen chat id (for ``"switch"``/``"rename"``/
        ``"delete"``).
    :param title: The new title (for ``"rename"``).
    """

    kind: str
    chat_id: str = ""
    title: str = ""


def _session_label(session: dict[str, Any], active: bool) -> Text:
    """Build a picker row label: ``*`` active marker, title, dim chat id."""
    title = str(session.get("title") or session.get("chat_id") or "Untitled")
    chat_id = str(session.get("chat_id", ""))
    text = Text()
    text.append("* " if active else "  ", style="bold")
    text.append(title, style="bold" if active else "")
    text.append(f"  {chat_id}", style="dim")
    return text


class SessionPicker(ModalScreen[SessionAction]):
    """A modal listing the user's chats; Enter/click switches to one."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    SessionPicker {
        align: center middle;
    }
    #sessions-dialog {
        width: 80%;
        height: auto;
        max-height: 80%;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #sessions-title {
        text-style: bold;
        height: 1;
    }
    #sessions-list {
        height: auto;
        max-height: 16;
    }
    """

    def __init__(self, sessions: list[dict[str, Any]], active_id: str) -> None:
        """Build the picker.

        :param sessions: The chats to list (most recently updated first).
        :param active_id: The active chat id (marked in the list).
        """
        super().__init__()
        self._sessions = sessions
        self._active_id = active_id

    def compose(self) -> ComposeResult:
        """Compose the session list and Cancel."""
        with Vertical(id="sessions-dialog"):
            yield Static("Sessions", id="sessions-title")
            if not self._sessions:
                yield Static("No sessions yet.")
            else:
                yield ListView(
                    *[
                        ListItem(
                            Label(
                                _session_label(
                                    session,
                                    str(session.get("chat_id")) == self._active_id,
                                )
                            ),
                            id=str(session.get("chat_id")),
                        )
                        for session in self._sessions
                    ],
                    id="sessions-list",
                )
            with Horizontal():
                yield Button("Rename", id="sessions-rename")
                yield Button("Delete", id="sessions-delete")
                yield Button("Cancel", id="sessions-cancel", variant="error")

    def on_mount(self) -> None:
        """Focus the list (or the Cancel button when empty)."""
        if self._sessions:
            self.query_one("#sessions-list", ListView).focus()
        else:
            self.query_one("#sessions-cancel", Button).focus()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        """Switch to the selected session.

        :param event: The ``ListView.Selected`` event.
        """
        self.dismiss(SessionAction("switch", chat_id=str(event.item.id)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Rename/delete the highlighted session, or cancel.

        :param event: The ``Button.Pressed`` event.
        """
        if event.button.id == "sessions-rename":
            self._start_rename()
        elif event.button.id == "sessions-delete":
            self._delete_highlighted()
        elif event.button.id == "sessions-cancel":
            self.action_cancel()

    def _highlighted_id(self) -> str | None:
        """Return the highlighted session's chat id, or ``None``."""
        item = self.query_one("#sessions-list", ListView).highlighted_child
        return str(item.id) if item is not None else None

    def _start_rename(self) -> None:
        """Prompt for a new title for the highlighted session."""
        chat_id = self._highlighted_id()
        if chat_id is None:
            return
        title = next(
            (
                str(session.get("title", ""))
                for session in self._sessions
                if str(session.get("chat_id")) == chat_id
            ),
            "",
        )
        self.app.push_screen(
            SessionRename(title),
            callback=lambda new_title: self._finish_rename(chat_id, new_title),
        )

    def _finish_rename(self, chat_id: str, new_title: str | None) -> None:
        """Dismiss with a rename action when a new title was entered."""
        if new_title:
            self.dismiss(SessionAction("rename", chat_id=chat_id, title=new_title))

    def _delete_highlighted(self) -> None:
        """Confirm, then dismiss with a delete action for the session.

        Deleting is irreversible, so it is gated behind a confirmation modal
        (mirroring the web UI); only a confirmed delete dismisses the picker.
        """
        chat_id = self._highlighted_id()
        if chat_id is None:
            return
        title = next(
            (
                str(session.get("title") or session.get("chat_id") or "Untitled")
                for session in self._sessions
                if str(session.get("chat_id")) == chat_id
            ),
            "this session",
        )
        self.app.push_screen(
            SessionDelete(title),
            callback=lambda confirmed: (
                self._finish_delete(chat_id) if confirmed else None
            ),
        )

    def _finish_delete(self, chat_id: str) -> None:
        """Dismiss with a delete action once the user confirms."""
        self.dismiss(SessionAction("delete", chat_id=chat_id))

    def action_cancel(self) -> None:
        """Dismiss without switching (Escape or Cancel)."""
        self.dismiss(SessionAction("cancel"))


class SessionDelete(ModalScreen[bool]):
    """A confirmation modal for deleting a session (``True`` to delete)."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    SessionDelete {
        align: center middle;
    }
    #delete-dialog {
        width: 60%;
        height: auto;
        border: round $error;
        padding: 1 2;
        background: $surface;
    }
    #delete-title {
        text-style: bold;
        height: 1;
    }
    #delete-body {
        height: auto;
        padding-bottom: 1;
    }
    """

    def __init__(self, title: str) -> None:
        """Build the confirmation for the session *title*.

        :param title: The session's display title.
        """
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        """Compose the warning and Cancel / Delete."""
        with Vertical(id="delete-dialog"):
            yield Static("Delete session?", id="delete-title")
            yield Static(
                f"This will permanently delete the session {self._title!r} "
                "and its messages. This cannot be undone.",
                id="delete-body",
                markup=False,
            )
            with Horizontal():
                yield Button("Cancel", id="delete-cancel")
                yield Button("Delete", id="delete-confirm", variant="error")

    def on_mount(self) -> None:
        """Focus Cancel, so a stray Enter does not delete."""
        self.query_one("#delete-cancel", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Confirm or cancel the deletion.

        :param event: The ``Button.Pressed`` event.
        """
        if event.button.id == "delete-confirm":
            self.dismiss(True)
        elif event.button.id == "delete-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Dismiss without deleting (Escape or Cancel)."""
        self.dismiss(False)


class SessionRename(ModalScreen[str | None]):
    """A small modal collecting a new session title (``None`` on cancel)."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    SessionRename {
        align: center middle;
    }
    #rename-dialog {
        width: 60%;
        height: auto;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #rename-title {
        text-style: bold;
        height: 1;
    }
    """

    def __init__(self, title: str) -> None:
        """Build the modal prefilled with *title*.

        :param title: The session's current title.
        """
        super().__init__()
        self._title = title

    def compose(self) -> ComposeResult:
        """Compose the title input and Save / Cancel."""
        with Vertical(id="rename-dialog"):
            yield Static("Rename session", id="rename-title")
            yield Input(value=self._title, id="rename-input")
            with Horizontal():
                yield Button("Save", id="rename-save", variant="primary")
                yield Button("Cancel", id="rename-cancel", variant="error")

    def on_mount(self) -> None:
        """Focus the input."""
        self.query_one("#rename-input", Input).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Save the new title, or cancel.

        :param event: The ``Button.Pressed`` event.
        """
        if event.button.id == "rename-save":
            new_title = self.query_one("#rename-input", Input).value.strip()
            self.dismiss(new_title or None)
        elif event.button.id == "rename-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Dismiss without renaming (Escape or Cancel)."""
        self.dismiss(None)
