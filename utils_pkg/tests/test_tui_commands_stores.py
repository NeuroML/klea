#!/usr/bin/env python3
"""Focused tests for the TUI ``/stores`` command (ADR-0047).

``/stores`` records the resolved absolute path of this chat's stores
configuration file on that chat's ``stores_config`` slot; the TUI turns the
slot into the outgoing tool-call ``_meta``.  The tests drive the command
through :func:`handle_command` (the merged-catalogue entry point the TUI uses)
and assert against the per-chat state, so a run of this module alone
demonstrates the ``stores_config`` round-trip and the no-mutation-on-error
behaviour.

File: utils_pkg/tests/test_tui_commands_stores.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from klea_utils.ui.state import ChatData, chats, ensure_chat
from klea_utils.ui.tui.components.commands import client_commands, handle_command

#: The chat every test drives the command for.
USER_ID = "stores-user"
CHAT_ID = "stores-chat"

#: The value a pre-existing chat starts with, so a failure can be told apart
#: from a write of the same value.
SENTINEL = "/sentinel/keep.json"


@pytest.fixture(autouse=True)
def clean_state() -> Any:
    """Empty the shared chat store around every test."""
    chats.clear()
    yield
    chats.clear()


def _run(
    text: str,
    *,
    user_id: str | None = USER_ID,
    chat_id: str | None = CHAT_ID,
) -> Any:
    """Run *text* through ``handle_command`` for the test catalogue."""
    return handle_command(
        text,
        client_commands(),
        user_id=user_id,
        chat_id=chat_id,
    )


def _text(result: Any) -> str:
    """Return the result's message text, tolerating its exact shape."""
    for attr in ("text", "message", "output", "detail", "content"):
        value = getattr(result, attr, None)
        plain = getattr(value, "plain", value)
        if isinstance(plain, str) and plain:
            return plain
    return str(result)


def _chat() -> ChatData:
    """Return this test's chat session dict."""
    return ensure_chat(USER_ID, CHAT_ID)


def _seed(value: str | None = SENTINEL) -> None:
    """Create the test chat with a known ``stores_config`` value."""
    _chat()["stores_config"] = value


def test_usage_error_keeps_slot_unchanged() -> None:
    """``/stores`` with no argument fails and writes nothing."""
    _seed()
    result = _run("/stores")
    assert result is not None
    assert "Usage: /stores <path>|clear" in _text(result)
    assert _chat()["stores_config"] == SENTINEL


def test_no_active_chat_is_rejected(tmp_path: Path) -> None:
    """Without a chat identity the command fails before touching state."""
    config = tmp_path / "stores.json"
    config.write_text("{}", encoding="utf-8")
    result = _run(f"/stores {config}", user_id=None, chat_id=None)
    assert result is not None
    assert "No active chat" in _text(result)
    assert chats == {}


def test_relative_path_is_rejected() -> None:
    """A relative path is refused, leaving the slot alone."""
    _seed()
    result = _run("/stores relative/stores.json")
    assert result is not None
    assert "must be absolute" in _text(result)
    assert _chat()["stores_config"] == SENTINEL


def test_missing_path_is_rejected(tmp_path: Path) -> None:
    """A non-existent absolute path is refused, leaving the slot alone."""
    _seed()
    missing = tmp_path / "does-not-exist.json"
    result = _run(f"/stores {missing}")
    assert result is not None
    assert "does not exist" in _text(result)
    assert _chat()["stores_config"] == SENTINEL


def test_directory_is_rejected(tmp_path: Path) -> None:
    """A directory is refused, leaving the slot alone."""
    _seed()
    directory = tmp_path / "stores.d"
    directory.mkdir()
    result = _run(f"/stores {directory}")
    assert result is not None
    assert "is not a file" in _text(result)
    assert _chat()["stores_config"] == SENTINEL


def test_valid_file_records_absolute_path(tmp_path: Path) -> None:
    """A real file writes its resolved absolute path into the chat."""
    _seed(None)
    config = tmp_path / "stores.json"
    config.write_text("{}", encoding="utf-8")
    result = _run(f"/stores {config}")
    assert result is not None
    assert "Stores configuration set to" in _text(result)
    assert _chat()["stores_config"] == str(Path(os.path.abspath(config)))


def test_valid_file_overwrites_previous_value(tmp_path: Path) -> None:
    """A successful ``/stores`` replaces an earlier selection."""
    _seed()
    config = tmp_path / "other-stores.json"
    config.write_text("{}", encoding="utf-8")
    _run(f"/stores {config}")
    assert _chat()["stores_config"] == str(Path(os.path.abspath(config)))


def test_tilde_resolves_against_monkeypatched_home(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``~`` expands via HOME, then is stored as the absolute path."""
    _seed(None)
    home = tmp_path / "home"
    home.mkdir()
    config = home / "stores.json"
    config.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    result = _run("/stores ~/stores.json")
    assert result is not None
    assert "Stores configuration set to" in _text(result)
    assert _chat()["stores_config"] == str(config)


def test_clear_sets_slot_to_none() -> None:
    """``/stores clear`` resets the slot so the deployment default applies."""
    _seed()
    result = _run("/stores clear")
    assert result is not None
    assert "cleared" in _text(result)
    assert _chat()["stores_config"] is None
