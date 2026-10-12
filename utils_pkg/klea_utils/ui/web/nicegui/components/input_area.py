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
from klea_utils.ui.web.nicegui.components import commands, stream
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.storage import safe_set_user
from klea_utils.ui.web.nicegui.state import (
    chats,
    ensure_chat,
    interrupt_display,
    missing_credentials,
)

logger = logging.getLogger(__name__)


def attach_input(ctx: PageContext) -> None:
    """Build the chat input row and wire send / Enter handling.

    Must be called with the chat tab panel (below the chat area) as the
    ambient NiceGUI context.  The input is disabled until
    :func:`initial_load.attach_initial_load` enables it after the
    backend health probe succeeds.

    :param ctx: The shared page context; ``text`` is filled in here.
    """
    # `/`-command autocomplete (ADR-0047): a suggestion list shown just above
    # the input row while a command is being typed.
    suggestions = ui.column().classes("w-full").style("gap: 0;")
    suggestions.set_visibility(False)
    with ui.row().classes("w-full no-wrap items-end py-4"):
        text = (
            ui.textarea(placeholder="Start a conversation")
            .props("outlined autogrow")
            .classes("flex-grow chat-input")
        )
        ctx.text = text
        commands.attach_autocomplete(ctx, text, suggestions)

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

        def _awaiting_input() -> bool:
            """Whether the active chat is paused at a HITL interrupt."""
            if not ctx.chat_id:
                return False
            chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
            return bool(
                chat and (chat.get("turn_status") or {}).get("kind") == "awaiting_input"
            )

        def submit_interrupt(
            response: dict | None, interrupt_id: str | None, cancel: bool
        ) -> None:
            """Send a HITL answer/cancel (the status-region form calls this)."""
            if ctx.chat_is_streaming(ctx.chat_id):
                return
            chat_id = ctx.chat_id
            if not chat_id:
                return
            stamp = datetime.now().astimezone().strftime("%X")
            ensure_chat(ctx.user_id, chat_id)["messages"].append(
                {
                    "text": interrupt_display(response, cancel=cancel),
                    "stamp": stamp,
                    "role": "user",
                    "header": "",
                }
            )
            ctx.render_chat_area()
            ctx.refresh_chat_list()
            task = background_tasks.create(
                stream.run_stream(
                    ctx,
                    "",
                    chat_id,
                    interrupt_response=response,
                    interrupt_id=interrupt_id,
                    interrupt_cancel=cancel,
                )
            )
            ctx.stream_tasks[f"{ctx.user_id}:{chat_id}"] = task

        ctx.submit_interrupt = submit_interrupt

        def ensure_active_chat() -> str:
            """Return the active chat id, creating and registering one if none.

            Shared by the send path and by client-command output (``/help``),
            so a command entered before the first message still lands in a chat
            (ADR-0047).
            """
            if ctx.chat_id:
                return ctx.chat_id
            current = coolname.generate_slug(2)
            ctx.chat_id = current
            safe_set_user("chat_id", current)
            new_chat = ensure_chat(ctx.user_id, current)
            for hook in ctx.chat_created_hooks:
                hook(new_chat)
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
            return current

        ctx.ensure_active_chat = ensure_active_chat

        def send() -> None:
            """Append the current input text as a user message, then stream."""
            raw = text.value
            if not raw.strip():
                return
            # Session commands (ADR-0047): a client/unknown command is handled
            # locally and never sent; a server command falls through to the send
            # path below, where the graph's command node runs it.  Handled
            # before the setup/streaming guards so /help works even when models
            # are not configured.
            if commands.handle_command(ctx, raw):
                text.value = ""
                return
            if _awaiting_input():
                # The status-region form is the input while paused; ignore a
                # stray Enter (useful once D1b re-enables the box on reload).
                logger.debug("send ignored: awaiting interrupt input")
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
            if ctx.chat_is_streaming(ctx.chat_id):
                # A run is already active for this chat; the send control is
                # a Stop button in that state, so ignore a stray Enter.
                logger.debug("send ignored: a run is already streaming")
                return
            stamp = datetime.now().astimezone().strftime("%X")
            query = raw
            text.value = ""

            current = ensure_active_chat()
            logger.debug("current=%s query_len=%d", current, len(query))

            ensure_chat(ctx.user_id, current)["messages"].append(
                {"text": query, "stamp": stamp, "role": "user", "header": ""}
            )
            ctx.render_chat_area()
            ctx.refresh_chat_list()

            task = background_tasks.create(stream.run_stream(ctx, query, current))
            ctx.stream_tasks[f"{ctx.user_id}:{current}"] = task

        # Send button inside the field, anchored to the bottom-right: the
        # textarea autogrows upward as the message gets longer while the
        # button stays on its last line (the Gemini pattern).  While a run
        # streams the same button becomes Stop.
        with text.add_slot("append"):
            send_button = ui.button(icon="send").props("flat dense round color=primary")
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
            logger.debug(f"send state updated: {incomplete = }\n{list(info) =}")

        ctx.refresh_send_state = refresh_send_state

        def refresh_stream_button() -> None:
            """Flip the send control to Stop, or disable it while awaiting input."""
            try:
                if ctx.chat_is_streaming(ctx.chat_id):
                    send_button.props("icon=stop")
                    send_button.enable()
                    text.enable()
                elif _awaiting_input():
                    # The status-region form is the input; disable the main box.
                    send_button.props("icon=send")
                    send_button.disable()
                    text.disable()
                else:
                    send_button.props("icon=send")
                    text.enable()
                    refresh_send_state()
            except Exception as e:  # noqa: BLE001
                logger.debug("stream button state update failed: %s", e)

        ctx.refresh_stream_button = refresh_stream_button

        def on_button_click() -> None:
            """Route the button: send normally, stop the current chat's run."""
            if ctx.chat_is_streaming(ctx.chat_id):
                logger.debug("stop requested from the send/stop button")
                stream.stop_stream(ctx, ctx.chat_id)
            else:
                send()

        send_button.on_click(on_button_click)

        # Plain Enter sends the message and prevents the default newline
        def handle_enter(e: GenericEventArguments):
            """Send on Enter, insert newline on Shift+Enter."""
            if e.args.get("shiftKey"):
                text.value += "\n"
            else:
                send()

        text.on("keydown.enter.exact.prevent", handle_enter)
        # Clicking the send icon inside the textarea also sends (or stops).
        text.on("click:append", on_button_click)

    # Disable chat input (and send) until backend is ready.  initial_load's
    # model fetch calls refresh_send_state() to re-enable when configured.
    try:
        text.disable()
        send_button.disable()
    except Exception as e:  # noqa: BLE001
        logger.debug("disable input failed: %s", e)
