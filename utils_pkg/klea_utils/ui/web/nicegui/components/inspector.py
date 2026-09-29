#!/usr/bin/env python3
"""
Inspector panel component: streamed inspect entries for the active chat.

Entries arrive as the graph runs.  The pane appends them incrementally (no
full rebuild per node) and groups them into a collapsible section per
query, marked by the query text and time.  Sections are kept for the
browser session so a chat accumulates its inspection history.

File: klea_utils/ui/web/nicegui/components/inspector.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Mapping, Sequence
from typing import Any

from nicegui import ui

from klea_utils.ui.inspect_format import format_details
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.inspector_code import render_inspector_code
from klea_utils.ui.web.nicegui.state import chats

logger = logging.getLogger(__name__)

#: Maximum query text length shown in a section header.
_MAX_MARKER_CHARS = 80


def _toggle_inspector_entry(ctx: PageContext, idx: int) -> None:
    """Toggle the expanded/collapsed state of an inspector entry."""
    current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
    if not current_chat:
        return
    expanded = current_chat.setdefault("inspector_expanded", set())
    if idx in expanded:
        expanded.discard(idx)
    else:
        expanded.add(idx)


def _toggle_inspector_section(ctx: PageContext, idx: int) -> None:
    """Toggle the collapsed state of an inspector query section."""
    current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
    if not current_chat:
        return
    collapsed = current_chat.setdefault("inspector_sections_collapsed", set())
    if idx in collapsed:
        collapsed.discard(idx)
    else:
        collapsed.add(idx)


def _truncate_query(text: str) -> str:
    """Truncate a query for the section header (full text in a tooltip)."""
    if len(text) <= _MAX_MARKER_CHARS:
        return text
    return text[:_MAX_MARKER_CHARS] + "..."


def attach_inspector_panel(ctx: PageContext) -> None:
    """Build the inspector panel for the active chat in the inspect tab.

    Must be called with the inspect tab panel as the ambient NiceGUI
    context.  Builds the scroll area and content column once (so entries
    can be appended incrementally) and registers the refresh/append
    callbacks on the context.

    :param ctx: The shared page context.
    """
    #: Body element of the query section currently receiving live entries.
    active_body: Any = None
    active_chat_id: str = ""

    def _render_entry(entry: Mapping[str, Any], idx: int) -> None:
        """Render one inspect entry (collapsible details)."""
        heading = entry.get("heading", "")
        timing = entry.get("timing_seconds", None)
        with ui.element("details").props("open").classes("inspector-entry mb-2 w-full"):
            with (  # noqa: SIM117
                ui.element("summary")
                .classes("text-xs font-bold cursor-pointer w-full")
                .on("click", lambda i=idx: _toggle_inspector_entry(ctx, i))
            ):
                with ui.row().classes("w-full flex-nowrap items-center"):
                    ui.label(heading)
                    if timing:
                        ui.label(f"({timing:.1f}s)").classes("text-xs text-grey-5")
            ui.label(entry.get("summary", "")).classes(
                "text-xs text-grey-6 mb-1 w-full"
            )
            details = entry.get("details", {})
            if details:
                with ui.element("details").classes(
                    "inspector-details text-xs text-grey-5 cursor-pointer w-full"
                ):
                    with ui.element("summary").classes("text-xs w-full"):
                        ui.label("View details")
                    # Keep the code box (JSON highlighting + copy button); the
                    # formatter does the readability work (expands multi-line
                    # values such as prompts and inlines JSON-encoded ones,
                    # which json.dumps would escape).  A custom renderer is
                    # used because ``ui.code`` breaks on code fences in the
                    # content and its JSON lexer rejects the expanded newlines.
                    render_inspector_code(format_details(details))

    def _render_section(
        section_idx: int,
        marker: Mapping[str, Any],
        items: list[tuple[int, Mapping[str, Any]]],
        collapsed_sections: set[int],
    ) -> Any:
        """Render one query section; returns its body element."""
        details = ui.element("details").classes("inspector-section mb-2 w-full")
        with details:
            if section_idx not in collapsed_sections:
                details.props("open")
            with (  # noqa: SIM117
                ui.element("summary")
                .classes("cursor-pointer w-full")
                .on(
                    "click",
                    lambda i=section_idx: _toggle_inspector_section(ctx, i),
                )
            ):
                with ui.row().classes("w-full flex-nowrap items-baseline gap-2"):
                    stamp = marker.get("stamp", "")
                    text = marker.get("text", "")
                    if stamp:
                        ui.label(stamp).classes("text-sm text-grey-5")
                    label = ui.label(_truncate_query(text)).classes("text-sm font-bold")
                    if text:
                        label.tooltip(text)
            body = ui.column().classes("w-full gap-0 pl-3")
            with body:
                for idx, entry in items:
                    _render_entry(entry, idx)
        return body

    def _group_sections(
        entries: Sequence[Mapping[str, Any]],
    ) -> list[tuple[Mapping[str, Any], list[tuple[int, Mapping[str, Any]]]]]:
        """Group a flat entry/marker list into per-query sections."""
        sections: list[
            tuple[Mapping[str, Any], list[tuple[int, Mapping[str, Any]]]]
        ] = []
        for idx, item in enumerate(entries):
            if item.get("type") == "query":
                sections.append((item, []))
            else:
                if not sections:
                    sections.append(({"text": "", "stamp": ""}, []))
                sections[-1][1].append((idx, item))
        return sections

    def _render_all() -> None:
        """(Re)build the whole inspector pane from the active chat's data."""
        nonlocal active_body, active_chat_id
        active_body = None
        active_chat_id = ""
        container = ctx.inspector_container
        if container is None:
            return
        container.clear()
        current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
        entries = current_chat.get("inspector_entries", []) if current_chat else []
        with container:
            if not entries:
                ui.label("No inspection data yet").classes("text-sm text-grey-5 py-8")
                ui.label(
                    "Inspector entries will appear here as the graph runs."
                ).classes("text-xs text-grey-5")
                return
            logger.debug(
                "Rendering inspector panel for chat %s (items=%d)",
                ctx.chat_id,
                len(entries),
            )
            collapsed_sections = (
                set(current_chat.get("inspector_sections_collapsed", set()))
                if current_chat
                else set()
            )
            for section_idx, (marker, items) in enumerate(_group_sections(entries)):
                body = _render_section(section_idx, marker, items, collapsed_sections)
                # Keep the last section's body open for live appends when the
                # stream belongs to the active chat.
                if ctx.streaming_chat_id and ctx.streaming_chat_id == ctx.chat_id:
                    active_body = body
                    active_chat_id = ctx.chat_id
        _scroll_bottom()

    def _begin_inspector_section(chat_id: str, marker: Mapping[str, Any]) -> None:
        """Show a new query section for *marker* (data already appended)."""
        if ctx.inspector_container is None or ctx.chat_id != chat_id:
            logger.debug(
                "inspector: not active for chat=%s (active=%s)", chat_id, ctx.chat_id
            )
            return
        _render_all()

    def _append_inspector(chat_id: str, entry: Mapping[str, Any]) -> None:
        """Append one inspect *entry* to the active query section."""
        if active_body is None or active_chat_id != chat_id or ctx.chat_id != chat_id:
            logger.debug(
                "inspector: append skipped for chat=%s (active=%s)",
                chat_id,
                ctx.chat_id,
            )
            return
        current_chat = chats.get(f"{ctx.user_id}:{chat_id}")
        idx = len(current_chat.get("inspector_entries", [])) - 1 if current_chat else 0
        with active_body:
            _render_entry(entry, idx)
        _scroll_bottom()

    def _scroll_bottom() -> None:
        """Scroll the inspector pane to the newest entry."""
        scroll = ctx.inspector_scroll
        if scroll is None:
            return
        try:
            scroll.scroll_to(percent=1.0)
        except Exception as e:  # noqa: BLE001
            logger.debug("inspector scroll failed: %s", e)

    with ui.scroll_area().classes("w-full grow inspector-scroll-area") as scroll:
        ctx.inspector_scroll = scroll
        ctx.inspector_container = ui.column().classes("w-full px-2 gap-0")

    ctx.refresh_inspector = _render_all
    ctx.begin_inspector_section = _begin_inspector_section
    ctx.append_inspector = _append_inspector
    ctx.reset_center_tab = lambda: (
        ctx.center_panels.set_value("chat") if ctx.center_panels is not None else None
    )
    _render_all()
