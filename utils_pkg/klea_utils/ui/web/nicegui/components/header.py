#!/usr/bin/env python3
"""
Header component: title bar and dark-mode toggle.

File: klea_utils/ui/web/nicegui/components/header.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import ui

from klea_utils.ui.web.nicegui.components.context import PageContext

logger = logging.getLogger(__name__)


def attach_header(ctx: PageContext) -> None:
    """Build the page header: title, optional subtitle, dark-mode toggle.

    Relies on ``ctx.dark`` being set by :func:`theme.install_theme`
    before this is called.

    :param ctx: The shared page context.
    """
    with ui.header().classes("items-center"):
        ui.label(ctx.title).classes("text-xl font-bold")
        if ctx.subtitle:
            ui.label(ctx.subtitle).classes("text-sm text-grey-4 ml-2 mr-2")
        ui.space()

        def _toggle_dark():
            """Flip the dark-mode flag in ``app.storage.user``."""
            ctx.dark.value = not ctx.dark.value
            logger.debug("toggled dark mode: value=%s", ctx.dark.value)

        ui.button(icon="dark_mode", on_click=_toggle_dark).props(
            "flat color=white round"
        )
