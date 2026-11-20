#!/usr/bin/env python3
"""
Regression tests for the chat area's mounted-message window.

The web chat area re-renders its scroll area on every stream event
(``render_chat_area`` -> ``_render_messages``), so a long session used to
mount one ``ChatBubble`` per message.  ``_render_messages`` now mounts only
the newest ``chat_area.MOUNTED_MESSAGE_LIMIT`` messages, mirroring the TUI
transcript's mounted window (``Transcript.MAX_MOUNTED``).

These tests exercise that bound directly: they call the renderer with the
bubble widget swapped for a recorder, so no browser or live backend is
needed.  They would fail against the pre-change code, which iterated the
whole message list and mounted every message.

File: utils_pkg/tests/ui/web/test_chat_area_windowing.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from typing import Any, Self, cast

from klea_utils.ui.state import ChatData, MessageData, chats, ensure_chat
from klea_utils.ui.web.nicegui.components import chat_area
from klea_utils.ui.web.nicegui.components.context import PageContext


class _FakeContainer:
    """Stand-in for a NiceGUI container: clearable and usable as a slot."""

    def __init__(self) -> None:
        self.children: list[Any] = []

    def clear(self) -> None:
        self.children.clear()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class _FakeContext:
    """Minimal ``PageContext`` carrying just what ``_render_messages`` reads."""

    def __init__(self, *, user_id: str, chat_id: str) -> None:
        self.user_id = user_id
        self.chat_id = chat_id
        self.chat_area = _FakeContainer()
        self.turn_status_container = _FakeContainer()
        self.interrupt_container = _FakeContainer()
        self.last_rendered_chat = ""
        self.rendered_message_counts: dict[str, int] = {}
        self.scroll_count = 0

    def scroll_chat_bottom(self) -> None:
        """Record scroll requests instead of driving Quasar."""
        self.scroll_count += 1


def _message(index: int) -> MessageData:
    """Build a user-role message whose rendered text is ``message <n>``.

    A ``user`` role is rendered verbatim (no markdown/linkify), so the text
    recorded by the fake bubble equals the value asserted below.
    """
    return {
        "text": f"message {index}",
        "stamp": "",
        "role": "user",
        "header": "",
    }


def _install_bubble_recorder(monkeypatch, created: list[dict[str, Any]]) -> None:
    """Swap ``chat_area.ChatBubble`` for a recorder of its kwargs."""

    class _RecordingBubble:
        def __init__(self, **kwargs: Any) -> None:
            created.append(kwargs)

    monkeypatch.setattr(chat_area, "ChatBubble", _RecordingBubble)


def _setup_chat(user_id: str, chat_id: str, messages: list[MessageData]) -> ChatData:
    """Create the in-memory chat that ``_render_messages`` will render."""
    chat = ensure_chat(user_id, chat_id)
    # An explicit idle turn status keeps ``render_turn_status`` on its
    # clear-and-return path, so the fake containers are enough.
    chat["turn_status"] = {"kind": "idle"}
    chat["messages"] = messages
    return chat


def test_live_append_stays_within_mounted_bound(monkeypatch) -> None:
    """A live turn past the bound never mounts more than the bound.

    Mirrors the live path: each new message is appended to the chat store and
    ``render_chat_area`` re-renders (``input_area`` then ``stream``).  The
    mounted bubble count must stay at or below ``MOUNTED_MESSAGE_LIMIT`` while
    the newest message stays mounted.
    """
    limit = chat_area.MOUNTED_MESSAGE_LIMIT
    user_id = "window-test-user"
    chat_id = "live-turn"
    created: list[dict[str, Any]] = []
    _install_bubble_recorder(monkeypatch, created)

    total = limit + 40
    # Start from an empty transcript and append one message per iteration, as
    # a live turn does, so each render sees exactly ``index + 1`` messages
    # rather than the whole pre-filled list on every pass.
    chat = _setup_chat(user_id, chat_id, [])
    ctx = _FakeContext(user_id=user_id, chat_id=chat_id)
    try:
        # Start well below the bound, then append past it one message at a
        # time, re-rendering after each append exactly as a live turn does.
        for index in range(total):
            chat["messages"].append(_message(index))
            created.clear()
            chat_area._render_messages(cast(PageContext, ctx))

            mounted = len(created)
            assert mounted <= limit, (
                f"mounted {mounted} bubbles after message {index}, "
                f"expected at most {limit}"
            )
            assert mounted == min(index + 1, limit)
            # The newest message is always mounted, at its absolute index.
            assert created[-1]["idx"] == index
            assert created[-1]["text"] == f"message {index}"
            # Indices stay contiguous and absolute into the full store list,
            # so expand/collapse keeps addressing the right message.
            start = max(0, index + 1 - limit)
            assert [bubble["idx"] for bubble in created] == list(
                range(start, index + 1)
            )

        # The chat store keeps the full history; only the mounted set is
        # trimmed, so export and hydration still see every message.
        assert len(chat["messages"]) == total
    finally:
        chats.pop(f"{user_id}:{chat_id}", None)


def test_short_chat_mounts_every_message(monkeypatch) -> None:
    """A transcript below the bound is rendered in full (no truncation)."""
    limit = chat_area.MOUNTED_MESSAGE_LIMIT
    user_id = "window-test-user"
    chat_id = "short-chat"
    created: list[dict[str, Any]] = []
    _install_bubble_recorder(monkeypatch, created)

    total = max(1, limit - 10)
    _setup_chat(user_id, chat_id, [_message(i) for i in range(total)])
    ctx = _FakeContext(user_id=user_id, chat_id=chat_id)
    try:
        chat_area._render_messages(cast(PageContext, ctx))

        assert len(created) == total
        assert [bubble["idx"] for bubble in created] == list(range(total))
        assert created[0]["text"] == "message 0"
        assert created[-1]["text"] == f"message {total - 1}"
    finally:
        chats.pop(f"{user_id}:{chat_id}", None)
