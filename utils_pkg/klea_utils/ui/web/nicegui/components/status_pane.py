#!/usr/bin/env python3
"""
Status pane component: the right drawer with chat state details.

Shows the chat name, per-role model summary (with a settings button
opening the model-config dialog), token usage totals, and the
per-node status sections streamed by the graph.  Everything up to (and
including) the token usage stays visible: it is a sticky header, so the state
sections below scroll under it (mirroring the TUI status panel).

File: klea_utils/ui/web/nicegui/components/status_pane.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from datetime import datetime
from typing import Any

from nicegui import ui

from klea_utils.llm import missing_required_roles, parse_model_name
from klea_utils.ui.linkify import linkify_md
from klea_utils.ui.state import ChatData, chats, missing_credentials
from klea_utils.ui.web.nicegui.components.context import PageContext

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
        .classes("overflow-y-auto overflow-x-hidden bg-white dark:bg-[#1d1d1d]")
    ):

        @ui.refreshable
        def _status_pane() -> None:
            """Render the sticky header, then the state sections below it.

            The app-defined context controls (mode/access) render even before a
            chat exists, so they can be set before the first message; they
            attach to the chat once it is created.
            """
            current_chat: ChatData | dict[Any, Any] = (
                chats.get(f"{ctx.user_id}:{ctx.chat_id}") or {}
            )
            chat_scope = bool(current_chat)
            if current_chat:
                model_info = current_chat.get("model_info", {})
                scope_label = current_chat.get("name", "") or "Models"
            else:
                model_info = ctx.session_model_info
                scope_label = "Default models"

            missing_models = missing_required_roles(model_info)
            missing_providers = sorted(
                {
                    (model_info[role].get("credential") or {}).get("provider", "")
                    for role in missing_credentials(model_info)
                }
                - {""}
            )
            needs_attention = bool(missing_models or missing_providers)
            logger.debug(
                f"{ctx.chat_id = }\n{missing_models = }\n{missing_providers = }"
            )

            # Sticky header: stays visible while the state sections scroll.
            with ui.column().classes(
                "w-full gap-0 p-2 sticky top-0 z-20 bg-white dark:bg-[#1d1d1d]"
            ):
                # App-defined context slots (e.g. operating-mode and
                # tool-access selectors, ADR-0030/ADR-0037).
                for render in ctx.status_extras:
                    render()

                # The scope row (chat name + settings) is the Models group's
                # collapsible header, so the whole group toggles from one line.
                with ui.expansion().classes("w-full text-xs models-group") as models:
                    with (
                        models.add_slot("header"),
                        ui.row().classes("items-center w-full no-wrap py-1"),
                    ):
                        with ui.label(scope_label).classes("text-sm font-bold mb-0"):
                            created = current_chat.get("created", 0)
                            if created:
                                ui.tooltip(
                                    "Created: "
                                    + datetime.fromtimestamp(created)
                                    .astimezone()
                                    .strftime("%a %d %b %Y at %X")
                                )
                        ui.space()
                        gear = (
                            ui.button(
                                icon="settings",
                                on_click=ctx.model_config_dialog,
                            )
                            .props("flat dense round")
                            .classes("text-sm icon-btn")
                        )
                        if needs_attention:
                            gear.classes("model-btn--attention")
                        with gear:
                            ui.tooltip(
                                "Choose models"
                                + (" - configuration needed" if needs_attention else "")
                            )
                    if model_info:
                        tooltip_parts: list[str] = []
                        with ui.element("ul").classes("models-list"):
                            for role, cfg in model_info.items():
                                raw = cfg.get("model", "")
                                provider = cfg.get("provider", "")
                                required = cfg.get("required", True)
                                if not raw:
                                    # No model set for this role -- show a
                                    # clear placeholder so the user knows it is
                                    # missing.
                                    display_short = "Not set"
                                    required_mark = " (required)" if required else ""
                                    tooltip_short = f"Not set{required_mark}"
                                else:
                                    display_short = (
                                        parse_model_name(raw).model_name or raw
                                    )
                                    if provider:
                                        tooltip_short = f"{display_short} ({provider})"
                                    else:
                                        tooltip_short = display_short
                                    credential = cfg.get("credential") or {}
                                    if credential.get("requires_key"):
                                        if credential.get("source") == "none":
                                            tooltip_short += " [no API key]"
                                        elif credential.get("source") == "user":
                                            tooltip_short += (
                                                f" [key {credential.get('masked', '')}]"
                                            )
                                if cfg.get("overridden"):
                                    tooltip_short += (
                                        " [Chat]" if chat_scope else " [Default]"
                                    )
                                if chat_scope and cfg.get("session_overridden"):
                                    tooltip_short += " [Default]"
                                with ui.element("li"):
                                    ui.label(
                                        f"{role.capitalize()}: "
                                        f"{provider or '(unknown)'}"
                                    )
                                    ui.label(display_short).classes("pl-4")
                                tooltip_parts.append(
                                    f"{role.capitalize()}: {tooltip_short}"
                                )
                            if tooltip_parts:
                                ui.tooltip("\n".join(tooltip_parts)).classes(
                                    "model-tooltip"
                                )
                    else:
                        ui.label("Model information is loading...").classes(
                            "text-xs text-grey-5"
                        )
                # Two-way binding keeps the open state across pane refreshes.
                models.bind_value(ctx, "status_models_expanded")

                if missing_models:
                    ui.label(
                        "Select a model to start: "
                        + ", ".join(role.capitalize() for role in missing_models)
                    ).classes("text-xs text-negative")
                elif missing_providers:
                    ui.label(
                        "Add an API key for: " + ", ".join(missing_providers)
                    ).classes("text-xs text-negative")

                if not chat_scope:
                    ui.label(
                        "State updates will appear here once you send a message"
                    ).classes("text-sm text-grey-5 mt-2")
                    return

                token_usage = current_chat.get("token_usage", {})
                if any(token_usage.values()):
                    ui.label(
                        f"{token_usage.get('input_tokens', 0):,} in / "
                        f"{token_usage.get('output_tokens', 0):,} out"
                    ).classes("text-xs mb-0")
                ui.separator().classes("my-0.5")

            # The state sections scroll under the sticky header.
            with ui.column().classes("w-full gap-0 px-2 pb-2"):
                sections = current_chat.get("state_sections", {})
                if not sections:
                    ui.label("State updates will appear here").classes(
                        "text-sm text-grey-5"
                    )
                    return
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
                            if section.get("preformatted"):
                                # Aligned/literal content (e.g. the plan): a
                                # plain code block, so columns line up and
                                # ``_``/``*`` in tool names are not parsed as
                                # markdown.  No language is passed (no syntax
                                # highlighting), and
                                # ``nicegui-code-noformat`` strips the code-box
                                # styling.
                                ui.code(display, language=None).classes(
                                    "text-xs w-full nicegui-code-noformat"
                                )
                            else:
                                ui.markdown(linkify_md(display)).classes(
                                    "text-xs w-full"
                                )
                        summary = section.get("summary", "")
                        if summary and not display:
                            ui.label(summary).classes("text-xs text-grey-6 mb-1 w-full")
                        details = section.get("details", {})
                        if details:
                            with ui.element("details").classes(
                                "status-details text-xs text-grey-5 "
                                "cursor-pointer w-full"
                            ):
                                with ui.element("summary").classes("text-xs w-full"):
                                    ui.label("View details")
                                ui.code(
                                    json.dumps(details, indent=2), language="json"
                                ).classes("text-xs")

        _status_pane()
        ctx.refresh_status_pane = _status_pane.refresh
