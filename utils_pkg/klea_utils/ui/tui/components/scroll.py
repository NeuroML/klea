#!/usr/bin/env python3
"""
A scrollable container whose children can be replaced without losing place.

The status panel reconciles one section at a time, so a section's body swaps
its children on every update.  :class:`KleaScroll` captures the scroll offset
first, replaces the children, and then either follows the tail (when the reader
was already at the bottom) or restores the previous offset, so a section that
grows or shrinks above the viewport never yanks a scrolled-up view.

The widget also carries the status pane's focus gate.  It is not focusable by
default, so the arrow keys keep reaching the outer pane's own scroll; the owner
enables focus only while the body actually overflows and its section is
expanded, in which case the body scrolls internally.  The owner applies the
height budget (``styles.max_height``) and the internal overflow rule; this
widget only manages its children, its scroll position and its focus.

File: klea_utils/ui/tui/components/scroll.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Iterable

from textual.containers import VerticalScroll
from textual.widget import Widget

logger = logging.getLogger(__name__)

#: Slack, in lines, for the "the reader is already at the bottom" test.  A
#: sub-line remainder means the view is at the end even when ``scroll_y`` sits
#: a fraction below ``max_scroll_y``.
TAIL_SLACK = 1


class KleaScroll(VerticalScroll):
    """A scroll container that replaces its children in place.

    The children are mounted fresh on every :meth:`set_content`; the reader's
    position is preserved unless they were already at the bottom, in which case
    the view follows the tail -- the same rule the status panel applies to its
    section list.
    """

    def __init__(
        self,
        *children: Widget,
        id: str | None = None,
        classes: str | None = None,
        can_focus: bool = False,
    ) -> None:
        """Build the container.

        :param children: The initial children.
        :param id: Optional DOM id for the widget.
        :param classes: Optional CSS classes.
        :param can_focus: Whether the container starts focusable; the owner
            raises this only while its section is expanded and the body
            overflows, so the outer pane keeps the arrow keys otherwise.
        """
        super().__init__(*children, id=id, classes=classes, can_focus=can_focus)

    async def set_content(
        self, widgets: Iterable[Widget], *, focusable: bool = False
    ) -> None:
        """Replace the container's children, keeping the reader's place.

        The offset is captured before anything is removed, then the children
        are replaced wholesale.  The view follows the tail only when the reader
        was already at the bottom (within :data:`TAIL_SLACK` lines of
        ``max_scroll_y``); otherwise the previous offset is restored.

        :param widgets: The new children (any iterable of widgets).
        :param focusable: Whether the body may now take focus (true only when
            the section is expanded and the body overflows).
        """
        follow_tail = self.scroll_y >= self.max_scroll_y - TAIL_SLACK
        previous_y = self.scroll_y
        new_widgets = list(widgets)
        await self.remove_children()
        await self.mount_all(new_widgets)
        self.can_focus = focusable
        if follow_tail:
            self.scroll_end(animate=False)
        else:
            self.scroll_to(y=previous_y, animate=False)
        logger.debug(
            "KleaScroll %r updated: %d child(ren), follow_tail=%s, "
            "restored_y=%s, focusable=%s",
            self.id,
            len(new_widgets),
            follow_tail,
            previous_y,
            focusable,
        )

    def anchor(self, anchor: bool = True) -> None:
        """Pin the view to the bottom so appended content stays visible.

        :param anchor: Whether to anchor (the Textual default is ``True``).
        """
        logger.debug("KleaScroll %r anchor=%s", self.id, anchor)
        super().anchor(anchor)

    def release_anchor(self) -> None:
        """Let the view stay where the reader put it."""
        logger.debug("KleaScroll %r released", self.id)
        super().release_anchor()
