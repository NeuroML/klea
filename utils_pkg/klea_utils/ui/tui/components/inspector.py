#!/usr/bin/env python3
"""
Inspector view for the Klea TUI.

The chat panel's second view (toggled from the transcript): the streamed
``inspect`` entries for the active chat, grouped into a collapsible section
per query (marked by the query text and time) and rendered with
:func:`klea_utils.ui.inspect_format.format_details` so multi-line values such
as prompts read as text.  Mirrors the web inspector pane
(``devdocs/system/tui.md``).

File: klea_utils/ui/tui/components/inspector.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from klea_utils.ui.inspect_format import format_details

logger = logging.getLogger(__name__)

#: Maximum query text length shown in a section title.
_MAX_MARKER_CHARS = 80


def _truncate_query(text: str) -> str:
    """Truncate a query for the section title.

    :param text: The query text.
    :returns: The text, truncated with an ellipsis when too long.
    """
    if len(text) <= _MAX_MARKER_CHARS:
        return text
    return text[:_MAX_MARKER_CHARS] + "..."


def group_sections(
    entries: Sequence[Mapping[str, Any]],
) -> list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]]:
    """Group a flat entry/marker list into per-query sections.

    The list is a per-query :class:`~klea_utils.ui.state.InspectorMarker`
    followed by that query's entries; a leading marker-less run is placed in a
    synthetic section so no entry is dropped.

    :param entries: The chat's ``inspector_entries``.
    :returns: ``(marker, [entry, ...])`` pairs in order.
    """
    sections: list[tuple[Mapping[str, Any], list[Mapping[str, Any]]]] = []
    for item in entries:
        if item.get("type") == "query":
            sections.append((item, []))
        else:
            if not sections:
                sections.append(({"text": "", "stamp": ""}, []))
            sections[-1][1].append(item)
    return sections


def _section_title(marker: Mapping[str, Any]) -> str:
    """Build the title for a query section.

    :param marker: The per-query marker.
    :returns: ``"<stamp>  <query>"`` (either part omitted when empty).
    """
    stamp = marker.get("stamp", "")
    text = _truncate_query(marker.get("text", ""))
    return f"{stamp}  {text}".strip() or "(query)"


def _entry_widget(entry: Mapping[str, Any]) -> Collapsible:
    """Build the collapsible widget for one inspect entry.

    The title carries the node heading and timing; the body carries the
    summary and the formatted ``details`` (collapsed by default).

    :param entry: An ``inspect`` entry.
    :returns: A collapsed :class:`~textual.widgets.Collapsible`.
    """
    heading = entry.get("heading", "") or entry.get("node", "")
    timing = entry.get("timing_seconds")
    title = f"{heading} ({timing:.1f}s)" if timing else heading
    body: list[Static] = []
    summary = entry.get("summary", "")
    if summary:
        body.append(Static(summary, markup=False))
    details = entry.get("details", {})
    if details:
        # ``format_details`` is arbitrary JSON (arrays/objects); disable markup
        # so brackets are not parsed as Rich tags.
        body.append(Static(format_details(details), markup=False))
    if not body:
        body.append(Static("(no details)", markup=False))
    return Collapsible(*body, title=title or "(entry)", collapsed=True)


class Inspector(VerticalScroll):
    """The inspector view: per-query sections of streamed inspect entries."""

    def __init__(self, *, id: str | None = None) -> None:
        """Build an empty inspector.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        """Compose the empty placeholder (replaced on the first refresh)."""
        yield Static("No inspection data yet.", id="inspector-empty")

    async def set_entries(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """Rebuild the inspector from the chat's ``inspector_entries``.

        :param entries: The chat's ``inspector_entries`` (markers + entries).
        """
        await self.remove_children()
        if not entries:
            await self.mount(Static("No inspection data yet.", id="inspector-empty"))
            return
        logger.debug("Rendering inspector (items=%d)", len(entries))
        for marker, items in group_sections(entries):
            children = [_entry_widget(item) for item in items] or [
                Static("No entries.")
            ]
            await self.mount(
                Collapsible(
                    *children,
                    title=_section_title(marker),
                    collapsed=False,
                )
            )
        self.scroll_end(animate=False)
