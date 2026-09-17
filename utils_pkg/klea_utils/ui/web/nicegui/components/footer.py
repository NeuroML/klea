"""
Footer component: disclaimer and footer text on the footer band.

File: klea_utils/ui/web/nicegui/components/footer.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import ui

from klea_utils.ui.web.nicegui.components.context import PageContext

logger = logging.getLogger(__name__)


def attach_footer(ctx: PageContext) -> None:
    """Build the page footer: disclaimer and footer text on one row.

    Both share the footer band's single line, centred as
    ``disclaimer | footer_text``, so the disclaimer no longer needs a line
    below the chat input and the input can sit flush at the bottom of the
    scroll area.  The row wraps on narrow screens.

    :param ctx: The shared page context (supplies ``disclaimer`` and
        ``footer_text``).
    """
    with (
        ui.footer().classes("footer-bar text-xs py-1"),
        ui.row().classes("w-full items-center justify-center gap-x-2 text-grey-6"),
    ):
        if ctx.disclaimer:
            ui.label(ctx.disclaimer)
            ui.label("|").classes("text-grey-5")
        ui.html(ctx.footer_text)
