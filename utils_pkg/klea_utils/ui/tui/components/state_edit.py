"""
Generic state-edit HITL panel for the TUI.

Renders the ``state_edit`` interrupt payload (``StateEditRequest``): a title,
optional description, and one control per ``StateField``.  ``path_list`` is a
scrollable list with per-entry remove and optional add; other kinds are an
input, switch, or select.  Save posts ``StateEditResponse``; Cancel resumes the
run with a cancel.

See ``devdocs/system/session-commands.md``; ADR-0047.

File: klea_utils/ui/tui/components/state_edit.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widgets import Button, Input, Label, ListItem, ListView, Select, Switch

from klea_utils.ui.tui.components.buttons import action_button
from klea_utils.ui.tui.components.hitl import HitlDecision, HitlDecisionPanel
from klea_utils.ui.tui.components.selectable import SelectableStatic

logger = logging.getLogger(__name__)


def _path_list_value(value: Any) -> list[str]:
    """Return *value* as a clean list of path strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


class StateEditPanel(HitlDecisionPanel):
    """An inline editor for generic session-state fields."""

    DEFAULT_CSS = """
    StateEditPanel ListView {
        height: auto;
        max-height: 10;
        border: round $primary 50%;
    }
    StateEditPanel .state-help {
        color: $text-muted;
        height: auto;
    }
    StateEditPanel Input {
        width: 1fr;
    }
    """

    def __init__(self, ask: dict[str, Any]) -> None:
        """Build the editor for *ask*.

        :param ask: The interrupt ``data`` (title/description/fields).
        """
        super().__init__()
        self.ask = ask
        self._fields = [
            field for field in ask.get("fields", []) if isinstance(field, Mapping)
        ]
        self._path_values: dict[str, list[str]] = {
            str(field.get("key", "")): _path_list_value(field.get("value"))
            for field in self._fields
            if str(field.get("kind", "text")) == "path_list"
        }

    def compose(self) -> ComposeResult:
        """Compose the fields and the Save / Cancel row."""
        yield SelectableStatic(
            str(self.ask.get("title") or "Edit session settings"),
            id="state-title",
        )
        description = str(self.ask.get("description") or "")
        if description:
            yield SelectableStatic(description, classes="state-help")
        for field in self._fields:
            key = str(field.get("key", ""))
            if not key:
                continue
            yield SelectableStatic(str(field.get("label") or key))
            help_text = str(field.get("help") or "")
            if help_text:
                yield SelectableStatic(help_text, classes="state-help")
            kind = str(field.get("kind", "text"))
            if kind == "path_list":
                yield ListView(id=f"state-{key}")
                if field.get("removable", True):
                    yield action_button("Remove selected", id=f"rmbtn-{key}")
                if field.get("addable", True):
                    with Horizontal(classes="actions"):
                        yield Input(placeholder="Add a path", id=f"add-{key}")
                        yield action_button("Add", id=f"addbtn-{key}")
            elif kind == "text":
                yield Input(value=str(field.get("value") or ""), id=f"val-{key}")
            elif kind == "bool":
                yield Switch(value=bool(field.get("value")), id=f"val-{key}")
            elif kind == "select":
                options = [str(option) for option in field.get("options") or []]
                current = field.get("value")
                yield Select(
                    [(option, option) for option in options],
                    value=current if current in options else None,
                    allow_blank=True,
                    id=f"val-{key}",
                )
        with Horizontal(classes="actions"):
            yield action_button("Save", id="state-save", variant="primary")
            yield action_button("Cancel", id="state-cancel", variant="default")

    def on_mount(self) -> None:
        """Populate path lists after their widgets exist."""
        for key, paths in self._path_values.items():
            self._refresh_paths(key, paths)

    def _refresh_paths(self, key: str, paths: list[str]) -> None:
        """Rebuild the path list for *key*."""
        list_view = self.query_one(f"#state-{key}", ListView)
        list_view.clear()
        for path in paths:
            list_view.append(ListItem(Label(path)))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle add, remove, save and cancel."""
        button_id = str(event.button.id or "")
        if button_id == "state-save":
            self._submit()
            return
        if button_id == "state-cancel":
            self.action_cancel()
            return
        if button_id.startswith("rmbtn-"):
            key = button_id.removeprefix("rmbtn-")
            self._remove_selected(key)
            return
        if button_id.startswith("addbtn-"):
            key = button_id.removeprefix("addbtn-")
            self._add_from_input(key)

    def _remove_selected(self, key: str) -> None:
        """Remove the highlighted path from *key*."""
        list_view = self.query_one(f"#state-{key}", ListView)
        index = list_view.index
        paths = self._path_values.get(key, [])
        if index is None or not (0 <= index < len(paths)):
            return
        paths.pop(index)
        list_view.pop(index)

    def _add_from_input(self, key: str) -> None:
        """Add the path currently typed for *key*."""
        input_element = self.query_one(f"#add-{key}", Input)
        value = str(input_element.value or "").strip()
        if not value:
            return
        paths = self._path_values.setdefault(key, [])
        if value not in paths:
            paths.append(value)
            self.query_one(f"#state-{key}", ListView).append(ListItem(Label(value)))
        input_element.value = ""

    def _submit(self) -> None:
        """Post the edited values."""
        values: dict[str, Any] = {}
        for field in self._fields:
            key = str(field.get("key", ""))
            if not key:
                continue
            kind = str(field.get("kind", "text"))
            if kind == "path_list":
                values[key] = list(self._path_values.get(key, []))
            elif kind == "text":
                values[key] = self.query_one(f"#val-{key}", Input).value
            elif kind == "bool":
                values[key] = self.query_one(f"#val-{key}", Switch).value
            elif kind == "select":
                values[key] = self.query_one(f"#val-{key}", Select).value
        logger.debug(f"Submitting state edit\n{list(values) = }")
        self.post_message(
            self.Decided(HitlDecision({"action": "save", "values": values}))
        )
