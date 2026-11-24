"""
Chat message block widget.

File: klea_utils/ui/web/nicegui/components/chat_bubble.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import ui

from klea_utils.ui.glyphs import UNICODE_GLYPHS
from klea_utils.ui.linkify import linkify_html

logger = logging.getLogger(__name__)

#: Browser output is always Unicode-capable, so the web reuses the TUI's glyph
#: table directly (the terminal detection / ASCII fallback does not apply); one
#: shared source keeps the role markers identical in both frontends.
GLYPHS = UNICODE_GLYPHS

#: Recognised transcript roles; anything else falls back to ``agent``.
_ROLES = ("user", "agent", "tool", "system")

#: MIME types rendered as a unified diff (``text/x-diff`` is the de-facto
#: type; ``text/x-patch`` is git's).  Rendered as a fenced block, which
#: markdown2/Pygments highlights with its diff lexer.
_DIFF_MIMES = ("text/x-diff", "text/x-patch")

#: Lines a collapsed message shows before the expand control appears (matches
#: ``.msg-collapsed { max-height: 9em; }`` at roughly 1.5em line-height).
MSG_COLLAPSED_LINES = 6

#: Lines a collapsed tool block shows (matches
#: ``.tool-block-collapsed { max-height: 1.8em; }``).
TOOL_COLLAPSED_LINES = 1

#: Conservative character width per rendered line; over-detect rather than clip
#: content with no way to expand it.
MSG_WRAP_CHARS = 60

#: Text labels for the expand/collapse control.
EXPAND_LABEL = "Show more"
COLLAPSE_LABEL = "Show less"

#: Class marking the labelled expand/collapse control.
EXPAND_CONTROL_CLASS = "msg-expand"


def expand_label(collapsed: bool) -> str:
    """Return the label for the expand/collapse control.

    A collapsed (truncated) block prefixes its label with the shared overflow
    glyph, so the truncation marker is drawn from the glyph table (never a
    pasted literal) and disappears once the block is expanded.
    """
    return COLLAPSE_LABEL if not collapsed else f"{GLYPHS.overflow} {EXPAND_LABEL}"


def content_overflows(role: str, text: str, data: str = "", mime: str = "") -> bool:
    """Return whether the body would overflow its collapsed height.

    A pure-Python estimate: each logical line counts as at least one rendered
    line, and long lines wrap at :data:`MSG_WRAP_CHARS`.  The estimate is
    deliberately conservative, so a short body never loses content and a
    long one always gets a control.
    """
    body = text if mime.startswith(("image/", "audio/")) else data or text
    lines = 0
    for line in body.splitlines() or [""]:
        lines += max(1, (len(line) + MSG_WRAP_CHARS - 1) // MSG_WRAP_CHARS)
    limit = TOOL_COLLAPSED_LINES if role == "tool" else MSG_COLLAPSED_LINES
    logger.debug("overflow check role=%s lines=%d limit=%d", role, lines, limit)
    return lines > limit


def _is_code_mime(mime: str) -> bool:
    """Return whether *mime* is rendered by ``ui.code`` (not markdown)."""
    return mime in _DIFF_MIMES or mime.startswith("text/x-")


def _render_body(
    role: str,
    mime: str,
    data: str,
    text: str,
    *,
    notice: bool = False,
    notice_glyph: bool = False,
) -> None:
    """Render a block body according to its role and MIME type.

    A **user** message is shown verbatim -- no Markdown decoration, so literal
    underscores, ``#``, lists and newlines in the user's input (URLs, paths,
    pasted code) are preserved; bare URLs stay clickable.  A **notice** is
    likewise rendered as plain, escaped text, mirroring the TUI, where a notice
    is plain Rich text and only agent/tool bodies go through a Markdown
    renderer.  The pending glyph normally marks the notice's header line; a
    notice *without* a header carries it here instead (``notice_glyph``), so
    every notice shows exactly one marker.  Other roles use the MIME type:
    diffs and code use NiceGUI's native ``ui.code`` (syntax highlighting and a
    copy button); text uses ``ui.markdown``.
    """
    if role == "user":
        ui.html(linkify_html(text), sanitize=True).classes(
            "whitespace-pre-wrap break-words"
        )
    elif notice:
        body = f"{GLYPHS.pending} {text}" if notice_glyph else text
        ui.html(linkify_html(body), sanitize=True).classes(
            "whitespace-pre-wrap break-words"
        )
    elif mime in _DIFF_MIMES:
        ui.code(data or text, language="diff").classes("tool-code")
    elif mime == "text/markdown" or not mime:
        ui.markdown(data or text, extras=["fenced-code-blocks", "tables", "alerts"])
    elif mime.startswith(("image/", "audio/")):
        # Inline media rendering is deferred; show the text fallback note.
        ui.markdown(text, extras=["fenced-code-blocks", "tables", "alerts"])
    elif mime.startswith("text/x-"):
        language = mime.split("/", 1)[1][2:]
        ui.code(data or text, language=language).classes("tool-code")
    else:
        ui.markdown(data or text, extras=["fenced-code-blocks", "tables", "alerts"])


class ChatBubble(ui.element):
    """A full-width chat transcript block with built-in actions.

    All roles span the full chat width (opencode-style); the surface colour
    distinguishes them (semantic ``chat-bubble--<role>`` classes in
    ``theme.py``), so user blocks read as tinted prompts, agent blocks as
    plain document text, and tool blocks as neutral blocks.  Each block has
    an optional header, collapsible text content, an expand / collapse
    control that starts the footer line (its label is prefixed with the
    shared overflow glyph while the body is truncated), and a right-aligned
    timestamp with the copy button -- all flowing naturally inside the block
    (no CSS hacks).

    Usage inside a ``@ui.refreshable``::

        ChatBubble(
            text="Hello", stamp="12:00", role="user",
            collapsed=False, idx=0,
            on_expand=lambda: print("toggle"),
            on_copy=lambda: print("copy"),
        )
    """

    def __init__(
        self,
        text: str,
        stamp: str,
        role: str,
        collapsed: bool,
        idx: int,
        header: str = "",
        mime: str = "",
        data: str = "",
        meta: dict | None = None,
        is_error: bool = False,
        on_expand=None,
        on_copy=None,
        *,
        overflowing: bool = False,
    ) -> None:
        """Build the block.

        :param text: Message text (markdown); also the fallback body.
        :param stamp: Timestamp string.
        :param role: ``"user"``, ``"agent"``, ``"tool"``, ``"system"`` (a
            client command's output), or ``"notice"`` (a compact progress
            note, rendered with the system surface); unknown values fall back
            to ``"agent"``.
        :param collapsed: ``True`` if the text is collapsed to a few lines.
        :param idx: Message index (used for expand/collapse tracking).
        :param header: Optional small bold header, e.g. a tool block's
            name and change counts.
        :param mime: MIME type of ``data``; selects the renderer (``text/x-diff``
            -> coloured diff, ``text/x-<lang>`` -> code, media deferred).
        :param data: The renderable payload (text or base64).
        :param meta: Extra entry metadata (path, language, ...); reserved for
            renderers that need it.
        :param is_error: Whether a ``tool`` block's result is an error; the
            header is rendered in the theme's negative colour.
        :param overflowing: Whether the body overflows its collapsed height;
            the expand/collapse control is shown only when it does.
        :param on_expand: Callable with no args, fired on expand/collapse click.
        :param on_copy: Callable with no args, fired on copy click.
        """
        super().__init__("div")

        is_notice = role == "notice"
        if is_notice:
            # A progress notice is a muted, system-style transcript block.  It
            # keeps its notice identity for rendering: notices are plain text
            # (see ``_render_body``), never Markdown, so underscores and other
            # markup in tool names stay literal (mirrors the TUI).
            role = "system"
        elif role not in _ROLES:
            role = "agent"

        self.classes("w-full flex flex-col")

        with (
            self,
            ui.element("div").classes(
                "flex w-full flex-col rounded-lg p-3 gap-1 "
                f"chat-bubble chat-bubble--{role}"
            ),
        ):
            if header:
                failed = role == "tool" and is_error
                header_classes = "text-xs font-bold"
                header_classes += " tool-header--error" if failed else " text-grey-6"
                # Mirror the TUI transcript: the tick / cross are reserved for
                # tool-call headers (success/failure), coloured by the same role
                # tokens; any other header -- a notice's node label -- takes the
                # pending marker, exactly as the TUI's ``_render_notice`` does.
                if role == "tool":
                    marker = GLYPHS.failure if failed else GLYPHS.success
                else:
                    marker = GLYPHS.pending
                ui.label(f"{marker} {header}").classes(header_classes)

            if role == "tool":
                # A tool block collapses to a one-line preview (the header is
                # the title), expandable; long diffs/command output stay hidden.
                text_cls = "tool-block-collapsed" if collapsed else "msg-expanded"
            else:
                text_cls = "msg-collapsed" if collapsed else "msg-expanded"
            # ``ui.markdown`` bodies get the chat markdown styling; ``ui.code``
            # bodies render their own box, so adding ``chat-markdown`` there
            # would stack our ``pre``/``code`` rules on top of it.  A user
            # message is plain text (no markdown styling).
            classes = f"text-left {text_cls}"
            if role != "user" and not is_notice and not _is_code_mime(mime):
                classes += " chat-markdown"
            with ui.element("div").classes(classes):
                _render_body(
                    role,
                    mime,
                    data,
                    text,
                    notice=is_notice,
                    notice_glyph=is_notice and not header,
                )

            with ui.row().classes(
                "w-full flex flex-row justify-between items-center gap-1"
            ):
                # Left slot: the expand control (whose label carries the
                # overflow ellipsis) starts the line when the body is
                # truncated.  The slot is emitted even when empty so
                # ``justify-between`` keeps the timestamp/copy group
                # right-aligned in both branches.
                with ui.element("div").classes("flex flex-row items-center gap-1"):
                    if on_expand and overflowing:
                        ui.button(expand_label(collapsed), on_click=on_expand).props(
                            "flat dense size=sm"
                        ).classes(EXPAND_CONTROL_CLASS)

                with ui.row().classes("flex flex-row items-center gap-1"):
                    ui.label(stamp).classes("text-xs text-grey-5")

                    if on_copy:
                        ui.button(icon="content_copy").props(
                            "flat dense round size=sm"
                        ).classes("icon-btn").on("click", on_copy)
