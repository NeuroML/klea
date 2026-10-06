#!/usr/bin/env python3
"""
Chat area component: message list, scroll, and empty-state.

Builds the scroll area that holds the message bubbles and the stream
progress container, and registers the render/scroll callbacks on the
page context.

File: klea_utils/ui/web/nicegui/components/chat_area.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from typing import Any

from nicegui import ui

from klea_utils.llm import missing_required_roles
from klea_utils.ui.linkify import linkify_md
from klea_utils.ui.web.nicegui.components.chat_bubble import ChatBubble
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import TurnStatus, chats, missing_credentials

logger = logging.getLogger(__name__)


def render_turn_status(ctx: PageContext, retry_cb=None) -> None:
    """Render the turn status region for the active chat from its state.

    The region is a single slot under the transcript, owned by the chat's
    ``status`` dict (``state.ChatData``), so it is restored on chat switch
    and replaced wholesale on the next turn.  ``kind`` selects the content:

    * ``progress`` - a spinner plus the live ``heading``.
    * ``error``    - the message and, when ``resumable``, a Retry action.
    * ``stopped``  - a plain "Stopped" line.
    * ``idle``/missing - nothing.

    :param ctx: The shared page context (uses ``turn_status_container``).
    :param retry_cb: Callable invoked by the Retry button; the stream
        component passes it when rendering a live error.
    """
    status = _current_turn_status(ctx)
    kind = status.get("kind", "idle")
    container = ctx.turn_status_container
    container.clear()
    if kind == "idle":
        return
    with container:
        if kind == "progress":
            with ui.row().classes("w-full items-center gap-2 p-2"):
                ui.spinner(type="dots").classes("w-4 h-4")
                label = ui.label(status.get("heading", "")).classes(
                    "text-xs text-grey-5 italic"
                )
            ctx.turn_status_label = label
        elif kind == "error":
            with ui.row().classes("w-full items-center gap-2 p-2"):
                ui.icon("error").classes("text-negative")
                message = f"Error: {status.get('message', '')}"
                if "No model configured" in message:
                    message += (
                        " Use the settings (gear) icon to choose a model for "
                        "this chat, then retry."
                    )
                ui.label(message).classes("text-xs text-negative flex-grow")
                if status.get("resumable") and retry_cb is not None:
                    ui.button("Retry", on_click=retry_cb).props(
                        "flat dense color=primary"
                    )
        elif kind == "stopped":
            ui.label("Stopped").classes("text-xs text-grey-5 italic p-2")
        elif kind == "awaiting_input":
            _render_interrupt_form(ctx)


def _current_turn_status(ctx: PageContext) -> TurnStatus:
    """Return the active chat's status dict, or ``{"kind": "idle"}``."""
    if not ctx.chat_id:
        return {"kind": "idle"}
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
    if not chat:
        return {"kind": "idle"}
    return chat.get("turn_status") or {"kind": "idle"}


def _interrupt_ask(ctx: PageContext) -> dict[str, Any]:
    """Return the active chat's pending HITL ask, or ``{}``."""
    if not ctx.chat_id:
        return {}
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
    return (chat or {}).get("interrupt") or {}


def _render_interrupt_form(ctx: PageContext) -> None:
    """Render the HITL ask form in the turn status region (ADR-0046).

    A ``review`` ask gets Approve / Request changes / Cancel; an ``input``
    ask gets one text field per question plus Answer / Cancel.  Submitting
    calls :attr:`PageContext.submit_interrupt` (registered by the input area).
    """
    ask = _interrupt_ask(ctx)
    kind = ask.get("kind", "input")
    interrupt_id = ask.get("interrupt_id")
    heading = "Plan review" if kind == "review" else "User input"
    with ui.column().classes("w-full gap-2 p-3 border border-primary rounded"):
        ui.label(heading).classes("text-sm font-bold")
        if kind == "review":
            if plan := ask.get("plan"):
                ui.code(plan, language=None).classes(
                    "text-xs w-full nicegui-code-noformat"
                )
            if question := ask.get("question"):
                ui.label(question).classes("text-sm")
            feedback = (
                ui.textarea(placeholder="Feedback (required to request changes)")
                .props("outlined autogrow")
                .classes("w-full")
            )
            with ui.row().classes("gap-2"):
                ui.button(
                    "Approve",
                    on_click=lambda: ctx.submit_interrupt(
                        {"decision": "approve"}, interrupt_id, False
                    ),
                ).props("unelevated color=primary")
                ui.button(
                    "Request changes",
                    on_click=lambda: ctx.submit_interrupt(
                        {"decision": "revise", "feedback": feedback.value or ""},
                        interrupt_id,
                        False,
                    ),
                ).props("outline")
                ui.button(
                    "Cancel",
                    on_click=lambda: ctx.submit_interrupt(None, interrupt_id, True),
                ).props("flat color=negative")
            return

        # input kind: one text field per question.
        questions = ask.get("questions")
        if not isinstance(questions, list) or not questions:
            fallback = ask.get("question") or "More information is needed to continue."
            questions = [{"question": fallback}]
        fields = []
        for question in questions:
            ui.label(str(question.get("question", ""))).classes("text-sm")
            fields.append(ui.input().props("outlined dense").classes("w-full"))

        def _submit() -> None:
            ctx.submit_interrupt(
                {"answers": [field.value or "" for field in fields]},
                interrupt_id,
                False,
            )

        with ui.row().classes("gap-2"):
            ui.button("Answer", on_click=_submit).props("unelevated color=primary")
            ui.button(
                "Cancel",
                on_click=lambda: ctx.submit_interrupt(None, interrupt_id, True),
            ).props("flat color=negative")


