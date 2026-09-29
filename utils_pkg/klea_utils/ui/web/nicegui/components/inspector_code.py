#!/usr/bin/env python3
"""
Inspector code block: JSON highlighting and copy button for inspect details.

``ui.code`` renders its content through a markdown2 fenced block, which
breaks when the content itself contains a code fence (the fence is closed
early and the rest is rendered as markdown).  This module renders the
formatted details directly with Pygments' JSON lexer, keeping ``ui.code``'s
code box and copy button without the markdown indirection.

File: klea_utils/ui/web/nicegui/components/inspector_code.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
from typing import Any

from nicegui import ui
from pygments import highlight
from pygments.formatters.html import HtmlFormatter
from pygments.lexers.data import JsonLexer

logger = logging.getLogger(__name__)

#: CSS class of the Pygments wrapper; ``theme.py`` serves its token colours.
CODE_HL_CLASS = "inspector-code-hl"


def highlight_details(text: str) -> str:
    """Return *text* highlighted as JSON (HTML).

    Uses Pygments' stock JSON lexer (object keys as ``Name.Tag``, string
    values as ``String.Double``); it already tolerates the real newlines
    inside string values that
    :func:`~klea_utils.ui.inspect_format.format_details` produces.

    :param text: The formatted details string.
    :returns: HTML with Pygments token spans; ``theme.py`` serves the
        matching ``.{CODE_HL_CLASS}`` colours.
    """
    return highlight(
        text,
        JsonLexer(),
        HtmlFormatter(cssclass=CODE_HL_CLASS, nobackground=True),
    )


def render_inspector_code(text: str) -> None:
    """Render a code box with highlighted *text* and a copy button.

    Mirrors ``ui.code`` (box, JSON highlighting, copy button) without its
    markdown2 fenced block, so content containing code fences or multi-line
    values renders intact.

    :param text: The formatted details string.
    """
    highlighted = highlight_details(text)
    with ui.element("div").classes("nicegui-code w-full"):
        copy_button: Any = None

        async def _copied() -> None:
            """Show a checkmark for a moment after a successful copy."""
            if copy_button is None:
                return
            copy_button.props("icon=check")
            await asyncio.sleep(3)
            copy_button.props("icon=content_copy")

        code = ui.html(highlighted, sanitize=False).classes(
            "inspector-code-content w-full overflow-auto"
        )
        copy_button = (
            ui.button(icon="content_copy", on_click=_copied)
            .props("round flat size=sm")
            .classes("nicegui-code-copy")
            .on(
                "click",
                js_handler=(
                    "() => { const el = "
                    f"document.querySelector('#{code.id} pre') "
                    f"|| document.getElementById('{code.id}'); "
                    "navigator.clipboard.writeText(el ? el.textContent : ''); }"
                ),
            )
        )
