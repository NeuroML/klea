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

from klea_utils.ui.tui.components.selectable import SelectableStatic
from klea_utils.ui.tui.glyphs import GlyphSet, get_glyphs
from klea_utils.ui.tui.theme import get_role_tints

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


def _style(role: str, tints: Mapping[str, str], *, bold: bool = False) -> str:
    """Build a Rich style for *role* from the resolved role tints.

    :param role: A key of ``theme.ROLE_TINTS``.
    :param tints: Role -> colour resolved from the active theme; a role the
        theme does not colour resolves to an empty string.
    :param bold: Whether the style also sets ``bold``.
    :returns: A non-empty Rich style string.
    """
    parts = ["bold"] if bold else []
    parts.extend(tints.get(role, "").split())
    return " ".join(dict.fromkeys(parts)) or "none"


def _render_notice(
    message: Mapping[str, Any], *, glyphs: GlyphSet, tints: Mapping[str, str]
) -> RenderableType:
    """Render a notice as a muted, glyph-prefixed line.

    :param message: A message dict with ``role == "notice"``.
    :param glyphs: The active glyph table.
    :param tints: Role -> colour resolved from the active theme.
    :returns: A Rich renderable for the notice.
    """
    header = message.get("header", "")
    text = message.get("text", "")
    prefix = f"{header}\n" if header else ""
    return Text(f"{glyphs.pending} {prefix}{text}", style=_style("notice", tints))


def _render_message(
    message: Mapping[str, Any], *, glyphs: GlyphSet, tints: Mapping[str, str]
) -> RenderableType:
    """Build the Rich renderable for one transcript message.

    User lines render verbatim (no Markdown decoration) in the bold user tint;
    notices render muted with the pending glyph; agent and tool messages render
    MIME-aware (see :func:`_render_body`), with a glyph-marked header for tool
    blocks in the theme's success colour, or its error colour when the tool
    result failed.

    :param message: A message dict (see ``state.MessageData``).
    :param glyphs: The active glyph table.
    :param tints: Role -> colour resolved from the active theme.
    :returns: A Rich renderable for the transcript.
    """
    role = message.get("role", "agent")
    text = message.get("text", "")
    header = message.get("header", "")
    if role == "user":
        return Text(text, style=_style("user", tints, bold=True))
    if role == "notice":
        return _render_notice(message, glyphs=glyphs, tints=tints)
    parts: list[RenderableType] = []
    if header:
        failed = bool(message.get("is_error"))
        marker = glyphs.failure if failed else glyphs.success
        style = _style("tool_error" if failed else "tool_success", tints, bold=True)
        parts.append(Text(f"{marker} {header}", style=style))
    parts.append(_render_body(message.get("mime", ""), message.get("data", ""), text))
    return Group(*parts) if len(parts) > 1 else parts[0]


def render_message(
    message: Mapping[str, Any],
    *,
    error_style: str = "bold",
    glyphs: GlyphSet | None = None,
    tints: Mapping[str, str] | None = None,
) -> RenderableType:
    """Build the Rich renderable for one transcript message.

    A thin, themeless wrapper around :func:`_render_message` for callers that
    build a single renderable directly (for example tests).  The transcript
    itself resolves the glyph table and the role tints once and passes them in.

    :param message: A message dict (see ``state.MessageData``).
    :param error_style: Style for an errored tool block's header when no
        resolved ``tool_error`` tint is supplied (defaults to plain bold).
    :param glyphs: The glyph table to draw markers from; the terminal's table
        is detected when omitted.
    :param tints: Role -> colour resolved from the active theme; omitted roles
        render with no colour.
    :returns: A Rich renderable for the transcript.
    """
    active_tints = dict(tints or {})
    if not active_tints.get("tool_error"):
        active_tints["tool_error"] = error_style
    return _render_message(message, glyphs=glyphs or get_glyphs(), tints=active_tints)


