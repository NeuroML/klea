#!/usr/bin/env python3
"""
Concurrency tests for the per-chat run registry and stream isolation.

These drive ``run_stream`` directly with a queue-backed ``stream_events``
stand-in, so several runs can be held open and their events released
deterministically.  No browser or server is involved: a bare ``PageContext``
has no-op render callbacks, and ``apply_stream_event`` only mutates the
per-chat data.

File: tests/test_web_concurrency.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio

import pytest
from klea_utils.ui.web.nicegui.components import stream
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.state import chats


class QueueStream:
    """``stream_events`` stand-in: one asyncio queue of events per chat."""

    def __init__(self) -> None:
        """Start with no per-chat queues."""
        self.queues: dict[str, asyncio.Queue] = {}

    def queue(self, chat_id: str) -> asyncio.Queue:
        """Return (creating if needed) the event queue for *chat_id*."""
        return self.queues.setdefault(chat_id, asyncio.Queue())

    def __call__(self, query, chat_id, server_url, **kwargs):
        """Return the per-chat event generator (ignores the HTTP arguments)."""
        return self._gen(self.queue(chat_id))

    async def _gen(self, queue: asyncio.Queue):
        """Yield events until a ``None`` sentinel ends the run."""
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event


class FakeLabel:
    """Records the text written to the live turn-status label."""

    def __init__(self) -> None:
        """Start with no recorded writes."""
        self.texts: list[str] = []

    def set_text(self, text: str) -> None:
        """Record *text*."""
        self.texts.append(text)


@pytest.fixture(autouse=True)
def _clean_chats():
    """Clear the process-global chat store around each test."""
    chats.clear()
    yield
    chats.clear()


async def _settle() -> None:
    """Let scheduled run tasks process their queued events."""
    await asyncio.sleep(0.02)


async def test_two_chats_run_concurrently(monkeypatch):
    """Two chats run at once; finishing one leaves the other alone."""
    streams = QueueStream()
    monkeypatch.setattr(stream, "stream_events", streams)
    ctx = PageContext(server_url="http://x", user_id="u")

    task_a = asyncio.create_task(stream.run_stream(ctx, "qA", "A"))
    task_b = asyncio.create_task(stream.run_stream(ctx, "qB", "B"))
    await _settle()

    assert ctx.chat_is_streaming("A")
    assert ctx.chat_is_streaming("B")
    assert chats["u:A"]["turn_status"]["kind"] == "progress"
    assert chats["u:B"]["turn_status"]["kind"] == "progress"

    # Completing A leaves B running.
    await streams.queue("A").put({"type": "complete", "message_for_user": "A done"})
    await _settle()
    assert not ctx.chat_is_streaming("A")
    assert ctx.chat_is_streaming("B")
    assert chats["u:A"]["messages"][-1]["text"] == "A done"

    # Cancelling B (Stop) does not touch A.
    task_b.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_b
    assert not ctx.chat_is_streaming("B")
    assert chats["u:B"]["turn_status"]["kind"] == "stopped"

    await task_a


async def test_background_progress_does_not_touch_the_active_label(monkeypatch):
    """A background run updates its data but not the visible turn-status label."""
    streams = QueueStream()
    monkeypatch.setattr(stream, "stream_events", streams)
    ctx = PageContext(server_url="http://x", user_id="u")
    label = FakeLabel()
    ctx.turn_status_label = label

    task_a = asyncio.create_task(stream.run_stream(ctx, "qA", "A"))
    await _settle()
    ctx.chat_id = "B"  # A is now a background chat

    await streams.queue("A").put({"type": "progress", "data": {"heading": "A work"}})
    await _settle()
    assert chats["u:A"]["turn_status"]["heading"] == "A work"
    assert label.texts == []

    # Switching to A lets its progress render.
    ctx.chat_id = "A"
    await streams.queue("A").put({"type": "progress", "data": {"heading": "A work 2"}})
    await _settle()
    assert label.texts == ["A work 2"]

    task_a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task_a


async def test_retry_callback_survives_a_failed_run(monkeypatch):
    """A resumable error clears the run entry but keeps the Retry callback."""
    streams = QueueStream()
    monkeypatch.setattr(stream, "stream_events", streams)
    ctx = PageContext(server_url="http://x", user_id="u")

    task_a = asyncio.create_task(stream.run_stream(ctx, "qA", "A"))
    await _settle()
    await streams.queue("A").put(
        {"type": "error", "message": "boom", "resumable": True}
    )
    await _settle()

    assert not ctx.chat_is_streaming("A")
    assert "u:A" in ctx.retry_cbs
    assert chats["u:A"]["turn_status"]["kind"] == "error"
    await task_a
