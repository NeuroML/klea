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
        #: Stored UI events returned by ``GET .../events`` (pane hydration).
        self.chat_events: list[dict[str, Any]] = []
        #: ``GET /chat/{user_id}`` rows, most recently updated first.
        self.chats: list[dict[str, Any]] = []
        #: ``GET /chat/{user_id}/{chat_id}/messages`` rows, keyed by chat id.
        self.messages: dict[str, list[dict[str, Any]]] = {}
        #: Recorded ``rename_chat_on_server`` calls.
        self.renames: list[dict[str, Any]] = []
        #: Recorded ``delete_chat_on_server`` chat ids.
        self.deletes: list[str] = []
        #: Recorded ``create_chat_on_server`` chat ids.
        self.created: list[str] = []
        #: Active model config per role (``GET .../models/active``).
        self.model_info: dict[str, dict[str, Any]] = {}
        #: Recorded ``set_model_override`` calls (chat scope).
        self.model_overrides: list[dict[str, Any]] = []
        #: Recorded ``clear_model_override`` role ids (chat scope).
        self.model_clears: list[str] = []
        #: Catalogue providers and per-provider models.
        self.catalogue_providers: list[str] = []
        self.catalogue_models: dict[str, list[str]] = {}
        #: Stored credentials (``GET /credentials``).
        self.credentials: list[dict[str, Any]] = []
        #: Recorded ``set_credential`` calls.
        self.credentials_set: list[dict[str, Any]] = []
        #: Recorded ``clear_credential`` calls.
        self.credentials_cleared: list[dict[str, Any]] = []

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

    async def _fetch_chat_events(
        server_url: str, user_id: str, chat_id: str
    ) -> list[dict[str, Any]]:
        return backend.chat_events

    monkeypatch.setattr("klea_utils.ui.client.fetch_chat_events", _fetch_chat_events)

    async def _fetch_chats(server_url: str, user_id: str) -> list[dict[str, Any]]:
        return backend.chats

    monkeypatch.setattr("klea_utils.ui.client.fetch_chats", _fetch_chats)

    async def _fetch_chat_messages(
        server_url: str, user_id: str, chat_id: str
    ) -> list[dict[str, Any]]:
        return backend.messages.get(chat_id, [])

    monkeypatch.setattr(
        "klea_utils.ui.client.fetch_chat_messages", _fetch_chat_messages
    )

    async def _rename_chat_on_server(
        server_url: str, user_id: str, chat_id: str, title: str
    ) -> None:
        backend.renames.append({"chat_id": chat_id, "title": title})

    monkeypatch.setattr(
        "klea_utils.ui.client.rename_chat_on_server", _rename_chat_on_server
    )

    async def _delete_chat_on_server(
        server_url: str, user_id: str, chat_id: str
    ) -> None:
        backend.deletes.append(chat_id)
        backend.chats = [c for c in backend.chats if c.get("chat_id") != chat_id]

    monkeypatch.setattr(
        "klea_utils.ui.client.delete_chat_on_server", _delete_chat_on_server
    )

    async def _create_chat_on_server(
        server_url: str, user_id: str, chat_id: str
    ) -> None:
        backend.created.append(chat_id)

    monkeypatch.setattr(
        "klea_utils.ui.client.create_chat_on_server", _create_chat_on_server
    )

    async def _fetch_active_models(
        server_url: str, user_id: str, chat_id: str
    ) -> dict[str, dict[str, Any]]:
        return backend.model_info

    monkeypatch.setattr("klea_utils.api.sse.fetch_active_models", _fetch_active_models)

    async def _fetch_credentials(server_url: str, user_id: str) -> list[dict[str, Any]]:
        return backend.credentials

    monkeypatch.setattr("klea_utils.api.sse.fetch_credentials", _fetch_credentials)
    # The dialogs bind the fetchers at import time, so patch their module
    # attributes too (patching ``api.sse`` alone would not rebind them).
    monkeypatch.setattr(
        "klea_utils.ui.tui.components.model_dialog.fetch_active_models",
        _fetch_active_models,
    )
    monkeypatch.setattr(
        "klea_utils.ui.tui.components.model_dialog.fetch_credentials",
        _fetch_credentials,
    )
    monkeypatch.setattr(
        "klea_utils.ui.tui.components.credentials_dialog.fetch_credentials",
        _fetch_credentials,
    )

    async def _fetch_catalogue_providers(server_url: str, user_id: str) -> list[str]:
        return backend.catalogue_providers

    monkeypatch.setattr(
        "klea_utils.ui.model_config.fetch_catalogue_providers",
        _fetch_catalogue_providers,
    )

    async def _fetch_catalogue_models(
        server_url: str, user_id: str, provider: str
    ) -> list[str]:
        return backend.catalogue_models.get(provider, [])

    monkeypatch.setattr(
        "klea_utils.ui.model_config.fetch_catalogue_models", _fetch_catalogue_models
    )

    async def _set_model_override(
        server_url: str, user_id: str, chat_id: str, role: str, payload: dict
    ) -> bool:
        backend.model_overrides.append(
            {"chat_id": chat_id, "role": role, "payload": payload}
        )
        return True

    monkeypatch.setattr(
        "klea_utils.ui.model_config.set_model_override", _set_model_override
    )

    async def _clear_model_override(
        server_url: str, user_id: str, chat_id: str, role: str
    ) -> bool:
        backend.model_clears.append(role)
        return True

    monkeypatch.setattr(
        "klea_utils.ui.model_config.clear_model_override", _clear_model_override
    )

    async def _set_credential(
        server_url: str,
        user_id: str,
        provider: str,
        secret: str,
        endpoint: str = "",
    ) -> bool:
        backend.credentials_set.append(
            {"provider": provider, "secret": secret, "endpoint": endpoint}
        )
        return True

    monkeypatch.setattr("klea_utils.ui.client.set_credential", _set_credential)
    monkeypatch.setattr(
        "klea_utils.ui.tui.components.credentials_dialog.set_credential",
        _set_credential,
    )

    async def _clear_credential(
        server_url: str, user_id: str, provider: str, endpoint: str = ""
    ) -> bool:
        backend.credentials_cleared.append({"provider": provider, "endpoint": endpoint})
        return True

    monkeypatch.setattr("klea_utils.ui.client.clear_credential", _clear_credential)
    monkeypatch.setattr(
        "klea_utils.ui.tui.components.credentials_dialog.clear_credential",
        _clear_credential,
    )
    return backend


@pytest.fixture
def make_app():
    """Return a factory building a :class:`KleaTuiApp` for tests."""

    def _make(
        chat_id: str = "test-chat",
        user_id: str = "",
        resume_chat_id: str = "",
        continue_session: bool = False,
        status_extras: list[Any] | None = None,
    ) -> KleaTuiApp:
        return KleaTuiApp(
            TuiContext(
                server_url="http://backend",
                title="Klea Test",
                chat_id=chat_id,
                user_id=user_id,
                resume_chat_id=resume_chat_id,
                continue_session=continue_session,
                status_extras=list(status_extras or []),
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
