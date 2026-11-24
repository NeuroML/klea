"""
Expand/collapse control visibility in web chat blocks.

File: utils_pkg/tests/ui/web/test_chat_bubble_expand.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from klea_utils.ui.glyphs import UNICODE_GLYPHS
from klea_utils.ui.web.nicegui.components.chat_area import _apply_bubble_collapsed
from klea_utils.ui.web.nicegui.components.chat_bubble import ChatBubble
from nicegui import ui
from nicegui.testing import User

pytestmark = pytest.mark.asyncio

#: Labels the chat block uses for its labelled expand/collapse control.  The
#: collapsed label carries the truncation glyph from the shared table, so the
#: control also marks the body as clipped; the expanded label drops it.
SHOW_MORE = f"{UNICODE_GLYPHS.overflow} Show more"
SHOW_LESS = "Show less"


def _make_bubble(
    text: str,
    *,
    overflowing: bool,
    on_expand: Callable[[], None],
) -> ChatBubble:
    """Build one agent block, as ``chat_area`` does after the overflow gate."""
    return ChatBubble(
        text=text,
        stamp="12:00",
        role="agent",
        collapsed=True,
        idx=0,
        on_expand=on_expand,
        overflowing=overflowing,
    )


async def test_short_content_has_no_expand_control(utils_user: User) -> None:
    """A body that fits its collapsed height renders no control at all."""

    @ui.page("/short-content")
    def page() -> None:
        _make_bubble("short message", overflowing=False, on_expand=lambda: None)

    await utils_user.open("/short-content")
    await utils_user.should_not_see(SHOW_MORE)
    await utils_user.should_not_see(SHOW_LESS)
    await utils_user.should_not_see(UNICODE_GLYPHS.overflow)


async def test_long_content_has_labelled_control_that_flips_wording(
    utils_user: User,
) -> None:
    """An overflowing body renders the control and retitles it on toggle."""
    holder: dict[str, Any] = {}

    @ui.page("/long-content")
    def page() -> None:
        holder["bubble"] = _make_bubble(
            "line\n" * 10, overflowing=True, on_expand=lambda: None
        )

    await utils_user.open("/long-content")
    await utils_user.should_see(SHOW_MORE)
    await utils_user.should_see(UNICODE_GLYPHS.overflow)

    # The toggle retitles the control in place, without rebuilding the block.
    _apply_bubble_collapsed(holder["bubble"], "agent", False)
    await utils_user.should_see(SHOW_LESS)
    await utils_user.should_not_see(UNICODE_GLYPHS.overflow)
