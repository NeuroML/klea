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

from nicegui import ui

from klea_utils.ui.linkify import linkify_md
from klea_utils.ui.web.nicegui.components.chat_bubble import ChatBubble
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats

logger = logging.getLogger(__name__)


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
        ``scroll_area`` and ``stream_container`` are filled in here.
    """
    with ui.scroll_area().classes("w-full grow chat-scroll-area") as scroll_area:
        ctx.scroll_area = scroll_area
        ctx.chat_area = ui.column().classes("w-full")
        ctx.stream_container = ui.column().classes("w-full")

    ctx.render_chat_area = lambda: _render_messages(ctx)
    ctx.scroll_chat_bottom = lambda: _scroll_to_bottom(ctx)
    _render_messages(ctx)
