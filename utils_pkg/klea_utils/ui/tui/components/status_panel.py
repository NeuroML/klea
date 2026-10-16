#!/usr/bin/env python3
"""
Status panel for the Klea TUI.

The full-height right panel: the chat's streamed state (``devdocs/system/tui.md``)
- the per-node ``state`` sections keyed by section key and the accumulated
token usage.  Each state section renders like the web status pane: a
collapsible heading with the node's ``display`` (Markdown, or a literal block
when ``preformatted``), its ``summary`` and its ``details`` via
:func:`klea_utils.ui.inspect_format.format_details`.

File: klea_utils/ui/tui/components/status_panel.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from rich.markdown import Markdown
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, Static

from klea_utils.ui.inspect_format import format_details

if TYPE_CHECKING:
    from klea_utils.ui.tui.context import TuiContext

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
    """The right panel: app slots, state sections, usage.

    The top holds the app-defined controls registered on
    :attr:`TuiContext.status_extras` (e.g. the agent's operating-mode and
    tool-access selectors); below them sits the chat's streamed state, rebuilt
    on every :meth:`set_state`.
    """

    # Both sections size to their content rather than splitting the panel
    # 50/50 (the Textual ``Vertical`` default is ``height: 1fr``).  Otherwise
    # the app slots take half the pane and the state body is clipped to the
    # other half, so it never contributes its real height to the scroll region
    # and the panel cannot scroll.
    DEFAULT_CSS = """
    StatusPanel #status-extras {
        height: auto;
    }
    StatusPanel #status-body {
        height: auto;
    }
    """

    def __init__(self, *, ctx: TuiContext | None = None, id: str | None = None) -> None:
        """Build the panel for *ctx*.

        :param ctx: The shared TUI context (its ``status_extras`` are rendered
            at the top); ``None`` renders no extras.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        self._ctx = ctx

    def compose(self) -> ComposeResult:
        """Compose the app slots and the (initially empty) state body."""
        with Vertical(id="status-extras"):
            yield from self._extra_widgets()
        yield Vertical(id="status-body")

    def _extra_widgets(self) -> list[Any]:
        """Build the app-slot widgets from the context's renderers."""
        if self._ctx is None:
            return []
        widgets: list[Any] = []
        for render in self._ctx.status_extras:
            widget = render(self._ctx)
            if widget is None:
                continue
            if isinstance(widget, (list, tuple)):
                widgets.extend(widget)
            else:
                widgets.append(widget)
        return widgets

    async def set_state(
        self, chat: Mapping[str, Any] | None, *, refresh_extras: bool = False
    ) -> None:
        """Rebuild the streamed state below the app slots.

        :param chat: The chat's data dict, or ``None`` when there is none.
        :param refresh_extras: Also rebuild the app slots (a ``context``
            change can alter what a selector should show).
        """
        if refresh_extras:
            extras = self.query_one("#status-extras", Vertical)
            await extras.remove_children()
            widgets = self._extra_widgets()
            if widgets:
                await extras.mount_all(widgets)
        body = self.query_one("#status-body", Vertical)
        await body.remove_children()
        widgets: list[Any] = []
        if chat:
            usage = chat.get("token_usage") or {}
            if any(usage.values()):
                widgets.append(Static(_usage_line(usage), id="status-usage"))
            for key, section in (chat.get("state_sections") or {}).items():
                widgets.append(_section_widget(key, section))
        if not widgets:
            widgets.append(Static("State updates will appear here."))
        await body.mount_all(widgets)
        self.scroll_end(animate=False)
