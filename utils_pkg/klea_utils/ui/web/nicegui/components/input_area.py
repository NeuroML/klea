#!/usr/bin/env python3
"""
Chat input component: text area, send handling, and stream kick-off.

File: klea_utils/ui/web/nicegui/components/input_area.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from datetime import datetime

import coolname
from nicegui import background_tasks, ui
from nicegui.events import GenericEventArguments

from klea_utils.ui.web.nicegui.client import create_chat_on_server
from klea_utils.ui.web.nicegui.components import stream
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.storage import safe_set_user
from klea_utils.ui.web.nicegui.state import ensure_chat

logger = logging.getLogger(__name__)


def attach_input(ctx: PageContext) -> None:
    """Build the chat input row and wire send / Enter handling.

    Must be called with the chat tab panel (below the chat area) as the
    ambient NiceGUI context.  The input is disabled until
    :func:`initial_load.attach_initial_load` enables it after the
    backend health probe succeeds.

    :param ctx: The shared page context; ``text`` is filled in here.
    """
    with ui.row().classes("w-full no-wrap items-end py-4"):
        text = (
            ui.textarea(placeholder="Start a conversation")
            .props("rounded outlined input-class=mx-3 autogrow")
            .classes("flex-grow")
        )
        ctx.text = text

        def send() -> None:
            """Append the current input text as a user message, then stream."""
            if not text.value.strip():
                return
            stamp = datetime.now().astimezone().strftime("%X")
            query = text.value
            text.value = ""

            current = ctx.chat_id
            logger.debug("current=%s query_len=%d", current, len(query))
            if not current:
                current = coolname.generate_slug(2)
                ctx.chat_id = current
                safe_set_user("chat_id", current)
                ensure_chat(ctx.user_id, current)
                background_tasks.create(
                    create_chat_on_server(ctx.server_url, ctx.user_id, current)
                )
                # Populate model_info for the newly created chat so the
                # "Choose models" dialog has roles to render.  Without this
                # the status pane stays empty and the dialog silently no-ops
                # for chats started by typing the first message.
                if ctx.fetch_model_info is not None:
                    background_tasks.create(ctx.fetch_model_info())
                ctx.refresh_chat_list()

            ensure_chat(ctx.user_id, current)["messages"].append((query, stamp, True))
            ctx.render_chat_area()
            ctx.refresh_chat_list()

            background_tasks.create(stream.run_stream(ctx, query, current))

        with (
            text.add_slot("append"),
            ui.button(icon="send", on_click=send).props(
                "flat dense round color=primary"
            ),
        ):
            ui.tooltip("Enter to send, Shift+Enter for newline")

        # Plain Enter sends the message and prevents the default newline
        def handle_enter(e: GenericEventArguments):
            """Send on Enter, insert newline on Shift+Enter."""
            if e.args.get("shiftKey"):
                text.value += "\n"
            else:
                send()

        text.on("keydown.enter.exact.prevent", handle_enter)
        # Clicking the send icon inside the textarea also sends.
        text.on("click:append", send)

    # The disclaimer is a full-width line BELOW the input row.  Keeping it
    # inside the flex row would share the line with the ``flex-grow``
    # textarea: as a ``w-full`` flex sibling it wins the space and squeezes
    # the text box down to its content width.
    if ctx.disclaimer:
        ui.label(ctx.disclaimer).classes("text-xs text-grey-5 pb-2 w-full text-center")

    # Disable chat input until backend is ready; initial_load re-enables.
    try:
        text.disable()
    except Exception as e:  # noqa: BLE001
        logger.debug("disable input failed: %s", e)
