#!/usr/bin/env python3
"""
Pilot tests for the shared Textual TUI app.

Each test drives the real :class:`KleaTuiApp` in-process with Textual's
``run_test()`` pilot against a :class:`FakeBackend` (scripted
``stream_events`` + catalogue), covering streaming, turn status, Stop/Retry,
the inspector/status panes, session commands and HITL.

File: tests/ui/tui/test_app.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from klea_utils.ui.state import chats
from klea_utils.ui.tui.components.commands import CommandSuggestions
from klea_utils.ui.tui.components.hitl import HitlModal, PermissionModal
from textual.widgets import Button, Collapsible, Input, RadioButton, Static


def _mode_metadata() -> dict:
    """A server-side ``/mode`` catalogue entry."""
    return {
        "name": "mode",
        "summary": "Show or set the operating mode",
        "arg_hint": "[general|scientific]",
        "side": "server",
        "klass": "session-state",
        "while_streaming": "block",
        "persists": "checkpoint",
        "capabilities": [],
        "implemented": True,
        "aliases": [],
    }


def _chat() -> Any:
    """Return the active test chat's data dict."""
    return chats[":test-chat"]


async def _submit(pilot, app, text: str) -> None:
    """Type *text* into the input and press Enter."""
    field = app.screen.query_one("#query-input", Input)
    field.focus()
    field.value = text
    await pilot.press("enter")


async def _settle(pilot, app) -> None:
    """Let the run worker(s) finish and the UI repaint."""
    await pilot.pause()
    await app.workers.wait_for_complete()
    await pilot.pause()


async def test_streaming_renders_progress_and_complete(fake_backend, make_app):
    """A query streams progress then renders the final message."""
    fake_backend.scripts = [
        [
            {"type": "progress", "node": "n", "data": {"heading": "Working"}},
            {"type": "complete", "message_for_user": "the answer"},
        ]
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)

    texts = [message["text"] for message in _chat()["messages"]]
    assert texts[0] == "hi"
    assert "the answer" in texts
    assert _chat()["turn_status"]["kind"] == "idle"


async def test_stop_sets_stopped_status(fake_backend, make_app, monkeypatch):
    """Ctrl+C cancels the run and asks the server to cancel."""
    cancelled: list[bool] = []

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "Working hard"}}
        await asyncio.Event().wait()  # never completes until cancelled

    async def _cancel(*args, **kwargs):
        cancelled.append(True)
        return True

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    monkeypatch.setattr("klea_utils.api.sse.request_cancel", _cancel)

    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        await pilot.press("ctrl+c")
        await _settle(pilot, app)

    assert _chat()["turn_status"]["kind"] == "stopped"
    assert cancelled


async def test_retry_resumes_without_duplicating_the_turn(fake_backend, make_app):
    """Retry resumes the failed run and does not re-append the user turn."""
    fake_backend.scripts = [
        [{"type": "error", "message": "boom", "resumable": True}],
        [{"type": "complete", "message_for_user": "recovered"}],
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        await pilot.press("ctrl+r")
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["resume"] is True
    texts = [message["text"] for message in _chat()["messages"]]
    assert texts.count("hi") == 1
    assert "recovered" in texts


async def test_interrupt_opens_modal_and_resumes(fake_backend, make_app):
    """A review interrupt opens the modal; the answer resumes the same run."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "review",
                    "question": "Approve?",
                    "interrupt_id": "i1",
                },
            }
        ],
        [{"type": "complete", "message_for_user": "done"}],
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await _settle(pilot, app)
        assert isinstance(app.screen, HitlModal)
        app.screen.query_one("#hitl-approve", Button).press()
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["interrupt_response"] == {"decision": "approve"}
    assert fake_backend.calls[-1]["interrupt_id"] == "i1"
    texts = [message["text"] for message in _chat()["messages"]]
    assert any("Plan approved" in text for text in texts)


async def test_permission_interrupt_modal(fake_backend, make_app):
    """A permission ask opens the permission modal; Submit sends decisions."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "permission",
                    "requests": [
                        {
                            "kind": "outside",
                            "directory": "/tmp/x",
                            "tool": "read_file",
                            "confidence": "high",
                        },
                        {"kind": "sensitive", "path": "/home/u/.ssh/id_rsa"},
                    ],
                    "interrupt_id": "p1",
                },
            }
        ],
        [{"type": "complete", "message_for_user": "done"}],
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await _settle(pilot, app)
        assert isinstance(app.screen, PermissionModal)
        # The first request defaults to deny; allow it once instead.
        app.screen.query_one("#perm-0-once", RadioButton).value = True
        app.screen.query_one("#perm-submit", Button).press()
        await _settle(pilot, app)

    call = fake_backend.calls[-1]
    assert call["interrupt_id"] == "p1"
    assert call["interrupt_response"] == {
        "decisions": [
            {"key": "/tmp/x", "decision": "once"},
            {"key": "/home/u/.ssh/id_rsa", "decision": "deny"},
        ]
    }


