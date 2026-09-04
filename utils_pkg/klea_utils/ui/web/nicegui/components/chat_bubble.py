"""
Chat message bubble widget.

File: klea_utils/ui/web/nicegui/components/chat_bubble.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from nicegui import ui


class ChatBubble(ui.element):
    """A custom chat message bubble with built-in actions.

    Replaces ``ui.chat_message`` with full control over the layout.
    Each bubble has collapsible text content, a timestamp, a copy
    button, and an expand / collapse toggle -- all flowing naturally
    inside the bubble (no CSS hacks).

    Usage inside a ``@ui.refreshable``::

        ChatBubble(
            text="Hello", stamp="12:00", is_user=True,
            collapsed=False, idx=0,
            on_expand=lambda: print("toggle"),
            on_copy=lambda: print("copy"),
        )
    """

    def __init__(
        self,
        text: str,
        stamp: str,
        is_user: bool,
        collapsed: bool,
        idx: int,
        on_expand=None,
        on_copy=None,
    ) -> None:
        """Build the bubble.

        :param text: Message text (rendered as HTML).
        :param stamp: Timestamp string.
        :param is_user: ``True`` for user messages, ``False`` for bot.
        :param collapsed: ``True`` if the text is collapsed to 4 lines.
        :param idx: Message index (used for expand/collapse tracking).
        :param on_expand: Callable with no args, fired on expand/collapse click.
        :param on_copy: Callable with no args, fired on copy click.
        """
        super().__init__("div")

        bg = (
            "bg-green-50 dark:bg-green-900"
            if is_user
            else "bg-blue-50 dark:bg-blue-900"
        )
        corner = "rounded-br-sm" if is_user else "rounded-bl-sm"
        align = "items-end" if is_user else "items-start"
        text_align = "text-right" if is_user else "text-left"
        bubble_w = "max-w-[85%]" if is_user else "w-full"
        bottom_align = "justify-end" if is_user else "justify-start"

        self.classes(f"w-full flex flex-col {align}")

        with (
            self,
            ui.element("div").classes(
                f"flex flex-col rounded-2xl {bg} {corner} p-3 gap-1 {bubble_w}"
            ),
        ):
            text_cls = (
                f"{text_align} msg-collapsed"
                if collapsed
                else f"{text_align} msg-expanded"
            )
            with ui.element("div").classes(text_cls):
                # 'alerts' extra renders GitHub-style ``> [!WARNING]`` blocks
                # (used for the fallback / best-effort warnings) as callouts.
                ui.markdown(text, extras=["fenced-code-blocks", "tables", "alerts"])

            with ui.row().classes(f"flex flex-row {bottom_align} items-center gap-1"):
                ui.label(stamp).classes("text-xs text-grey-5")

                if on_copy:
                    ui.button(icon="content_copy").props("flat dense round size=sm").on(
                        "click", on_copy
                    )

                if on_expand:
                    icon = "expand_less" if not collapsed else "expand_more"
                    ui.button(icon=icon).props("flat dense round size=sm").on(
                        "click", on_expand
                    )
