"""
Generic state-edit HITL form for the web UI.

Renders the ``state_edit`` interrupt payload (``StateEditRequest``): a title,
optional description, and one control per ``StateField``.  The ``path_list``
kind is a list editor with per-entry remove controls and an optional add input;
the other kinds are a text input, switch, or select.  Save submits
``StateEditResponse``; Cancel resumes the run with a cancel.

See ``devdocs/system/session-commands.md``; ADR-0047.

File: klea_utils/ui/web/nicegui/components/state_edit.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from typing import Any

from nicegui import ui

logger = logging.getLogger(__name__)


def _path_list_value(value: Any) -> list[str]:
    """Return *value* as a clean list of path strings."""
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def render_state_edit(ctx: Any, ask: dict[str, Any], interrupt_id: Any) -> None:
    """Render a generic state-editor form and submit its values.

    :param ctx: The page context (provides ``submit_interrupt``).
    :param ask: The interrupt ``data`` (title/description/fields).
    :param interrupt_id: The interrupt identifier to resume.
    """
    title = str(ask.get("title") or "Edit session settings")
    description = str(ask.get("description") or "")
    fields = [field for field in ask.get("fields", []) if isinstance(field, dict)]
    path_values: dict[str, list[str]] = {}
    widgets: dict[str, Any] = {}

    with ui.column().classes("w-full gap-2 p-3 border border-primary rounded"):
        ui.label(title).classes("text-sm font-bold")
        if description:
            ui.label(description).classes("text-xs text-grey-6")

        @ui.refreshable
        def _rows() -> None:
            for field in fields:
                key = str(field.get("key", ""))
                if not key:
                    continue
                kind = str(field.get("kind", "text"))
                ui.label(str(field.get("label") or key)).classes("text-sm font-bold")
                help_text = str(field.get("help") or "")
                if help_text:
                    ui.label(help_text).classes("text-xs text-grey-6")
                if kind == "path_list":
                    paths = path_values.setdefault(
                        key, _path_list_value(field.get("value"))
                    )
                    with ui.column().classes("w-full gap-1"):
                        for index, path in enumerate(paths):
                            with ui.row().classes("w-full items-center no-wrap gap-1"):
                                ui.label(path).classes("text-xs").style(
                                    "white-space: pre-wrap; word-break: break-all"
                                )
                                if field.get("removable", True):
                                    ui.button(
                                        icon="delete",
                                        on_click=lambda k=key, i=index: _remove(
                                            k, i, path_values, _rows
                                        ),
                                    ).props("flat dense round size=sm").classes(
                                        "icon-btn"
                                    )
                        if field.get("addable", True):
                            add_input = (
                                ui.input(placeholder="Add a path")
                                .classes("w-full")
                                .props("outlined dense")
                            )
                            ui.button(
                                "Add",
                                on_click=lambda k=key, inp=add_input: _add(
                                    k, inp, path_values, _rows
                                ),
                            ).props("flat dense")
                elif kind == "text":
                    widgets[key] = (
                        ui.input(value=str(field.get("value") or ""))
                        .classes("w-full")
                        .props("outlined dense")
                    )
                elif kind == "bool":
                    widgets[key] = ui.switch(value=bool(field.get("value")))
                elif kind == "select":
                    options = [str(option) for option in field.get("options") or []]
                    widgets[key] = (
                        ui.select(
                            options=options,
                            value=field.get("value"),
                        )
                        .classes("w-full")
                        .props("outlined dense")
                    )

        _rows()

        def _submit() -> None:
            values = {
                key: widget.value
                for key, widget in widgets.items()
                if hasattr(widget, "value")
            }
            for field in fields:
                if str(field.get("kind", "text")) == "path_list":
                    key = str(field.get("key", ""))
                    values[key] = list(path_values.get(key, []))
            logger.debug(f"Submitting state edit\n{list(values) = }")
            ctx.submit_interrupt(
                {"action": "save", "values": values},
                interrupt_id,
                False,
            )

        with ui.row().classes("gap-2"):
            ui.button("Save", on_click=_submit).props("unelevated color=primary")
            ui.button(
                "Cancel",
                on_click=lambda: ctx.submit_interrupt(None, interrupt_id, True),
            ).props("flat color=negative")


def _remove(
    key: str,
    index: int,
    path_values: dict[str, list[str]],
    refresh: Any,
) -> None:
    """Remove one path from *key* and refresh the form."""
    paths = path_values.get(key, [])
    if 0 <= index < len(paths):
        paths.pop(index)
    refresh()


def _add(
    key: str,
    input_element: Any,
    path_values: dict[str, list[str]],
    refresh: Any,
) -> None:
    """Add the input's path to *key* (if new) and refresh the form."""
    value = str(input_element.value or "").strip()
    if not value:
        return
    paths = path_values.setdefault(key, [])
    if value not in paths:
        paths.append(value)
    input_element.value = ""
    refresh()