async def test_pending_interrupt_represents_on_load(fake_backend, make_app):
    """A pending interrupt is re-presented on load (hydrated context)."""
    fake_backend.chat_context = {
        "context": {"mode": "general"},
        "pending_interrupt": {
            "kind": "review",
            "question": "Approve?",
            "interrupt_id": "i9",
        },
    }
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert isinstance(app.screen, HitlModal)
        assert _chat()["turn_status"]["kind"] == "awaiting_input"
        assert _chat()["context"]["mode"] == "general"


async def test_toggle_inspector_view(fake_backend, make_app):
    """Ctrl+I toggles the chat panel to the inspector with query sections."""
    fake_backend.scripts = [
        [
            {
                "type": "inspect",
                "node": "N",
                "data": {"heading": "N", "summary": "s", "details": {"x": 1}},
            },
            {"type": "complete", "message_for_user": "done"},
        ]
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        await pilot.press("ctrl+i")
        await pilot.pause()
        inspector = app.screen.query_one("#inspector")
        assert inspector.display is True
        assert list(inspector.query(Collapsible))


async def test_status_panel_shows_context_state_and_usage(fake_backend, make_app):
    """The status panel renders context, state sections and token usage."""
    fake_backend.scripts = [
        [
            {"type": "context", "data": {"mode": "general"}},
            {
                "type": "state",
                "node": "Planner",
                "data": {"heading": "Plan", "display": "step 1"},
            },
            {
                "type": "usage",
                "node": "n",
                "data": {"details": {"input_tokens": 3, "output_tokens": 1}},
            },
            {"type": "complete", "message_for_user": "done"},
        ]
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        panel = app.screen.query_one("#status")
        titles = [str(collapsible.title) for collapsible in panel.query(Collapsible)]
        statics = [str(static.render()) for static in panel.query(Static)]
        assert "context" in titles and "Plan" in titles
        assert any("3 in / 1 out" in text for text in statics)


async def test_client_help_command_renders_locally(fake_backend, make_app):
    """A client ``/help`` renders the catalogue and starts no run."""
    fake_backend.commands = [_mode_metadata()]
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await _submit(pilot, app, "/help")
        await pilot.pause()

    texts = [message["text"] for message in _chat()["messages"]]
    assert any("Available commands" in text for text in texts)
    assert fake_backend.calls == []


async def test_unknown_command_is_rejected_locally(fake_backend, make_app):
    """An unknown command renders an error and is never forwarded."""
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "/bogus")
        await pilot.pause()

    texts = [message["text"] for message in _chat()["messages"]]
    assert any("Unknown command" in text for text in texts)
    assert fake_backend.calls == []


async def test_server_command_is_forwarded(fake_backend, make_app):
    """A server command is forwarded to the graph as the query."""
    fake_backend.commands = [_mode_metadata()]
    fake_backend.scripts = [[{"type": "complete", "message_for_user": "ok"}]]
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await _submit(pilot, app, "/mode general")
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["query"] == "/mode general"


async def test_autocomplete_lists_and_hides(fake_backend, make_app):
    """Typing ``/prefix`` shows matching commands; a non-command hides them."""
    fake_backend.commands = [_mode_metadata()]
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        field = app.screen.query_one("#query-input", Input)
        field.value = "/m"
        await pilot.pause()
        suggestions = app.screen.query_one("#command-suggestions", CommandSuggestions)
        assert suggestions.display is True
        assert [option.id for option in suggestions.options] == ["mode"]

        field.value = "hi"
        await pilot.pause()
        assert suggestions.display is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