def _render_messages(ctx: PageContext) -> None:
    """Rebuild the scroll-area content (welcome or messages) for the page.

    Uses explicit clear+rebuild instead of ``@ui.refreshable``
    to avoid issues with the welcome-to-empty-chat transition.
    """
    current = ctx.chat_id
    logger.debug(
        "current=%s msgs=%d",
        current,
        len(chats.get(f"{ctx.user_id}:{current}", {}).get("messages", []))
        if current
        else 0,
    )
    ctx.chat_area.clear()
    # The turn status region is rendered from the active chat's turn status,
    # so switching chats restores that chat's own status and no stale
    # marker bleeds across.  A live run re-renders this from state too, so
    # there is a single source of truth for the region.
    render_turn_status(ctx)
    with ctx.chat_area:
        if not current:
            with (
                ui.column()
                .classes("w-full h-full items-center justify-center gap-4")
                .style("flex: 1; display: flex;")
            ):
                ui.label("Start a conversation").classes("text-xl text-grey-5")
                ui.label("Type your message below to begin").classes(
                    "text-sm text-grey-5"
                )
                # First-run setup call-to-action: point the user straight
                # at the model dialog when anything required is missing.
                missing_models = missing_required_roles(ctx.session_model_info)
                missing_keys = missing_credentials(ctx.session_model_info)
                if missing_models or missing_keys:
                    with ui.card().classes("items-center gap-1 mt-2 p-4"):
                        ui.label("Models are not configured").classes(
                            "text-sm font-bold"
                        )
                        if missing_models:
                            ui.label(
                                "Set the required models to start: "
                                + ", ".join(
                                    role.capitalize() for role in missing_models
                                )
                            ).classes("text-xs text-grey-6")
                        elif missing_keys:
                            providers = sorted(
                                {
                                    (
                                        ctx.session_model_info[role].get("credential")
                                        or {}
                                    ).get("provider", "")
                                    for role in missing_keys
                                }
                                - {""}
                            )
                            ui.label(
                                "Add an API key for: " + ", ".join(providers)
                            ).classes("text-xs text-grey-6")
                        ui.button(
                            "Choose models", on_click=ctx.model_config_dialog
                        ).props("unelevated color=primary")
        else:
            current_chat = chats.get(f"{ctx.user_id}:{current}")
            msgs = current_chat["messages"] if current_chat else []
            for idx, msg in enumerate(msgs):
                collapsed = idx not in ctx.expanded
                text = msg.get("text", "")
                ChatBubble(
                    text=linkify_md(text),
                    stamp=msg.get("stamp", ""),
                    role=msg.get("role", "agent"),
                    header=msg.get("header", ""),
                    mime=msg.get("mime", ""),
                    data=msg.get("data", ""),
                    meta=msg.get("meta", {}),
                    is_error=msg.get("is_error", False),
                    collapsed=collapsed,
                    idx=idx,
                    on_copy=lambda t=text: ui.run_javascript(
                        f"navigator.clipboard.writeText({json.dumps(t)})"
                    ),
                    on_expand=lambda i=idx: (
                        (
                            ctx.expanded.discard(i)
                            if i in ctx.expanded
                            else ctx.expanded.add(i)
                        )
                        or ctx.render_chat_area()
                    ),
                )
    ctx.scroll_chat_bottom()


def _scroll_to_bottom(ctx: PageContext) -> None:
    """Scroll chat area to the bottom.

    Uses Quasar's ``setScrollPosition`` (via NiceGUI's
    ``scroll_to(pixels=99999)``) instead of raw JavaScript because
    NiceGUI batches UI updates and JS into the same WebSocket packet
     ---  by the time a ``setTimeout`` or ``requestAnimationFrame``
    callback fires the new DOM may not be laid out yet, so
    ``scrollTop = scrollHeight`` or ``scrollIntoView`` land at the
    wrong position.

    ``setScrollPosition`` is Quasar's own scroll API on
    ``QScrollArea``; it coordinates with its internal layout cycle
    so the scroll lands correctly after the content updates are
    painted.  The large pixel value is safe  ---  Quasar clamps it to
    the actual scrollable extent.
    """
    logger.debug("attempting scroll for chat=%s", ctx.chat_id)
    ctx.scroll_area.scroll_to(pixels=99999)


def attach_chat_area(ctx: PageContext) -> None:
    """Build the chat scroll area and register its render callbacks.

    Must be called with the chat tab panel as the ambient NiceGUI
    context (the surrounding layout decides where the tab panels go).

    :param ctx: The shared page context; ``chat_area``,
        ``scroll_area`` and ``turn_status_container`` are filled in here.
    """
    with ui.scroll_area().classes("w-full grow chat-scroll-area") as scroll_area:
        ctx.scroll_area = scroll_area
        ctx.chat_area = ui.column().classes("w-full")
        ctx.turn_status_container = ui.column().classes("w-full")

    ctx.render_chat_area = lambda: _render_messages(ctx)
    ctx.refresh_turn_status = lambda: render_turn_status(
        ctx, retry_cb=ctx.turn_retry_cb
    )
    ctx.scroll_chat_bottom = lambda: _scroll_to_bottom(ctx)
    _render_messages(ctx)
