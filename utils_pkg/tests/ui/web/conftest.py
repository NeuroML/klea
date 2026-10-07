#!/usr/bin/env python3
"""
Shared fixtures for the shared-component NiceGUI tests.

Builds a minimal page from the reusable
``klea_utils.ui.web.nicegui.components`` and drives it in-process with
NiceGUI's user simulation.  A :class:`FakeBackend` supplies canned
responses for every backend call, so no server and no model are needed.

File: tests/ui/web/conftest.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

import httpx
import pytest


class FakeBackend:
    """Canned in-process backend for the frontend's HTTP calls.

    Tests set the response fields (``chats``, ``session_models``, ...) and
    the scripted ``stream_events``; every request is recorded in
    :attr:`requests` so a test can assert what the UI sent.
    """

    def __init__(self) -> None:
        """Start with empty responses and no scripted stream events."""
        self.chats: list[dict] = []
        self.messages: dict[str, list[dict]] = {}
        self.context: dict[str, dict] = {}
        self.session_models: dict = {}
        self.chat_models: dict = {}
        self.credentials: list[dict] = []
        self.catalogue: dict = {"providers": [], "models": []}
        #: Events returned by ``POST /query/stream`` as an SSE body.
        self.stream_events: list[dict] = []
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        """Return a canned response for *request*."""
        self.requests.append(request)
        parts = request.url.path.strip("/").split("/")
        method = request.method
        if parts == ["health", "ready"]:
            return httpx.Response(200, json={"status": "ok"})
        if parts == ["query", "stream"] and method == "POST":
            body = "".join(
                f"data: {json.dumps(event)}\n\n" for event in self.stream_events
            )
            return httpx.Response(
                200, text=body, headers={"content-type": "text/event-stream"}
            )
        if parts == ["query", "cancel"] and method == "POST":
            return httpx.Response(204)
        if parts and parts[0] == "credentials":
            return httpx.Response(200, json=self.credentials)
        if parts and parts[0] == "chat":
            return self._chat_response(parts, method)
        return httpx.Response(200, json={})

    def _chat_response(self, parts: list[str], method: str) -> httpx.Response:
        """Resolve ``/chat/...`` read endpoints from the configured state."""
        # ["chat", <user>]
        if len(parts) == 2 and method == "GET":
            return httpx.Response(200, json=self.chats)
        # ["chat", <user>, "models", ...]
        if parts[2:3] == ["models"]:
            if parts[3:4] == ["active"]:
                return httpx.Response(200, json=self.session_models)
            if parts[3:4] == ["catalogue"]:
                return httpx.Response(200, json=self.catalogue)
        # ["chat", <user>, <chat>, ...]
        if len(parts) >= 4 and parts[3:4] == ["messages"] and method == "GET":
            return httpx.Response(200, json=self.messages.get(parts[2], []))
        if len(parts) >= 4 and parts[3:4] == ["context"] and method == "GET":
            return httpx.Response(
                200,
                json=self.context.get(
                    parts[2], {"context": None, "pending_interrupt": None}
                ),
            )
        if len(parts) >= 5 and parts[3:5] == ["models", "active"]:
            return httpx.Response(200, json=self.chat_models)
        return httpx.Response(200, json={})


@pytest.fixture
def fake_backend(monkeypatch: pytest.MonkeyPatch) -> FakeBackend:
    """Route all frontend HTTP calls through a :class:`FakeBackend`."""
    backend = FakeBackend()
    real_client = httpx.AsyncClient

    def _factory(*args, **kwargs):
        kwargs.setdefault("transport", httpx.MockTransport(backend.handler))
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return backend


@pytest.fixture
async def utils_user(fake_backend: FakeBackend):
    """A simulated user on a page composing the shared components."""
    pytest.importorskip("nicegui")

    from klea_utils.ui.web.nicegui.components import (
        chat_area,
        chat_list,
        footer,
        header,
        initial_load,
        input_area,
        inspector,
        model_dialog,
        status_pane,
        theme,
    )
    from klea_utils.ui.web.nicegui.components.context import PageContext
    from klea_utils.ui.web.nicegui.state import chats
    from nicegui import ui
    from nicegui.testing.user_simulation import user_simulation

    def _build_page() -> None:
        """Compose the shared components, mirroring an app's page layout."""
        ctx = PageContext(
            chat_id="",
            server_url="http://backend",
            user_id="u",
            title="Klea Test",
        )
        theme.install_theme(ctx)
        header.attach_header(ctx)
        chat_list.attach_chat_list(ctx)
        model_dialog.attach_model_info(ctx)
        status_pane.attach_status_pane(ctx)
        with ui.column().classes("w-full"):
            with ui.row() as ctx.loading_row:
                ui.spinner(type="dots")
                ui.label("Backend is starting, please wait...")
            with ui.tabs() as center_tabs:
                chat_tab = ui.tab(name="chat", label="chat")
                inspect_tab = ui.tab(name="inspect", label="inspect")
            with ui.tab_panels(center_tabs, value="chat") as center_panels:
                ctx.center_panels = center_panels
                with ui.tab_panel(chat_tab):
                    chat_area.attach_chat_area(ctx)
                    input_area.attach_input(ctx)
                with ui.tab_panel(inspect_tab):
                    inspector.attach_inspector_panel(ctx)
        initial_load.attach_initial_load(ctx)
        footer.attach_footer(ctx)

    chats.clear()
    async with user_simulation(root=_build_page) as user:
        yield user
    chats.clear()
