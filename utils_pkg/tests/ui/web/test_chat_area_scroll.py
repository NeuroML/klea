"""
Regression tests for the web transcript's scroll behaviour.

Covers two fixes in ``chat_area.py``:

* expanding a bubble toggles it in place instead of rebuilding the
  transcript (a rebuild cleared the content and reset the reader's scroll
  position), and
* the transcript pages older/newer messages on scroll-to-top / scroll-to-
  bottom while keeping the mounted window bounded and the absolute indices
  contiguous.

File: utils_pkg/tests/ui/web/test_chat_area_scroll.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from collections.abc import Callable
from types import SimpleNamespace
from typing import ClassVar, Self, cast

import pytest
from klea_utils.ui.web.nicegui.components import chat_area as ca
from klea_utils.ui.web.nicegui.components.context import PageContext


class FakeScroll:
    """Minimal stand-in for ``ui.scroll_area`` (tracks the scroll offset)."""

    def __init__(self) -> None:
        self.offset = 0.0

    def scroll_to(self, pixels: float = 0, **_kwargs: object) -> None:
        self.offset = float(pixels)


class FakeContainer:
    """Minimal stand-in for the chat content column (supports ``with``)."""

    def __init__(self, scroll: FakeScroll) -> None:
        self.scroll = scroll
        self.clear_count = 0

    def clear(self) -> None:
        # Clearing the content collapses the scrollable extent, which resets
        # the browser's scroll offset; model that so a rebuild is detectable.
        self.clear_count += 1
        self.scroll.offset = 0.0

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class FakeBubble:
    """Records each ``ChatBubble`` construction and its callbacks."""

    instances: ClassVar[list[FakeBubble]] = []

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.default_slot = SimpleNamespace(children=[])
        FakeBubble.instances.append(self)

    def classes(self, *_args: object, **_kwargs: object) -> FakeBubble:
        return self


class FakeContext:
    """Minimal ``PageContext`` for the chat-area renderer."""

    def __init__(self, user_id: str = "u", chat_id: str = "c") -> None:
        self.user_id = user_id
        self.chat_id = chat_id
        self.scroll_area = FakeScroll()
        self.chat_area = FakeContainer(self.scroll_area)
        self.last_rendered_chat = ""
        self.rendered_message_counts: dict[str, int] = {}
        self.mounted_start_by_chat: dict[str, int] = {}
        self.loading_older = False
        self.loading_newer = False
        self.scroll_paging_until = 0.0
        self.render_chat_area = lambda: ca._render_messages(cast(PageContext, self))
        self.scroll_chat_bottom = lambda: ca._scroll_to_bottom(cast(PageContext, self))


@pytest.fixture
def wired(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """Replace the chat area's collaborators with the fakes above."""
    FakeBubble.instances = []
    monkeypatch.setattr(ca, "ChatBubble", FakeBubble)
    monkeypatch.setattr(ca, "linkify_md", lambda text: text)
    monkeypatch.setattr(ca, "render_turn_status", lambda *args, **kwargs: None)
    return monkeypatch


def _store(
    monkeypatch: pytest.MonkeyPatch,
    messages: list[dict],
    key: str = "u:c",
) -> dict:
    """Install a one-chat store into the chat-area module."""
    data = {key: {"messages": messages, "expanded": set()}}
    monkeypatch.setattr(ca, "chats", data)
    return data


def test_expand_toggles_in_place_without_resetting_scroll(
    wired: pytest.MonkeyPatch,
) -> None:
    """Expanding a bubble must not rebuild the transcript (and jump the view)."""
    data = _store(
        wired,
        [
            {"text": "m0", "role": "agent", "stamp": "0"},
            {"text": "m1", "role": "agent", "stamp": "1"},
        ],
    )
    ctx = FakeContext()
    ctx.render_chat_area()

    # Simulate the reader having scrolled to a mid-transcript position.
    ctx.scroll_area.offset = 500.0
    clear_before = ctx.chat_area.clear_count

    # Expand the second bubble through the callback the renderer wired up.
    on_expand = cast(Callable[[], None], FakeBubble.instances[1].kwargs["on_expand"])
    on_expand()

    assert 1 in data["u:c"]["expanded"]
    # The expand must not rebuild the transcript: no clear (which would reset
    # the offset), so the reader's scroll position is exactly preserved.
    assert ctx.chat_area.clear_count == clear_before
    assert ctx.scroll_area.offset == 500.0

    # Collapsing again is likewise in place.
    on_expand()
    assert 1 not in data["u:c"]["expanded"]
    assert ctx.scroll_area.offset == 500.0


async def test_scroll_paging_mounts_older_then_trims(
    wired: pytest.MonkeyPatch,
) -> None:
    """Scroll-to-top mounts older messages; scroll-to-bottom trims them back."""

    async def _no_offset(_idx: int) -> None:
        return None

    async def _no_restore(_idx: int, _offset: float) -> None:
        return None

    wired.setattr(ca, "_measure_anchor_offset", _no_offset)
    wired.setattr(ca, "_restore_anchor_offset", _no_restore)

    total = ca.MOUNTED_MESSAGE_LIMIT + 40
    messages = [
        {"text": f"m{i}", "role": "agent", "stamp": str(i)} for i in range(total)
    ]
    _store(wired, messages)
    ctx = FakeContext()
    ctx.render_chat_area()

    latest_start = total - ca.MOUNTED_MESSAGE_LIMIT

    def mounted() -> list[int]:
        return [cast(int, b.kwargs["idx"]) for b in FakeBubble.instances]

    initial = mounted()
    assert initial == list(range(latest_start, total))
    assert len(initial) <= ca.MOUNTED_MESSAGE_LIMIT

    # Scroll to the top: the previous batch of older messages is mounted,
    # contiguous, still bounded, and the newest end is trimmed.
    FakeBubble.instances.clear()
    await ca._page_older(cast(PageContext, ctx))
    older = mounted()
    assert older[0] == latest_start - ca.SCROLL_BATCH
    assert older == list(range(older[0], older[0] + len(older)))
    assert len(older) <= ca.MOUNTED_MESSAGE_LIMIT
    assert older[-1] < total - 1

    # Scroll back to the bottom: the newer messages are re-mounted and the
    # newest message is mounted again, still contiguous and bounded.
    ctx.scroll_paging_until = 0.0
    FakeBubble.instances.clear()
    await ca._page_newer(cast(PageContext, ctx))
    newer = mounted()
    assert newer[-1] == total - 1
    assert newer == list(range(newer[0], newer[-1] + 1))
    assert len(newer) <= ca.MOUNTED_MESSAGE_LIMIT
