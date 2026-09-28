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

from klea_utils.llm import missing_required_roles
from klea_utils.ui.web.nicegui.client import create_chat_on_server
from klea_utils.ui.web.nicegui.components import stream
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.storage import safe_set_user
from klea_utils.ui.web.nicegui.state import chats, ensure_chat, missing_credentials

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
            .props("outlined autogrow")
            .classes("flex-grow chat-input")
        )
        ctx.text = text

        def _effective_model_info() -> dict:
            """Model info for the active scope (chat, else session defaults)."""
            if ctx.chat_id:
                chat_info = chats.get(f"{ctx.user_id}:{ctx.chat_id}", {}).get(
                    "model_info", {}
                )
                if chat_info:
                    return chat_info
            return ctx.session_model_info

        def _setup_incomplete() -> bool:
            """Whether required models or API keys are still missing."""
            info = _effective_model_info()
            return bool(missing_required_roles(info) or missing_credentials(info))

        def send() -> None:
            """Append the current input text as a user message, then stream."""
            if not text.value.strip():
                return
            if _setup_incomplete():
                logger.warning(
                    "send blocked: required models or API keys not configured"
                )
                ui.notification(
                    "Select the required models and API keys before sending. "
                    "Use the settings (gear) icon.",
                    type="warning",
                    close_button=True,
                )
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

            ensure_chat(ctx.user_id, current)["messages"].append(
                {"text": query, "stamp": stamp, "role": "user", "header": ""}
            )
            ctx.render_chat_area()
            ctx.refresh_chat_list()

            background_tasks.create(stream.run_stream(ctx, query, current))

        # Send button inside the field, anchored to the bottom-right: the
        # textarea autogrows upward as the message gets longer while the
        # button stays on its last line (the Gemini pattern).
        with text.add_slot("append"):
            send_button = ui.button(icon="send", on_click=send).props(
                "flat dense round color=primary"
            )
            with send_button:
                ui.tooltip("Enter to send, Shift+Enter for newline")

        def refresh_send_state() -> None:
            """Enable/disable send based on model and API-key readiness."""
            incomplete = _setup_incomplete()
            info = _effective_model_info()
            try:
                if incomplete:
                    send_button.disable()
                else:
                    send_button.enable()
            except Exception as e:  # noqa: BLE001
                logger.debug("send button state update failed: %s", e)
            logger.debug(f"send state updated: {incomplete = }\n{list(info) = }")

        ctx.refresh_send_state = refresh_send_state

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

    # Disable chat input (and send) until backend is ready.  initial_load's
    # model fetch calls refresh_send_state() to re-enable when configured.
    try:
        text.disable()
        send_button.disable()
    except Exception as e:  # noqa: BLE001
        logger.debug("disable input failed: %s", e)
