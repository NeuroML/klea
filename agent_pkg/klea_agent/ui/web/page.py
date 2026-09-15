#!/usr/bin/env python3
"""
Agent NiceGUI page composition.

This module *composes* the agent web page from the shared
``klea_utils.ui.web.nicegui.components`` (ADR-0031): it creates a
:class:`PageContext` and attaches the components in the layout order.
Process setup (logging, storage, ``ui.run``) is delegated to
:func:`klea_utils.ui.web.nicegui.components.bootstrap.run_nicegui_server`.

Agent-specific UI elements (the ADR-0030 operating-mode selector and
resolved-mode badge, see :mod:`klea_agent.ui.web.mode_ui`, and the ADR-0037
tool-access-level selector and badge, see :mod:`klea_agent.ui.web.access_ui`)
slot into the shared status pane here without touching ``klea_utils``.

File: klea_agent/ui/web/page.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.ui.web.nicegui.components import (
    bootstrap,
    chat_area,
    chat_list,
    header,
    initial_load,
    input_area,
    inspector,
    model_dialog,
    status_pane,
    theme,
)
from klea_utils.ui.web.nicegui.components.context import DEFAULT_FOOTER, PageContext
from nicegui import ui

from klea_agent.ui.web import access_ui, mode_ui

logger = logging.getLogger(__name__)


def setup_layout(
    chat_id: str,
    server_url: str,
    user_id: str = "",
    title: str = "Klea",
    subtitle: str = "",
    disclaimer: str = "",
    footer_text: str = DEFAULT_FOOTER,
) -> None:
    """Build the agent page UI: header, drawers, chat area, and footer.

    User messages appear as right-aligned bubbles (grey background);
    system / bot messages are left-aligned, full-width and transparent,
    matching the Gemini/ChatGPT model without avatars.

    Layout (left to right)::

        [left_drawer | center_column | right_drawer]

    The left drawer uses Quasar's *mini* mode to provide a
    ChatGPT-style rail that shows only icons when collapsed and
    full text when expanded.

    :param chat_id: Chat conversation identifier.
    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param title: Bold application title in the header bar.
    :param subtitle: Optional smaller text shown next to *title*.
    :param disclaimer: Optional text shown below the chat input.
    :param footer_text: HTML content for the footer bar.
    """
    ctx = PageContext(
        chat_id=chat_id,
        server_url=server_url,
        user_id=user_id,
        title=title,
        subtitle=subtitle,
        disclaimer=disclaimer,
        footer_text=footer_text,
    )

    # --- Page chrome ---
    theme.install_theme(ctx)
    header.attach_header(ctx)

    # --- Drawers ---
    chat_list.attach_chat_list(ctx)  # left drawer (sessions)
    model_dialog.attach_model_info(ctx)
    mode_ui.attach_mode_ui(ctx)  # status-pane slot: mode selector + badge
    access_ui.attach_access_ui(ctx)  # status-pane slot: access selector + badge
    status_pane.attach_status_pane(ctx)  # right drawer (state); needs model dialog

    # ---- Center: chat messages + input (pinned to bottom) ----
    with (
        ui.column()
        .classes("w-full px-2")
        .style("flex: 1; min-height: 0; display: flex; flex-direction: column;")
    ):
        # Backend readiness banner - shown until health-check + hydrate
        # complete.  Keeping this in the delivered layout avoids blocking
        # the page timeout on the 180s probe (HF cold-start race that
        # deleted the client and made ``Drawer.__init__`` crash with
        # ``page_container is not in list``).
        with ui.row().classes(
            "w-full justify-center items-center gap-2 p-2"
        ) as ctx.loading_row:
            ui.spinner(type="dots").classes("w-4 h-4")
            ui.label("Backend is starting, please wait...").classes(
                "text-xs text-grey-5 italic"
            )

        with ui.tabs().classes("w-full") as center_tabs:
            chat_tab = ui.tab(name="chat", label="chat")
            inspect_tab = ui.tab(name="inspect", label="inspect")
        with (
            ui.tab_panels(center_tabs, value="chat")
            .classes("w-full grow center-tab-panels")
            .style("flex: 1; min-height: 0; display: flex; flex-direction: column;")
        ) as center_panels:
            ctx.center_panels = center_panels
            with (
                ui.tab_panel(chat_tab)
                .classes("w-full")
                .style(
                    "flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0;"
                )
            ):
                chat_area.attach_chat_area(ctx)
                input_area.attach_input(ctx)
            with (
                ui.tab_panel(inspect_tab)
                .classes("w-full")
                .style(
                    "flex: 1; min-height: 0; display: flex; flex-direction: column; padding: 0;"
                )
            ):
                inspector.attach_inspector_panel(ctx)

    # ---- Background initialisation: health-check + hydrate ----
    # Runs after the layout is delivered so the page handler returns
    # within ``response_timeout`` even when the backend needs 30-60s
    # to become ready on HF (cold container).
    initial_load.attach_initial_load(ctx)

    # ---- Footer ----
    with ui.footer().classes("bg-grey-3 dark:bg-grey-9 text-xs py-1"):
        ui.html(footer_text).classes("w-full text-center text-grey-6")


def run_agent_web(
    title: str,
    server_url: str,
    *,
    subtitle: str = "",
    disclaimer: str = "",
    footer_text: str = DEFAULT_FOOTER,
    reload: bool = False,
    nicegui_url: str = "0.0.0.0:7860",
    storage_secret: str = "klea-nicegui-secret-change-me",
    app_name: str = "klea-web",
) -> None:
    """Start the agent NiceGUI web server with :func:`setup_layout`.

    Thin wrapper around :func:`components.bootstrap.run_nicegui_server`
    that supplies this app's page composition.

    :param title: Application title (displayed in the header and
        browser tab).
    :param server_url: Base URL of the backend API server.
    :param subtitle: Optional smaller text shown next to *title*.
    :param disclaimer: Optional text shown below the chat input.
    :param footer_text: HTML content for the footer bar.
    :param reload: When ``True``, enable NiceGUI's file-watch hot reload.
    :param nicegui_url: ``host:port`` to bind the NiceGUI web server to.
    :param storage_secret: Secret used by NiceGUI for browser session
        persistence.
    :param app_name: Log identity for this frontend process.
    """
    bootstrap.run_nicegui_server(
        title,
        server_url,
        page_builder=setup_layout,
        subtitle=subtitle,
        disclaimer=disclaimer,
        footer_text=footer_text,
        reload=reload,
        nicegui_url=nicegui_url,
        storage_secret=storage_secret,
        app_name=app_name,
    )
