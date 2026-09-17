"""
Chat message block widget.

File: klea_utils/ui/web/nicegui/components/chat_bubble.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from nicegui import ui

#: Recognised transcript roles; anything else falls back to ``agent``.
_ROLES = ("user", "agent", "tool")


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
        on_expand=None,
        on_copy=None,
    ) -> None:
        """Build the block.

        :param text: Message text (rendered as markdown).
        :param stamp: Timestamp string.
        :param role: ``"user"``, ``"agent"`` or ``"tool"``; unknown values
            fall back to ``"agent"``.
        :param collapsed: ``True`` if the text is collapsed to 4 lines.
        :param idx: Message index (used for expand/collapse tracking).
        :param header: Optional small bold header, e.g. a tool block's
            name and change counts.
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
            with ui.element("div").classes(f"text-left {text_cls} chat-markdown"):
                # 'alerts' extra renders GitHub-style ``> [!WARNING]`` blocks
                # (used for the fallback / best-effort warnings) as callouts.
                ui.markdown(text, extras=["fenced-code-blocks", "tables", "alerts"])

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
