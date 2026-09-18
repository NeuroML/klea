"""
Chat message block widget.

File: klea_utils/ui/web/nicegui/components/chat_bubble.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from nicegui import ui

#: Recognised transcript roles; anything else falls back to ``agent``.
_ROLES = ("user", "agent", "tool")

#: MIME types rendered as a unified diff (``text/x-diff`` is the de-facto
#: type; ``text/x-patch`` is git's).  Rendered as a fenced block, which
#: markdown2/Pygments highlights with its diff lexer.
_DIFF_MIMES = ("text/x-diff", "text/x-patch")


def _is_code_mime(mime: str) -> bool:
    """Return whether *mime* is rendered by ``ui.code`` (not markdown)."""
    return mime in _DIFF_MIMES or mime.startswith("text/x-")


def _render_body(mime: str, data: str, text: str) -> None:
    """Render a block body according to its MIME type.

    Unknown/empty mime falls back to the preformatted ``text`` (markdown).
    Diffs and code use NiceGUI's native ``ui.code`` (syntax highlighting and
    a copy button); text uses ``ui.markdown``.
    """
    if mime in _DIFF_MIMES:
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
    an optional header, collapsible text content, a timestamp, a copy
    button, and an expand / collapse toggle -- all flowing naturally inside
    the block (no CSS hacks).

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
        on_expand=None,
        on_copy=None,
    ) -> None:
        """Build the block.

        :param text: Message text (markdown); also the fallback body.
        :param stamp: Timestamp string.
        :param role: ``"user"``, ``"agent"`` or ``"tool"``; unknown values
            fall back to ``"agent"``.
        :param collapsed: ``True`` if the text is collapsed to 4 lines.
        :param idx: Message index (used for expand/collapse tracking).
        :param header: Optional small bold header, e.g. a tool block's
            name and change counts.
        :param mime: MIME type of ``data``; selects the renderer (``text/x-diff``
            -> coloured diff, ``text/x-<lang>`` -> code, media deferred).
        :param data: The renderable payload (text or base64).
        :param meta: Extra entry metadata (path, language, ...); reserved for
            renderers that need it.
        :param on_expand: Callable with no args, fired on expand/collapse click.
        :param on_copy: Callable with no args, fired on copy click.
        """
        super().__init__("div")

        if role not in _ROLES:
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
                ui.label(header).classes("text-xs font-bold text-grey-6")

            text_cls = "msg-collapsed" if collapsed else "msg-expanded"
            # ``ui.markdown`` bodies get the chat markdown styling; ``ui.code``
            # bodies render their own box, so adding ``chat-markdown`` there
            # would stack our ``pre``/``code`` rules on top of it.
            classes = f"text-left {text_cls}"
            if not _is_code_mime(mime):
                classes += " chat-markdown"
            with ui.element("div").classes(classes):
                _render_body(mime, data, text)

            with ui.row().classes("flex flex-row justify-start items-center gap-1"):
                ui.label(stamp).classes("text-xs text-grey-5")

                if on_copy:
                    ui.button(icon="content_copy").props(
                        "flat dense round size=sm"
                    ).classes("icon-btn").on("click", on_copy)

                if on_expand:
                    icon = "expand_less" if not collapsed else "expand_more"
                    ui.button(icon=icon).props("flat dense round size=sm").classes(
                        "icon-btn"
                    ).on("click", on_expand)
