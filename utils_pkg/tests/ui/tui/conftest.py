#!/usr/bin/env python3
"""
Shared fixtures for the shared-component Textual TUI tests.

The TUI is driven in-process with Textual's ``run_test()`` pilot.  A
:class:`FakeBackend` supplies a scriptable ``stream_events`` async generator
and a canned ``GET /commands`` catalogue, so no server and no model are
needed.  Tests build a :class:`~klea_utils.ui.tui.app.KleaTuiApp` via the
``make_app`` fixture and open it with ``app.run_test()``.

File: tests/ui/tui/conftest.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
from klea_utils.ui.tui.app import KleaTuiApp
from klea_utils.ui.tui.context import TuiContext

#: A scripted run: the events one ``stream_events`` call yields.
Script = list[dict[str, Any]]


class FakeBackend:
    """Canned in-process backend for the TUI's stream and command calls.

    Set :attr:`scripts` to the runs to yield (one list per ``stream_events``
    call; a default ``complete`` is used when exhausted) and :attr:`commands`
    to the server catalogue.  Every stream call is recorded in
    :attr:`calls` so a test can assert what the app sent.
    """

    def __init__(self) -> None:
        """Start with no scripts and an empty catalogue."""
        self.scripts: list[Script] = []
        self.commands: list[dict[str, Any]] = []
        self.calls: list[dict[str, Any]] = []
        #: The active chat's ``GET /chat/.../context`` body (for hydration).
        self.chat_context: dict[str, Any] = {}

    async def stream_events(
        self,
        query: str,
        chat_id: str,
        server_url: str,
        user_id: str = "",
        *,
        resume: bool = False,
        interrupt_response: dict[str, Any] | None = None,
        interrupt_id: str | None = None,
        interrupt_cancel: bool = False,
        **kwargs: Any,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Record the call and yield the next scripted run."""
        self.calls.append(
            {
                "query": query,
                "chat_id": chat_id,
                "resume": resume,
                "interrupt_response": interrupt_response,
                "interrupt_id": interrupt_id,
                "interrupt_cancel": interrupt_cancel,
            }
        )
        script = (
            self.scripts.pop(0)
            if self.scripts
            else [{"type": "complete", "message_for_user": "ok"}]
        )
        for event in script:
            yield event


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Patch the stream client and command fetch onto a :class:`FakeBackend`."""
    backend = FakeBackend()
    monkeypatch.setattr("klea_utils.api.sse.stream_events", backend.stream_events)

    async def _fetch_commands(server_url: str) -> list[dict[str, Any]]:
        return backend.commands

    monkeypatch.setattr("klea_utils.ui.client.fetch_commands", _fetch_commands)

    async def _fetch_chat_context(
        server_url: str, user_id: str, chat_id: str
    ) -> dict[str, Any]:
        return backend.chat_context

    monkeypatch.setattr("klea_utils.ui.client.fetch_chat_context", _fetch_chat_context)
    return backend


@pytest.fixture
def make_app():
    """Return a factory building a :class:`KleaTuiApp` for tests."""

    def _make(chat_id: str = "test-chat") -> KleaTuiApp:
        return KleaTuiApp(
            TuiContext(
                server_url="http://backend",
                title="Klea Test",
                chat_id=chat_id,
            )
        )

    return _make


@pytest.fixture(autouse=True)
def _isolate_chat_store():
    """Keep the process-global chat store from leaking between tests."""
    from klea_utils.ui.state import chats

    snapshot = dict(chats)
    yield
    chats.clear()
    chats.update(snapshot)
