#!/usr/bin/env python3
"""
Inspector panel component: streamed info/debug entries for the active chat.

File: klea_utils/ui/web/nicegui/components/inspector.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging

from nicegui import ui

from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats

logger = logging.getLogger(__name__)


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


def attach_inspector_panel(ctx: PageContext) -> None:
    """Build the inspector panel for the active chat in the inspect tab.

    Must be called with the inspect tab panel as the ambient NiceGUI
    context.  Registers ``refresh_inspector`` and ``reset_center_tab``
    on the context and renders the (empty) initial state.

    :param ctx: The shared page context.
    """

    @ui.refreshable
    def _render_inspector_panel() -> None:
        """Render the inspector entries for the active chat in the inspect tab."""
        with ui.column().classes("w-full px-2 gap-0"):
            current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
            if not current_chat or not current_chat.get("inspector_entries"):
                ui.label("No inspection data yet").classes("text-sm text-grey-5 py-8")
                ui.label(
                    "Inspector entries will appear here after a query completes."
                ).classes("text-xs text-grey-5")
                return

            entries = list(current_chat["inspector_entries"])
            logger.debug(
                "Rendering inspector panel for chat %s (entries=%d)",
                ctx.chat_id,
                len(entries),
            )
            for idx, entry in enumerate(entries):
                heading = entry.get("heading", "")
                timing = entry.get("timing_seconds", None)
                with (
                    ui.element("details")
                    .props("open")
                    .classes("inspector-entry mb-2 w-full")
                ):
                    with (  # noqa: SIM117
                        ui.element("summary")
                        .classes("text-xs font-bold cursor-pointer w-full")
                        .on("click", lambda i=idx: _toggle_inspector_entry(ctx, i))
                    ):
                        with ui.row().classes("w-full flex-nowrap items-center"):
                            ui.label(heading)
                            if timing:
                                ui.label(f"({timing:.1f}s)").classes(
                                    "text-xs text-grey-5"
                                )
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
                            ui.code(
                                json.dumps(details, indent=2), language="json"
                            ).classes("text-xs")

    ctx.refresh_inspector = _render_inspector_panel.refresh
    ctx.reset_center_tab = lambda: (
        ctx.center_panels.set_value("chat") if ctx.center_panels is not None else None
    )
    _render_inspector_panel()
