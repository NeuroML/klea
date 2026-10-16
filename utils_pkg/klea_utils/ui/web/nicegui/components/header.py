#!/usr/bin/env python3
"""
Header component: title bar, dark-mode toggle and exit button.

File: klea_utils/ui/web/nicegui/components/header.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging

from nicegui import app, background_tasks, ui

from klea_utils.ui.exit_confirm import EXIT_CONFIRM_MESSAGE, EXIT_CONFIRM_TITLE
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.stream import stop_all_streams

logger = logging.getLogger(__name__)

#: Delay before shutting the server down, so the fire-and-forget
#: ``window.close()`` has a chance to reach the browser first.
_TAB_CLOSE_GRACE_SECONDS = 0.2


def attach_header(ctx: PageContext) -> None:
    """Build the page header: title, subtitle, dark-mode toggle, exit button.

    Relies on ``ctx.dark`` being set by :func:`theme.install_theme`
    before this is called.  Registers ``ctx.exit_app``: it stops this page's
    active runs, then shuts the NiceGUI server down (which, for a
    client-spawned server, ends the subprocess via the CLI's process-group
    teardown).  ``ctx.exit_app`` is the immediate action used by the ``/exit``
    client command; the header's exit button confirms first, since an
    accidental click would stop the server for every open tab.

    :param ctx: The shared page context.
    """

    def _exit_app():
        """Stop the page's runs, close the tab, then shut the server down."""
        logger.info("exit requested from the web header")
        stop_all_streams(ctx)
        _close_browser_tab()
        # Give the fire-and-forget ``window.close()`` a moment to reach the
        # browser before the socket drops, then stop the server.
        background_tasks.create(_shutdown_soon())

    def _close_browser_tab() -> None:
        """Best-effort ``window.close()``.

        Browsers only let a script close a window it opened itself, so this is
        a no-op for a normal, user-opened tab; the server is stopped either way.
        """
        try:
            ui.run_javascript("window.close()")
        except Exception as e:  # noqa: BLE001
            logger.debug("window.close() not sent: %s", e)

    async def _shutdown_soon() -> None:
        await asyncio.sleep(_TAB_CLOSE_GRACE_SECONDS)
        app.shutdown()

    def _confirm_exit() -> None:
        """Confirm before exiting: the header button is easy to hit by accident.

        The dialog always shows (an accidental click would stop the web server
        for every open tab, not just this one); ``/exit`` skips it because
        typing the command is deliberate.
        """
        logger.debug("opening exit-confirmation dialog")
        dialog = ui.dialog()
        with dialog, ui.card():
            ui.label(EXIT_CONFIRM_TITLE).classes("text-lg font-bold")
            ui.label(EXIT_CONFIRM_MESSAGE).classes("text-sm")
            with ui.row().classes("w-full justify-end"):
                ui.button("Cancel", on_click=dialog.close)
                ui.button("Exit", on_click=lambda: (dialog.close(), _exit_app())).props(
                    "unelevated color=negative"
                )
        dialog.open()

    ctx.exit_app = _exit_app

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
        ui.button(icon="power_settings_new", on_click=_confirm_exit).props(
            "flat color=white round"
        ).tooltip("Exit")
