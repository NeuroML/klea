#!/usr/bin/env python3
"""
Tests for the shared ``GET /commands`` router (ADR-0047).

File: tests/test_commands_api.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from klea_utils.api.commands import create_commands_router
from klea_utils.commands.common import Command, CommandRegistry


def _client(registry: Any) -> TestClient:
    app = FastAPI()
    app.state.graph = SimpleNamespace(command_registry=registry)
    app.include_router(create_commands_router())
    return TestClient(app)


def _registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(Command(name="mode", summary="set the mode", side="server"))
    registry.register(
        Command(name="access", summary="stub", side="server", implemented=False)
    )
    return registry


def test_lists_only_available_commands():
    body = _client(_registry()).get("/commands").json()
    assert [c["name"] for c in body["commands"]] == ["mode"]


def test_metadata_shape():
    entry = _client(_registry()).get("/commands").json()["commands"][0]
    assert entry["name"] == "mode"
    assert entry["side"] == "server"
    assert entry["implemented"] is True
    assert "handler" not in entry


def test_no_registry_returns_empty_catalogue():
    app = FastAPI()
    app.include_router(create_commands_router())
    assert TestClient(app).get("/commands").json() == {"commands": []}
