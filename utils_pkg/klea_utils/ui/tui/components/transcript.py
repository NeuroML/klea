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
from typing import TYPE_CHECKING, Any, ClassVar

from rich.console import Group, RenderableType
from rich.markdown import Markdown
from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical, VerticalScroll
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


def render_message(
    message: Mapping[str, Any], *, error_style: str = "bold"
) -> RenderableType:
    """Build the Rich renderable for one transcript message.

    User lines render as a bold ``"> "`` prompt; agent and tool messages
    render MIME-aware (see :func:`_render_body`), with a bold header for tool
    blocks (in the theme's error colour when the tool result failed).

    :param message: A message dict (see ``state.MessageData``).
    :param error_style: Rich style for an errored tool block's header; the
        caller passes the active theme's ``error`` colour so it follows a
        theme change (defaults to plain bold for a themeless caller).
    :returns: A Rich renderable for the transcript.
    """
    role = message.get("role", "agent")
    text = message.get("text", "")
    header = message.get("header", "")
    if role == "user":
        return Text(f"> {text}", style="bold")
    if role == "notice":
        prefix = f"{header}: " if header else ""
        return Text(prefix + text, style="dim")
    parts: list[RenderableType] = []
    if header:
        style = error_style if message.get("is_error") else "bold"
        parts.append(Text(header, style=style))
    parts.append(_render_body(message.get("mime", ""), message.get("data", ""), text))
    return Group(*parts) if len(parts) > 1 else parts[0]


class MessageBlock(Vertical):
    """One transcript message, collapsed to a few lines until expanded.

    Long messages are clipped to :data:`COLLAPSED_LINES` rendered rows (so the
    clip is width-aware, like the web UI's ``max-height`` clamp); a muted hint
    appears when there is more, and clicking the block or pressing Enter (when
    it has focus) toggles between the clipped preview and the full text.  Short
    messages are not focusable and show no hint.
    """

    #: Rows shown while collapsed.
    COLLAPSED_LINES = 4

    can_focus = True

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("enter,space", "toggle_expand", "Expand/collapse", show=False)
    ]

    DEFAULT_CSS = """
    MessageBlock {
        height: auto;
        width: 100%;
        padding: 1 0;
    }
    MessageBlock.message-user {
        background: $primary 15%;
    }
    MessageBlock .message-scroll {
        height: auto;
        max-height: 4;
        overflow-y: hidden;
    }
    MessageBlock .message-scroll.-expanded {
        max-height: 10000;
        overflow-y: hidden;
    }
    MessageBlock .message-more {
        height: 1;
        color: $text-muted;
        display: none;
    }
    """

    def __init__(
        self, message: Mapping[str, Any], *, error_style: str = "bold"
    ) -> None:
        """Build the block for *message*.

        :param message: A message dict (see ``state.MessageData``).
        :param error_style: Rich style for an errored tool block's header.
        """
        super().__init__()
        self._message = message
        self._error_style = error_style
        # Role-specific styling (currently a tinted surface for user messages).
        self.add_class(f"message-{message.get('role', 'agent')}")

    def compose(self) -> ComposeResult:
        """Compose the clipped body and the expand hint."""
        with VerticalScroll(classes="message-scroll"):
            yield Static(render_message(self._message, error_style=self._error_style))
        yield Static("", classes="message-more", markup=False)

    def on_mount(self) -> None:
        """Decide, after layout, whether the hint is needed."""
        self.call_after_refresh(self._refresh_more)

    def _refresh_more(self) -> None:
        """Show the hint and make the block focusable only on overflow."""
        scroll = self.query_one(".message-scroll", VerticalScroll)
        more = self.query_one(".message-more", Static)
        overflowing = scroll.virtual_size.height > self.COLLAPSED_LINES
        self.can_focus = overflowing
        more.display = overflowing
        if overflowing:
            action = "collapse" if self._expanded else "expand"
            more.update(f"… Enter/click to {action}")

    @property
    def _expanded(self) -> bool:
        """Whether the block is currently expanded."""
        return self.query_one(".message-scroll", VerticalScroll).has_class("-expanded")

    def action_toggle_expand(self) -> None:
        """Expand or collapse the message."""
        scroll = self.query_one(".message-scroll", VerticalScroll)
        if scroll.virtual_size.height <= self.COLLAPSED_LINES:
            return
        scroll.toggle_class("-expanded")
        self._refresh_more()

    def on_click(self, event: Any) -> None:
        """Toggle on click.

        :param event: The click event.
        """
        self.action_toggle_expand()


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

    def _error_style(self) -> str:
        """Return a bold style in the active theme's error colour.

        The TUI follows the Textual theme, so an errored tool block's header
        uses ``$error`` rather than a literal colour.  Falls back to plain bold
        when the theme exposes no error colour.

        :returns: A Rich style string.
        """
        error = self.app.get_css_variables().get("error")
        return f"bold {error}" if error else "bold"

    async def append(self, message: MessageData) -> None:
        """Append one message and scroll to the bottom.

        :param message: A message dict (see ``state.MessageData``).
        """
        for placeholder in self.query("#transcript-empty"):
            placeholder.remove()
        await self.mount(MessageBlock(message, error_style=self._error_style()))
        self.scroll_end(animate=False)

    async def set_messages(self, messages: Sequence[Mapping[str, Any]]) -> None:
        """Rebuild the transcript from a chat's messages (on load/switch).

        :param messages: The chat's ``messages`` list.
        """
        await self.remove_children()
        if not messages:
            await self.mount(Static("No messages yet.", id="transcript-empty"))
            return
        error_style = self._error_style()
        for message in messages:
            await self.mount(MessageBlock(message, error_style=error_style))
        self.scroll_end(animate=False)


# Re-exported for callers that build a renderable directly (e.g. tests).
__all__ = ["Transcript", "render_message"]
