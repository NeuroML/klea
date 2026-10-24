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
from textual.widgets import Collapsible

from klea_utils.ui.inspect_format import format_details
from klea_utils.ui.tui.components.selectable import SelectableStatic

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
    body: list[SelectableStatic] = []
    summary = entry.get("summary", "")
    if summary:
        body.append(SelectableStatic(summary, markup=False))
    details = entry.get("details", {})
    if details:
        # ``format_details`` is arbitrary JSON (arrays/objects); disable markup
        # so brackets are not parsed as Rich tags.
        body.append(SelectableStatic(format_details(details), markup=False))
    if not body:
        body.append(SelectableStatic("(no details)", markup=False))
    return Collapsible(*body, title=title or "(entry)", collapsed=True)


class Inspector(VerticalScroll):
    """The inspector view: per-query sections of streamed inspect entries."""

    def __init__(self, *, id: str | None = None) -> None:
        """Build an empty inspector.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        #: Marker dicts of the mounted sections (``None`` when unrendered or
        #: when the mounted state cannot be trusted for an incremental append).
        self._markers: list[Mapping[str, Any]] | None = None
        #: Mounted section collapsibles, aligned to :attr:`_markers`.
        self._sections: list[Collapsible] = []
        #: Number of entry widgets mounted per section (placeholders excluded).
        self._counts: list[int] = []
        #: The "No entries." placeholder per section, or ``None`` when the
        #: section has entries mounted.
        self._placeholders: list[SelectableStatic | None] = []

    def compose(self) -> ComposeResult:
        """Compose the empty placeholder (replaced on the first refresh)."""
        yield SelectableStatic("No inspection data yet.", id="inspector-empty")

    async def set_entries(self, entries: Sequence[Mapping[str, Any]]) -> None:
        """Update the inspector from the chat's ``inspector_entries``.

        New entries are appended incrementally where possible: an update that
        only adds entries (or opens a new query section) mounts just the tail,
        so already-mounted entries keep their collapsed/expanded state.  A chat
        switch (a different marker sequence) or an untrustworthy mounted state
        falls back to a full rebuild.

        :param entries: The chat's ``inspector_entries`` (markers + entries).
        """
        grouped = group_sections(entries)
        # Tail-follow only: capture the offset first so a user who scrolled up
        # keeps their position across the update, while a user at the bottom
        # still follows the newest entry.
        follow_tail = self.scroll_y >= self.max_scroll_y - 1
        previous_y = self.scroll_y
        if self._can_append(grouped):
            await self._append_tail(grouped)
        else:
            await self._rebuild(grouped, entries)
        if follow_tail:
            self.scroll_end(animate=False)
        else:
            self.scroll_to(y=previous_y, animate=False)

    def _reset_tracking(self) -> None:
        """Forget the mounted incremental state (forcing a full rebuild)."""
        self._markers = None
        self._sections = []
        self._counts = []
        self._placeholders = []

    def _can_append(
        self,
        grouped: Sequence[tuple[Mapping[str, Any], list[Mapping[str, Any]]]],
    ) -> bool:
        """Decide whether *grouped* extends the mounted sections in place.

        The call counts as an append only when every previously-mounted query
        marker is still present, in the same position and with the same
        identity -- so a chat switch, which brings a different chat's marker
        dicts, triggers a rebuild -- and the mounted section widgets are still
        attached.

        :param grouped: The full ``group_sections`` output.
        :returns: ``True`` when the tail can be mounted incrementally.
        """
        if self._markers is None:
            return False
        if len(grouped) < len(self._markers):
            return False
        if len(self.query(".inspector-section")) != len(self._sections):
            return False
        return all(
            marker is old_marker
            for old_marker, (marker, _items) in zip(self._markers, grouped)
        )

    async def _rebuild(
        self,
        grouped: Sequence[tuple[Mapping[str, Any], list[Mapping[str, Any]]]],
        entries: Sequence[Mapping[str, Any]],
    ) -> None:
        """Clear the view and mount every section from scratch.

        :param grouped: The full ``group_sections`` output.
        :param entries: The chat's ``inspector_entries`` (markers + entries).
        """
        await self.remove_children()
        self._reset_tracking()
        if not entries:
            await self.mount(
                SelectableStatic("No inspection data yet.", id="inspector-empty")
            )
            return
        logger.debug("Rendering inspector (items=%d)", len(entries))
        markers: list[Mapping[str, Any]] = []
        sections: list[Collapsible] = []
        counts: list[int] = []
        placeholders: list[SelectableStatic | None] = []
        for marker, items in grouped:
            section, count, placeholder = await self._mount_section(marker, items)
            markers.append(marker)
            sections.append(section)
            counts.append(count)
            placeholders.append(placeholder)
        self._markers = markers
        self._sections = sections
        self._counts = counts
        self._placeholders = placeholders

    async def _mount_section(
        self, marker: Mapping[str, Any], items: Sequence[Mapping[str, Any]]
    ) -> tuple[Collapsible, int, SelectableStatic | None]:
        """Mount one section (its entries or a placeholder) at the end.

        :param marker: The section's per-query marker.
        :param items: The section's inspect entries.
        :returns: ``(section, entry_count, placeholder)`` for tracking.
        """
        children: list[Collapsible | SelectableStatic] = [
            _entry_widget(item) for item in items
        ]
        placeholder: SelectableStatic | None = None
        if not children:
            placeholder = SelectableStatic("No entries.")
            children = [placeholder]
        section = Collapsible(
            *children,
            title=_section_title(marker),
            collapsed=False,
            classes="inspector-section",
        )
        await self.mount(section)
        return section, len(items), placeholder

    async def _append_tail(
        self,
        grouped: Sequence[tuple[Mapping[str, Any], list[Mapping[str, Any]]]],
    ) -> None:
        """Mount only the entries and sections in *grouped* that are not shown.

        :param grouped: The full ``group_sections`` output.
        """
        markers = self._markers
        if markers is None:  # pragma: no cover - _can_append guarantees this
            return
        existing = len(self._sections)
        for index in range(existing):
            _marker, items = grouped[index]
            new_items = items[self._counts[index] :]
            if not new_items:
                continue
            section = self._sections[index]
            placeholder = self._placeholders[index]
            if placeholder is not None:
                await placeholder.remove()
                self._placeholders[index] = None
            contents = section.query_one("Contents")
            await contents.mount_all([_entry_widget(item) for item in new_items])
            self._counts[index] = len(items)
        for marker, items in grouped[existing:]:
            section, count, placeholder = await self._mount_section(marker, items)
            markers.append(marker)
            self._sections.append(section)
            self._counts.append(count)
            self._placeholders.append(placeholder)
