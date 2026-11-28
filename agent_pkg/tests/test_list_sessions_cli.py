#!/usr/bin/env python3
"""
Tests for the ``klea list-sessions`` subcommand.

The command is a backend client, so every test replaces the two lazy seams it
resolves at call time: ``klea_utils.ui.client.fetch_chats`` (an async fake, no
HTTP) and ``klea_utils.ui.cli._maybe_spawn_server`` (a no-op context manager,
no subprocess).  An explicit ``--user-id`` keeps the identity seam from
persisting or reading anything.

File: tests/test_list_sessions_cli.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import contextlib
import json
import logging

import pytest
from klea_agent.ui.cli import agent_app
from typer.testing import CliRunner

logger = logging.getLogger(__name__)

runner = CliRunner()

#: Chats as the server's ``GET /chat/{user_id}`` returns them: newest first,
#: with the already-truncated ``preview`` (``CHAT_PREVIEW_MAX_CHARS``).
CHATS = [
    {
        "user_id": "u1",
        "chat_id": "olivine-caracal",
        "title": "Olivine Caracal",
        "created_at": 1764200000.0,
        "updated_at": 1764300000.0,
        "overrides": "{}",
        "preview": "How do I run the tests?",
    },
    {
        "user_id": "u1",
        "chat_id": "basalt-lynx",
        "title": "Basalt Lynx",
        "created_at": 1764100000.0,
        "updated_at": 1764200000.0,
        "overrides": "{}",
        "preview": "Summarise the paper.",
    },
]


@pytest.fixture
def no_spawn(monkeypatch):
    """Replace the server spawn seam with a no-op context manager."""
    monkeypatch.setattr(
        "klea_utils.ui.cli._maybe_spawn_server",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )


@pytest.fixture
def chats(monkeypatch):
    """Serve *chats* from the shared ``fetch_chats`` client call."""

    def _use(chats: list[dict]):
        async def fake_fetch_chats(server_url: str, user_id: str) -> list[dict]:
            return chats

        monkeypatch.setattr("klea_utils.ui.client.fetch_chats", fake_fetch_chats)

    return _use


def test_lists_sessions_newest_first(no_spawn, chats):
    """Each session prints its chat id, title and preview, in server order."""
    chats(CHATS)

    result = runner.invoke(agent_app, ["list-sessions", "--user-id", "u1"])
    assert result.exit_code == 0, result.output

    output = result.output
    lines = output.splitlines()
    assert "olivine-caracal" in lines[0]
    assert "Olivine Caracal" in lines[0]
    assert lines[1] == "    How do I run the tests?"
    assert "basalt-lynx" in lines[2]
    assert "Basalt Lynx" in lines[2]
    assert lines[3] == "    Summarise the paper."
    # The server orders ``updated_at DESC`` and the command keeps that order.
    assert output.index("olivine-caracal") < output.index("basalt-lynx")
    logger.debug("populated listing rendered in server order")


def test_limit_caps_the_number_of_sessions(no_spawn, chats):
    """``--limit`` keeps only the newest sessions the server returned."""
    chats(CHATS)

    result = runner.invoke(
        agent_app, ["list-sessions", "--user-id", "u1", "--limit", "1"]
    )
    assert result.exit_code == 0, result.output

    assert "olivine-caracal" in result.output
    assert "basalt-lynx" not in result.output


def test_no_preview_drops_the_preview_line(no_spawn, chats):
    """``--no-preview`` prints the header line only."""
    chats(CHATS)

    result = runner.invoke(
        agent_app, ["list-sessions", "--user-id", "u1", "--no-preview"]
    )
    assert result.exit_code == 0, result.output

    assert "olivine-caracal" in result.output
    assert "How do I run the tests?" not in result.output


def test_empty_listing_prints_a_message(no_spawn, chats):
    """No sessions prints a message (and ``[]`` for ``--json``), exiting 0."""
    chats([])

    result = runner.invoke(agent_app, ["list-sessions", "--user-id", "u1"])
    assert result.exit_code == 0, result.output
    assert "No chat sessions found for user u1." in result.output

    result = runner.invoke(agent_app, ["list-sessions", "--user-id", "u1", "--json"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output) == []


def test_json_output_is_machine_readable(no_spawn, chats):
    """``--json`` prints one array with a fixed, trimmed key set."""
    chats(CHATS)

    result = runner.invoke(agent_app, ["list-sessions", "--user-id", "u1", "--json"])
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload == [
        {
            "chat_id": "olivine-caracal",
            "title": "Olivine Caracal",
            "updated_at": 1764300000.0,
            "preview": "How do I run the tests?",
        },
        {
            "chat_id": "basalt-lynx",
            "title": "Basalt Lynx",
            "updated_at": 1764200000.0,
            "preview": "Summarise the paper.",
        },
    ]
    # The raw epoch is kept for scripting; the display label is not used here.
    assert payload[0]["updated_at"] == CHATS[0]["updated_at"]


def test_json_without_preview_omits_the_key(no_spawn, chats):
    """``--no-preview`` drops the ``preview`` key from the JSON objects."""
    chats(CHATS)

    result = runner.invoke(
        agent_app,
        ["list-sessions", "--user-id", "u1", "--json", "--no-preview"],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert [set(item) for item in payload] == [
        {"chat_id", "title", "updated_at"},
        {"chat_id", "title", "updated_at"},
    ]


def test_json_drops_server_only_fields(no_spawn, chats):
    """``user_id``, ``created_at`` and ``overrides`` are not exposed."""
    chats(CHATS[:1])

    result = runner.invoke(agent_app, ["list-sessions", "--user-id", "u1", "--json"])
    assert result.exit_code == 0, result.output

    item = json.loads(result.output)[0]
    for dropped in ("user_id", "created_at", "overrides"):
        assert dropped not in item


def test_negative_limit_is_rejected(no_spawn, chats):
    """A negative ``--limit`` fails before anything is fetched."""
    chats(CHATS)

    result = runner.invoke(
        agent_app, ["list-sessions", "--user-id", "u1", "--limit", "-1"]
    )
    assert result.exit_code != 0
