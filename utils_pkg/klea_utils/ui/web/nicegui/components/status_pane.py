#!/usr/bin/env python3
"""
Status pane component: the right drawer with chat state details.

Shows the chat name, per-role model summary (with a settings button
opening the model-config dialog), token usage totals, and the
per-node status sections streamed by the graph.

File: klea_utils/ui/web/nicegui/components/status_pane.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from datetime import datetime

from nicegui import ui

from klea_utils.llm import parse_model_name
from klea_utils.ui.linkify import linkify_md
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats

logger = logging.getLogger(__name__)


def attach_status_pane(ctx: PageContext) -> None:
    """Build the right drawer (status pane) and register its refresh.

    Must be called with ``ctx.model_config_dialog`` already registered
    (by :func:`model_dialog.attach_model_info`), since the pane's
    settings button invokes it.

    :param ctx: The shared page context; ``refresh_status_pane`` is
        set here.
    """
    with (
        ui.right_drawer(value=True)
        .props("width=420 bordered")
        .classes("overflow-y-auto overflow-x-hidden")
    ):

        @ui.refreshable
        def _status_pane() -> None:
            """Render chat name, model info and state sections for the active chat."""
            current_chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
            if not current_chat:
                if ctx.chat_id:
                    logger.debug("No chat found for %s, status pane empty", ctx.chat_id)
                return
            with ui.column().classes("w-full gap-0"):
                with ui.row().classes("items-center w-full gap-0"):
                    with ui.label(current_chat.get("name", "")).classes(
                        "text-sm font-bold mb-0"
                    ):
                        created = current_chat.get("created", 0)
                        if created:
                            ui.tooltip(
                                "Created: "
                                + datetime.fromtimestamp(created)
                                .astimezone()
                                .strftime("%a %d %b %Y at %X")
                            )
                    ui.space()
                    with (
                        ui.button(
                            icon="settings",
                            on_click=ctx.model_config_dialog,
                        )
                        .props("flat dense round color=grey-9")
                        .classes("text-sm")
                    ):
                        ui.tooltip("Choose models")
                model_info = current_chat.get("model_info", {})
                if model_info:
                    tooltip_parts: list[str] = []
                    display_parts: list[str] = []
                    for role, cfg in model_info.items():
                        raw = cfg.get("model", "")
                        provider = cfg.get("provider", "")
                        required = cfg.get("required", True)
                        if not raw:
                            # No model set for this role -- show a clear
                            # placeholder so the user knows it is missing.
                            display_short = "Not set"
                            required_mark = " (required)" if required else ""
                            tooltip_short = f"Not set{required_mark}"
                            if cfg.get("overridden"):
                                tooltip_short += " [User]"
                        else:
                            short = (
                                parse_model_name(raw).model_name
                                if parse_model_name(raw)
                                else raw
                            )
                            if provider:
                                tooltip_short = f"{short} ({provider})"
                            else:
                                tooltip_short = short
                            if cfg.get("overridden"):
                                tooltip_short += " [User]"
                            display_short = short
                        tooltip_parts.append(f"{role.capitalize()}: {tooltip_short}")
                        display_parts.append(display_short)
                    with ui.label(" | ".join(display_parts)).classes(
                        "text-xs text-grey-5"
                    ):
                        if tooltip_parts:
                            ui.tooltip("\n".join(tooltip_parts)).classes(
                                "model-tooltip"
                            )
            token_usage = current_chat.get("token_usage", {})
            has_token_usage = any(token_usage.values())
            if has_token_usage:
                usage_display = (
                    f"{token_usage.get('input_tokens', 0)} in / "
                    f"{token_usage.get('output_tokens', 0)} out"
                )
                ui.label(usage_display).classes("text-xs text-grey-5 mb-0")
            sections = current_chat.get("state_sections", {})
            has_content = bool(model_info) or bool(sections) or has_token_usage
            if not has_content:
                ui.label("State updates will appear here").classes(
                    "text-sm text-gray-500"
                )
                return
            if sections:
                ui.separator().classes("my-0.5")
            for node_label, section in sections.items():
                with (
                    ui.element("details")
                    .props("open")
                    .classes("status-entry mb-2 w-full")
                ):
                    with (
                        ui.element("summary").classes(
                            "text-xs font-bold cursor-pointer w-full"
                        ),
                        ui.row().classes("w-full flex-nowrap items-center"),
                    ):
                        ui.label(section.get("heading", node_label))
                    display = section.get("display", "")
                    if display:
                        ui.markdown(linkify_md(display)).classes("text-xs w-full")
                    summary = section.get("summary", "")
                    if summary and not display:
                        ui.label(summary).classes("text-xs text-grey-6 mb-1 w-full")
                    details = section.get("details", {})
                    if details:
                        with ui.element("details").classes(
                            "status-details text-xs text-grey-5 cursor-pointer w-full"
                        ):
                            with ui.element("summary").classes("text-xs w-full"):
                                ui.label("View details")
                            ui.code(
                                json.dumps(details, indent=2), language="json"
                            ).classes("text-xs")

        _status_pane()
        ctx.refresh_status_pane = _status_pane.refresh
