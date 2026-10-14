#!/usr/bin/env python3
"""
Transcript widget for the Klea TUI.

The scrolling chat transcript: each message is rendered MIME-aware, mirroring
the web's ``chat_bubble`` rules (``devdocs/system/tui.md``, ADR-0040):
Markdown for prose, a syntax-highlighted block for diffs and ``text/x-<lang>``
code, an optional header for tool blocks, and a plain text fallback whenever
a MIME cannot be rendered (invariant I7).

File: klea_utils/ui/tui/components/transcript.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.widgets import Static

if TYPE_CHECKING:
    from klea_utils.ui.state import MessageData

logger = logging.getLogger(__name__)

#: MIME types rendered as a unified diff (``text/x-diff`` is the de-facto
#: type; ``text/x-patch`` is git's).  Mirrors the web ``chat_bubble`` rules.
_DIFF_MIMES = ("text/x-diff", "text/x-patch")


def _render_body(mime: str, data: str, text: str) -> RenderableType:
    """Render a block body according to its MIME type.

    Diffs and ``text/x-<lang>`` code render with syntax highlighting; media
    falls back to its text (inline media is a non-goal, ADR-0040); everything
    else (including ``text/markdown`` and an empty mime) renders as Markdown.

    :param mime: MIME type of *data*.
    :param data: The renderable payload (text or base64).
    :param text: The text fallback (``display``).
    :returns: A Rich renderable for the block body.
    """
    if mime in _DIFF_MIMES:
        return Syntax(data or text, "diff", word_wrap=True)
    if mime.startswith(("image/", "audio/")):
        # Inline media is deferred; show the text fallback note (ADR-0040).
        return Markdown(text or data)
    if mime.startswith("text/x-"):
        language = mime.split("/", 1)[1][2:]
        return Syntax(data or text, language, word_wrap=True)
    return Markdown(data or text)


def render_message(message: Mapping[str, Any]) -> RenderableType:
    """Build the Rich renderable for one transcript message.

    User lines render as a bold ``"> "`` prompt; agent and tool messages
    render MIME-aware (see :func:`_render_body`), with a bold header for tool
    blocks (in the error colour when the tool result failed).

    :param message: A message dict (see ``state.MessageData``).
    :returns: A Rich renderable for the transcript.
    """
    role = message.get("role", "agent")
    text = message.get("text", "")
    header = message.get("header", "")
    if role == "user":
        return Text(f"> {text}", style="bold")
    parts: list[RenderableType] = []
    if header:
        style = "bold red" if message.get("is_error") else "bold"
        parts.append(Text(header, style=style))
    parts.append(_render_body(message.get("mime", ""), message.get("data", ""), text))
    return Group(*parts) if len(parts) > 1 else parts[0]


class Transcript(VerticalScroll):
    """The scrolling transcript of chat messages."""

    def __init__(self, *, id: str | None = None) -> None:
        """Build an empty transcript.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)

    def compose(self) -> ComposeResult:
        """Compose the empty placeholder (removed on the first message)."""
        yield Static("No messages yet.", id="transcript-empty")

    async def append(self, message: MessageData) -> None:
        """Append one message and scroll to the bottom.

        :param message: A message dict (see ``state.MessageData``).
        """
        for placeholder in self.query("#transcript-empty"):
            placeholder.remove()
        await self.mount(Static(render_message(message)))
        self.scroll_end(animate=False)

    async def set_messages(self, messages: Sequence[Mapping[str, Any]]) -> None:
        """Rebuild the transcript from a chat's messages (on load/switch).

        :param messages: The chat's ``messages`` list.
        """
        await self.remove_children()
        if not messages:
            await self.mount(Static("No messages yet.", id="transcript-empty"))
            return
        for message in messages:
            await self.mount(Static(render_message(message)))
        self.scroll_end(animate=False)


# Re-exported for callers that build a renderable directly (e.g. tests).
__all__ = ["Transcript", "render_message"]
