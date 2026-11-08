#!/usr/bin/env python3
"""
Tests for the TUI ``/export`` client command.

The terminal client has no browser, so ``/export`` writes the session
transcript as Markdown to ``<slug>.md`` in the frontend's current working
directory instead of downloading it.  The tests drive the command through
:func:`handle_command` (the merged-catalogue entry point the TUI uses), with
the working directory monkeypatched to a ``tmp_path`` so the write is
sandboxed (and Windows-safe), and assert against the written file.

File: tests/test_tui_commands_export.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import pytest
from klea_utils.ui import state
from klea_utils.ui.state import MessageData, ensure_chat
from klea_utils.ui.tui.components.commands import client_commands, handle_command

#: The user every test drives the command for.
USER_ID = "user-export"

#: The chat every test drives the command for.
CHAT_ID = "brave-otter"


@pytest.fixture(autouse=True)
def _fresh_chats(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate the shared chat store for every test."""
    monkeypatch.setattr(state, "chats", {})


@pytest.fixture(autouse=True)
def _in_tmp_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test inside its own temporary working directory."""
    monkeypatch.chdir(tmp_path)


def _seed(messages: Iterable[MessageData], name: str = "My session") -> None:
    """Seed the test chat with *messages* and a display name."""
    chat = ensure_chat(USER_ID, CHAT_ID)
    chat["name"] = name
    chat["messages"].extend(messages)


def _run(text: str, chat_id: str | None = CHAT_ID):
    """Run *text* through ``handle_command`` for the TUI client catalogue."""
    return handle_command(
        text,
        client_commands(),
        user_id=USER_ID,
        chat_id=chat_id,
    )


def _message(role: str, text: str, stamp: str = "", header: str = "") -> MessageData:
    """Build a transcript message for the seed."""
    return {"role": role, "text": text, "stamp": stamp, "header": header}


class TestTuiExport:
    """The TUI ``/export`` command writes the transcript to the cwd."""

    def test_export_writes_the_transcript(self, tmp_path: Path):
        _seed(
            [
                _message("user", "Hello there", "10:00"),
                _message("agent", "General Kenobi", "10:01"),
            ]
        )
        result = _run("/export")
        assert result is not None and not result.error
        path = tmp_path / "My-session.md"
        assert path.is_file()
        content = path.read_text(encoding="utf-8")
        assert content.startswith("# My session\n")
        assert "## User (10:00)" in content
        assert "Hello there" in content
        assert "## Assistant (10:01)" in content
        assert "General Kenobi" in content
        assert any(str(path.resolve()) in line for line in result.output)

    def test_export_includes_tool_blocks(self, tmp_path: Path):
        _seed(
            [
                _message("user", "run it", "10:00"),
                {**_message("tool", "exit 0", "10:02"), "header": "run_command"},
            ]
        )
        result = _run("/export")
        assert result is not None and not result.error
        content = (tmp_path / "My-session.md").read_text(encoding="utf-8")
        assert "## Tool: run_command (10:02)" in content
        assert "exit 0" in content

    def test_export_without_an_active_chat_fails(self, tmp_path: Path):
        result = _run("/export", chat_id=None)
        assert result is not None
        assert "No active session" in result.error
        assert list(tmp_path.iterdir()) == []

    def test_export_with_nothing_to_export_fails(self, tmp_path: Path):
        _seed([])
        result = _run("/export")
        assert result is not None
        assert "nothing to export" in result.error
        assert list(tmp_path.iterdir()) == []

    def test_export_filename_falls_back_when_unnamed(self, tmp_path: Path):
        _seed([_message("user", "hi", "10:00")], name="???")
        result = _run("/export")
        assert result is not None and not result.error
        assert (tmp_path / "klea-session.md").is_file()
