#!/usr/bin/env python3
"""
Concurrency tests for the agent page: several chats run at once.

Uses the in-process user simulation with a gated ``stream_events`` stand-in,
so a run stays 'streaming' until the test releases it.  This is the UI-level
companion to ``utils_pkg/tests/test_web_concurrency.py`` (which drives
``run_stream`` directly).

File: tests/ui/web/test_concurrency.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio

from nicegui import ui


class GatedStream:
    """``stream_events`` stand-in holding each chat's run open.

    Each chat gets its own queue; a run consumes queued events and then waits,
    so it stays in ``progress`` until the test pushes more (or ``None``).
    """

    def __init__(self) -> None:
        """Start with no per-chat queues."""
        self.queues: dict[str, asyncio.Queue] = {}
        self.calls: list[str] = []

    def __call__(self, query, chat_id, server_url, **kwargs):
        """Return the per-chat generator (HTTP arguments are ignored)."""
        self.calls.append(chat_id)
        queue = self.queues.setdefault(chat_id, asyncio.Queue())
        return self._gen(queue)

    async def _gen(self, queue: asyncio.Queue):
        """Yield queued events until a ``None`` sentinel ends the run."""
        while True:
            event = await queue.get()
            if event is None:
                return
            yield event


def _send(user, text: str) -> None:
    """Type *text* into the chat input and press Enter."""
    user.find(ui.textarea).type(text)
    user.find(ui.textarea).trigger("keydown.enter.exact.prevent")


async def _settle() -> None:
    """Let scheduled background runs start processing."""
    await asyncio.sleep(0.05)


async def test_send_is_allowed_in_a_second_chat(agent_user, monkeypatch):
    """While one chat streams, another can start; the streaming one cannot."""
    import klea_utils.ui.web.nicegui.components.stream as stream_mod

    streams = GatedStream()
    monkeypatch.setattr(stream_mod, "stream_events", streams)

    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")

    # First chat: the run is gated (stays streaming).
    _send(agent_user, "first message")
    await _settle()
    await agent_user.should_see("first message")
    assert streams.calls, "the gated run did not start"

    # Start a second chat and send there: per-chat gating allows it even though
    # the first chat is still streaming.
    agent_user.find(marker="new-chat").click()
    _send(agent_user, "second message")
    await _settle()
    await agent_user.should_see("second message")

    # The active chat is now streaming, so a further send is ignored (the text
    # stays in the input box; clear it so the assertion only sees the
    # transcript).
    _send(agent_user, "ignored while streaming")
    await _settle()
    agent_user.find(ui.textarea).clear()
    await agent_user.should_not_see("ignored while streaming")

    # Release the gates so the runs finish cleanly.
    for queue in streams.queues.values():
        queue.put_nowait(None)
