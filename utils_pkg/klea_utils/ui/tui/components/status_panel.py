#!/usr/bin/env python3
"""
Status panel for the Klea TUI.

The full-height right panel: the chat's streamed state (``devdocs/system/tui.md``)
- the graph-level session ``context`` (ADR-0032), the per-node ``state``
sections keyed by section key, and the accumulated token usage.  Each state
section renders like the web status pane: a collapsible heading with the
node's ``display`` (Markdown, or a literal block when ``preformatted``), its
``summary`` and its ``details`` via
:func:`klea_utils.ui.inspect_format.format_details`.

File: klea_utils/ui/tui/components/status_panel.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Collapsible, Static

from klea_utils.ui.inspect_format import format_details

logger = logging.getLogger(__name__)


def _usage_line(usage: Mapping[str, Any]) -> str:
    """Format the token usage totals.

    :param usage: The chat's ``token_usage`` dict.
    :returns: ``"tokens: <in> in / <out> out"``.
    """
    return (
        f"tokens: {usage.get('input_tokens', 0)} in / "
        f"{usage.get('output_tokens', 0)} out"
    )


def _context_widget(context: Mapping[str, Any]) -> Collapsible:
    """Build the collapsible widget for the graph-level session context.

    :param context: The chat's ``context`` dict.
    :returns: A collapsed :class:`~textual.widgets.Collapsible`.
    """
    lines = [Static(f"{key}: {value}", markup=False) for key, value in context.items()]
    return Collapsible(*lines, title="context", collapsed=True)


def _section_widget(key: str, section: Mapping[str, Any]) -> Collapsible:
    """Build the collapsible widget for one ``state`` section.

    :param key: The section key (``data.key`` or node label).
    :param section: The stored section (heading/display/summary/details/...).
    :returns: An open :class:`~textual.widgets.Collapsible`.
    """
    heading = section.get("heading", "") or key
    display = section.get("display", "")
    summary = section.get("summary", "")
    details = section.get("details", {})
    body: list[Static] = []
    if display:
        if section.get("preformatted"):
            # Aligned/literal content (e.g. the plan): render as-is so columns
            # line up and markup-like characters are not interpreted.
            body.append(Static(display, markup=False))
        else:
            body.append(Static(Markdown(display)))
    elif summary:
        body.append(Static(summary, markup=False))
    if details:
        body.append(Static(format_details(details)))
    if not body:
        body.append(Static("(no content)", markup=False))
    return Collapsible(*body, title=heading, collapsed=False)


class StatusPanel(VerticalScroll):
    """The right panel: session context, state sections and token usage."""

    def __init__(self, *, id: str | None = None) -> None:
        """Build an empty status panel.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        """Compose the empty placeholder (replaced on the first refresh)."""
        yield Static("State updates will appear here.", id="status-empty")

    async def set_state(self, chat: Mapping[str, Any] | None) -> None:
        """Rebuild the panel from the active chat's state.

        :param chat: The chat's data dict, or ``None`` when there is none.
        """
        await self.remove_children()
        widgets: list[Any] = []
        if chat:
            usage = chat.get("token_usage") or {}
            if any(usage.values()):
                widgets.append(Static(_usage_line(usage), id="status-usage"))
            context = chat.get("context") or {}
            if context:
                widgets.append(_context_widget(context))
            for key, section in (chat.get("state_sections") or {}).items():
                widgets.append(_section_widget(key, section))
        if not widgets:
            widgets.append(Static("State updates will appear here."))
        await self.mount_all(widgets)
        self.scroll_end(animate=False)
