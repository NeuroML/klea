#!/usr/bin/env python3
"""
Tests for the shared FastAPI app lifespan.

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from fastapi.testclient import TestClient
from klea_utils.api.app import make_app
from klea_utils.client_context import ClientContext
from klea_utils.graph.base import BaseLangGraph


class _FakeClient:
    """Stand-in for a fastmcp client created by the session pool."""

    async def aclose(self) -> None:
        pass


class _FakeGraph:
    """Minimal graph stub exposing the attributes the lifespan touches."""

    def __init__(self, user_data_dir: Path) -> None:
        self.paths = SimpleNamespace(user_data_dir=user_data_dir)
        self.checkpointer = None
        self.mcp_client = None
        self.mcp_config = None
        self.mcp_session_pool = None
        self.setup_called = False
        self.client_contexts: list[ClientContext | None] = []

    async def setup(self) -> None:
        self.setup_called = True

    def new_mcp_client(
        self, client_context: ClientContext | None = None
    ) -> _FakeClient:
        self.client_contexts.append(client_context)
        return _FakeClient()


def test_lifespan_wires_session_mcp_pool(tmp_path: Path) -> None:
    """Startup builds the pool and hands it to the graph before ``setup()``."""
    graph = _FakeGraph(tmp_path)
    app = make_app(lambda: cast(BaseLangGraph, graph))

    with TestClient(app):
        assert graph.setup_called
        assert app.state.mcp_session_pool is not None
        assert graph.mcp_session_pool is app.state.mcp_session_pool

        context = ClientContext(cwd="/tmp/project")
        client = asyncio.run(app.state.mcp_session_pool._factory("session-1", context))
        assert isinstance(client, _FakeClient)
        assert graph.client_contexts == [context]