class MessageBlock(Vertical):
    """One transcript message, collapsed to a few lines until expanded.

    Long messages are clipped to :data:`COLLAPSED_LINES` rendered rows (so the
    clip is width-aware, like the web UI's ``max-height`` clamp); a muted hint
    appears when there is more, and clicking the block or pressing Enter (when
    it has focus) toggles between the clipped preview and the full text.  A
    drag selects text instead of toggling.  Short messages are not focusable
    and show no hint.
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
        padding: 1 1;
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
        self,
        message: Mapping[str, Any],
        *,
        glyphs: GlyphSet | None = None,
        tints: Mapping[str, str] | None = None,
    ) -> None:
        """Build the block for *message*.

        :param message: A message dict (see ``state.MessageData``).
        :param glyphs: The active glyph table; detected from the terminal when
            omitted so a standalone block still renders.
        :param tints: Role -> colour resolved from the active theme.
        """
        super().__init__()
        self._message = message
        self._glyphs = glyphs or get_glyphs()
        self._tints = dict(tints or {})
        # Role-specific styling (currently a tinted surface for user messages).
        self.add_class(f"message-{message.get('role', 'agent')}")

    def compose(self) -> ComposeResult:
        """Compose the clipped body and the expand hint."""
        with VerticalScroll(classes="message-scroll"):
            # SelectableStatic adds Textual's selection protocol to the
            # MIME-aware Rich renderable; see its class docstring for why we
            # keep Rich rendering instead of the native Markdown widget.
            yield SelectableStatic(
                render_message(self._message, glyphs=self._glyphs, tints=self._tints)
            )
        yield SelectableStatic("", classes="message-more", markup=False)

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
            more.update(
                f"{self._glyphs.overflow} Enter/click to {action} | drag to select"
            )

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
        """Toggle on click, unless the click finished a text selection.

        Textual keeps the selection after a drag but clears it before a plain
        click, so an active selection means the user selected text rather than
        asking to expand or collapse the block.

        :param event: The click event.
        """
        if self.screen.selections:
            return
        self.action_toggle_expand()


class Transcript(VerticalScroll):
    """The scrolling transcript of chat messages."""

    def __init__(self, *, id: str | None = None) -> None:
        """Build an empty transcript.

        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        # Resolved once, at component init, so every block in this transcript
        # draws the same table even if the terminal changes mid-session.
        self._glyphs = get_glyphs()

    def compose(self) -> ComposeResult:
        """Compose the empty placeholder (removed on the first message)."""
        yield SelectableStatic("No messages yet.", id="transcript-empty")

    def _resolved_tints(self) -> dict[str, str]:
        """Resolve the role tints to colours for the active theme.

        The TUI follows the Textual theme, so every role maps to a semantic
        colour token (``$error``, ``$success``, ...) rather than a literal
        colour.  Tokens the active theme does not define resolve to an empty
        string, which the render helpers treat as "no colour".

        :returns: Role -> colour mapping (see ``theme.ROLE_TINTS``).
        """
        variables = self.app.get_css_variables()
        tints: dict[str, str] = {}
        for role, token in get_role_tints().items():
            tints[role] = variables.get(token.lstrip("$"), "")
        return tints

    async def append(self, message: MessageData) -> None:
        """Append one message and scroll to the bottom.

        :param message: A message dict (see ``state.MessageData``).
        """
        for placeholder in self.query("#transcript-empty"):
            placeholder.remove()
        await self.mount(
            MessageBlock(message, glyphs=self._glyphs, tints=self._resolved_tints())
        )
        self.scroll_end(animate=False)

    async def set_messages(self, messages: Sequence[Mapping[str, Any]]) -> None:
        """Rebuild the transcript from a chat's messages (on load/switch).

        :param messages: The chat's ``messages`` list.
        """
        await self.remove_children()
        if not messages:
            await self.mount(
                SelectableStatic("No messages yet.", id="transcript-empty")
            )
            return
        tints = self._resolved_tints()
        for message in messages:
            await self.mount(MessageBlock(message, glyphs=self._glyphs, tints=tints))
        self.scroll_end(animate=False)


# Re-exported for callers that build a renderable directly (e.g. tests).
__all__ = ["Transcript", "render_message"]
