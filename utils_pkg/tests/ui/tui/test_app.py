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
from klea_utils.ui.tui.components.confirm import ExitConfirm
from klea_utils.ui.tui.components.credentials_dialog import CredentialsDialog
from klea_utils.ui.tui.components.hitl import HitlPanel, PermissionPanel
from klea_utils.ui.tui.components.input import QueryInput
from klea_utils.ui.tui.components.model_dialog import ModelDialog
from klea_utils.ui.tui.components.selectable import SelectableStatic
from klea_utils.ui.tui.components.sessions import (
    SessionDelete,
    SessionPicker,
    SessionRename,
)
from klea_utils.ui.tui.components.transcript import MessageBlock, Transcript
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import (
    Button,
    Collapsible,
    Input,
    Label,
    ListView,
    RadioButton,
    Static,
    TextArea,
)


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
    field = app.screen.query_one("#query-input", TextArea)
    field.focus()
    field.text = text
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


async def test_interrupt_opens_panel_and_resumes(fake_backend, make_app):
    """A review interrupt opens the panel; the answer resumes the same run."""
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
        assert app.screen.query_one(HitlPanel)
        app.screen.query_one("#hitl-approve", Button).press()
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["interrupt_response"] == {"decision": "approve"}
    assert fake_backend.calls[-1]["interrupt_id"] == "i1"
    texts = [message["text"] for message in _chat()["messages"]]
    assert any("Plan approved" in text for text in texts)


async def test_review_interrupt_panel_shows_headline_and_sections(
    fake_backend, make_app
):
    """The review panel renders the adaptive headline and sections."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "review",
                    "headline": "Approve this plan (2 steps)",
                    "question": "Approve, request changes, or cancel.",
                    "sections": [
                        {
                            "title": "Plan",
                            "body": "[ ] Step 1: fetch\n[ ] Step 2: write",
                        }
                    ],
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
        assert app.screen.query_one(HitlPanel)
        headline = app.screen.query_one("#hitl-headline", Static)
        assert "Approve this plan" in str(headline.render())
        title = app.screen.query_one("#hitl-section-0-title", Static)
        assert str(title.render()) == "Plan"
        body = app.screen.query_one("#hitl-section-0-body", Static)
        assert "Step 1: fetch" in str(body.render())
        app.screen.query_one("#hitl-approve", Button).press()
        await _settle(pilot, app)


async def test_review_panel_with_long_section_scrolls(fake_backend, make_app):
    """A long review section scrolls so Approve/Decline stay reachable."""
    long_plan = "\n".join(f"step {i}: do the thing" for i in range(60))
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "review",
                    "headline": "Approve this plan (60 steps)",
                    "question": "Approve, request changes, or cancel.",
                    "sections": [{"title": "Plan", "body": long_plan}],
                    "interrupt_id": "i1",
                },
            }
        ],
        [{"type": "complete", "message_for_user": "done"}],
    ]
    app = make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await _submit(pilot, app, "go")
        await _settle(pilot, app)
        assert app.screen.query_one(HitlPanel)
        panel = app.screen.query_one(HitlPanel)
        assert panel.max_scroll_y > 0
        app.screen.query_one("#hitl-approve", Button).press()
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["interrupt_response"] == {"decision": "approve"}


async def test_permission_interrupt_panel(fake_backend, make_app):
    """A permission ask opens the permission panel; Submit submits decisions."""
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
        assert app.screen.query_one(PermissionPanel)
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


async def test_permission_panel_shows_full_path_and_scope(fake_backend, make_app):
    """The permission panel shows the target path, scope and call context."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "permission",
                    "requests": [
                        {
                            "kind": "outside",
                            "tool": "read_file",
                            "argument": "path",
                            "path": "/tmp/x/file.txt",
                            "directory": "/tmp/x",
                            "argument_preview": "read /tmp/x/file.txt",
                            "confidence": "declared",
                            "source": "checkpaths:path",
                        }
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
        panel = app.screen.query_one(PermissionPanel)
        texts = [str(widget.render()) for widget in panel.query(Static)]
        assert any("Outside project path: /tmp/x/file.txt" in text for text in texts)
        assert any("Session approval covers: /tmp/x" in text for text in texts)
        assert any("read /tmp/x/file.txt" in text for text in texts)
        assert any("Source: checkpaths:path" in text for text in texts)
        app.screen.query_one("#perm-submit", Button).press()
        await _settle(pilot, app)


async def test_permission_panel_uses_bounded_path_preview(fake_backend, make_app):
    """A bounded path_display is used when the payload carries one."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "permission",
                    "requests": [
                        {
                            "kind": "outside",
                            "path": "/tmp/" + ("a" * 500) + "/file.txt",
                            "path_display": "/tmp/aaa... [500 chars omitted] .../file.txt",
                            "directory": "/tmp",
                            "confidence": "guess",
                        }
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
        panel = app.screen.query_one(PermissionPanel)
        texts = [str(widget.render()) for widget in panel.query(Static)]
        assert any("chars omitted" in text for text in texts)
        app.screen.query_one("#perm-submit", Button).press()
        await _settle(pilot, app)


async def test_permission_panel_with_many_requests_scrolls(fake_backend, make_app):
    """A permission ask with many paths scrolls so Submit stays reachable."""
    requests = [
        {
            "kind": "outside",
            "directory": f"/tmp/{i}",
            "tool": "read_file",
            "confidence": "high",
        }
        for i in range(20)
    ]
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "permission",
                    "requests": requests,
                    "interrupt_id": "p1",
                },
            }
        ],
        [{"type": "complete", "message_for_user": "done"}],
    ]
    app = make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await _submit(pilot, app, "go")
        await _settle(pilot, app)
        assert app.screen.query_one(PermissionPanel)
        panel = app.screen.query_one(PermissionPanel)
        assert panel.max_scroll_y > 0
        app.screen.query_one("#perm-submit", Button).press()
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["interrupt_id"] == "p1"


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
        assert app.screen.query_one(HitlPanel)
        assert app.screen.query_one("#query-input-frame").display is False
        assert _chat()["turn_status"]["kind"] == "awaiting_input"
        assert _chat()["context"]["mode"] == "general"


async def test_pending_permission_represents_on_load(fake_backend, make_app):
    """A pending permission interrupt is re-presented inline on load."""
    fake_backend.chat_context = {
        "context": {"mode": "general"},
        "pending_interrupt": {
            "kind": "permission",
            "requests": [
                {
                    "kind": "outside",
                    "directory": "/tmp/x",
                    "tool": "read_file",
                    "confidence": "high",
                }
            ],
            "interrupt_id": "p9",
        },
    }
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.screen.query_one(PermissionPanel)
        assert app.screen.query_one("#query-input-frame").display is False


async def test_input_ask_mounts_inline_panel(fake_backend, make_app):
    """An input ask mounts the inline panel and answers positionally."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "input",
                    "questions": [{"step_number": 1, "question": "Which file?"}],
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
        assert app.screen.query_one(HitlPanel)
        app.screen.query_one("#hitl-answer-0", Input).value = "a.txt"
        app.screen.query_one("#hitl-answer", Button).press()
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["interrupt_response"] == {"answers": ["a.txt"]}


async def test_input_ask_renders_plan_history_sections(fake_backend, make_app):
    """An input ask renders abandoned plan versions above the questions."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {
                    "kind": "input",
                    "sections": [
                        {
                            "title": "Plan version 1 (abandoned)",
                            "body": "Abandoned: tool failed\nSteps:\n[FAILED] 1. read",
                        }
                    ],
                    "questions": [
                        {"step_number": 2, "question": "How should I proceed?"}
                    ],
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
        assert app.screen.query_one(HitlPanel)
        title = app.screen.query_one("#hitl-section-0-title", Static)
        assert str(title.render()) == "Plan version 1 (abandoned)"
        body = app.screen.query_one("#hitl-section-0-body", Static)
        assert "Abandoned: tool failed" in str(body.render())
        assert app.screen.query_one("#hitl-answer-0", Input)
        app.screen.query_one("#hitl-answer-0", Input).value = "try again"
        app.screen.query_one("#hitl-answer", Button).press()
        await _settle(pilot, app)

    assert fake_backend.calls[-1]["interrupt_response"] == {"answers": ["try again"]}


async def test_inline_panel_swaps_the_composer(fake_backend, make_app):
    """The ask replaces the composer while active and restores it on a decision."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {"kind": "review", "headline": "Approve", "interrupt_id": "i1"},
            }
        ],
        [{"type": "complete", "message_for_user": "done"}],
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await _settle(pilot, app)
        assert app.screen.query_one("#query-input-frame").display is False
        assert "Approve" in str(app.screen.query_one("#key-hints", Static).render())
        app.screen.query_one("#hitl-approve", Button).press()
        await _settle(pilot, app)
        assert app.screen.query_one("#query-input-frame").display is True
        assert "Enter send" in str(app.screen.query_one("#key-hints", Static).render())


async def test_inline_panel_keeps_the_transcript_scrollable(fake_backend, make_app):
    """The transcript stays mounted and scrollable while a panel is active."""
    fake_backend.scripts = [
        [
            {
                "type": "interrupt",
                "data": {"kind": "review", "headline": "Approve", "interrupt_id": "i1"},
            }
        ],
        [{"type": "complete", "message_for_user": "done"}],
    ]
    app = make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await _submit(pilot, app, "go")
        await _settle(pilot, app)
        assert app.screen.query_one(HitlPanel)
        transcript = app.screen.query_one("#transcript", Transcript)
        assert transcript.display is True
        for index in range(40):
            await transcript.append(
                {
                    "text": f"line {index}",
                    "stamp": "",
                    "role": "assistant",
                    "header": "",
                }
            )
        await pilot.pause()
        assert transcript.max_scroll_y > 0
        transcript.scroll_home(animate=False)
        await pilot.pause()
        assert transcript.scroll_y == 0
        app.screen.query_one("#hitl-approve", Button).press()
        await _settle(pilot, app)


async def test_hydration_rebuilds_panes_and_token_usage(fake_backend, make_app):
    """Stored events + the usage projection hydrate the panes on load."""
    fake_backend.chat_events = [
        {"type": "query", "text": "hello", "stamp": "10:00:00"},
        {
            "type": "inspect",
            "node": "Plan",
            "data": {"heading": "Plan", "summary": "1 step", "details": {}},
        },
        {
            "type": "state",
            "node": "Plan",
            "data": {"key": "plan", "heading": "Plan", "display": "[x] Step 1"},
        },
    ]
    fake_backend.chat_context = {
        "context": None,
        "pending_interrupt": None,
        "usage": {
            "input_tokens": 5,
            "output_tokens": 3,
            "total_tokens": 8,
            "cached_tokens": 2,
        },
    }
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        chat = _chat()
        assert [e["type"] for e in chat["inspector_entries"]] == ["query", "inspect"]
        assert chat["state_sections"]["plan"]["display"] == "[x] Step 1"
        assert chat["token_usage"]["total_tokens"] == 8
        assert chat["token_usage"]["cached_tokens"] == 2


async def test_active_chat_is_keyed_by_user(fake_backend, make_app):
    """The keyed store entry is ``{user_id}:{chat_id}`` for the active chat."""
    app = make_app(user_id="u1", chat_id="c1")
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)

    assert "u1:c1" in chats


async def test_resume_specific_chat(fake_backend, make_app):
    """--resume loads the given chat and renders its messages."""
    fake_backend.messages["c9"] = [
        {"role": "user", "content": "old q", "created_at": 0},
        {"role": "assistant", "content": "old a", "created_at": 0},
    ]
    app = make_app(resume_chat_id="c9")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.ctx.chat_id == "c9"
        chat = chats[":c9"]
        assert [message["text"] for message in chat["messages"]] == ["old q", "old a"]
        transcript = app.screen.query_one("#transcript")
        assert len(list(transcript.children)) == 2


async def test_continue_picks_most_recent(fake_backend, make_app):
    """--continue activates the most recently updated chat."""
    fake_backend.chats = [{"chat_id": "c2"}, {"chat_id": "c1"}]
    app = make_app(continue_session=True)
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert app.ctx.chat_id == "c2"


async def test_switch_blocked_while_streaming(fake_backend, make_app, monkeypatch):
    """load_chat is refused while a run is streaming (I2)."""

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "x"}}
        await asyncio.Event().wait()  # never completes until cancelled

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        await app.load_chat("c2")
        assert app.ctx.chat_id == "c1"
        assert app._worker is not None
        app._worker.cancel()
        await pilot.pause()


async def test_session_picker_switches(fake_backend, make_app):
    """Ctrl+S opens the picker; choosing a chat switches to it."""
    fake_backend.chats = [
        {"chat_id": "c2", "title": "B"},
        {"chat_id": "c1", "title": "A"},
    ]
    fake_backend.messages["c2"] = [{"role": "user", "content": "hi2", "created_at": 0}]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        list_view = app.screen.query_one("#sessions-list", ListView)
        list_view.index = 0
        list_view.action_select_cursor()
        await _settle(pilot, app)

    assert app.ctx.chat_id == "c2"
    assert [m["text"] for m in chats[":c2"]["messages"]] == ["hi2"]


async def test_session_picker_shows_first_query_preview(fake_backend, make_app):
    """The highlighted session's preview renders below the action buttons."""
    fake_backend.chats = [
        {"chat_id": "c2", "title": "B", "preview": "show me the models"},
        {"chat_id": "c1", "title": "A"},
    ]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        list_view = app.screen.query_one("#sessions-list", ListView)
        preview = app.screen.query_one("#sessions-preview", Static)
        assert (
            "show me the models"
            not in list_view.children[0].query_one(Label).render().plain
        )
        assert "show me the models" in str(preview.render())

        # Highlighting a session without a preview clears the hint.
        list_view.index = 1
        await pilot.pause()
        assert str(preview.render()) == ""


async def test_session_picker_rename(fake_backend, make_app):
    """Renaming from the picker calls the server and updates the local name."""
    fake_backend.chats = [
        {"chat_id": "c2", "title": "B"},
        {"chat_id": "c1", "title": "A"},
    ]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        app.screen.query_one("#sessions-rename", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, SessionRename)
        app.screen.query_one("#rename-input", Input).value = "Renamed B"
        app.screen.query_one("#rename-save", Button).press()
        await _settle(pilot, app)

    assert fake_backend.renames == [{"chat_id": "c2", "title": "Renamed B"}]
    assert chats[":c2"]["name"] == "Renamed B"


async def test_session_picker_delete_inactive(fake_backend, make_app):
    """Deleting a non-active chat removes it without switching."""
    fake_backend.chats = [
        {"chat_id": "c2", "title": "B"},
        {"chat_id": "c1", "title": "A"},
    ]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        app.screen.query_one("#sessions-delete", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, SessionDelete)
        app.screen.query_one("#delete-confirm", Button).press()
        await _settle(pilot, app)

    assert fake_backend.deletes == ["c2"]
    assert ":c2" not in chats
    assert app.ctx.chat_id == "c1"


async def test_session_delete_cancelled_keeps_chat(fake_backend, make_app):
    """Cancelling the delete confirmation keeps the session."""
    fake_backend.chats = [
        {"chat_id": "c2", "title": "B"},
        {"chat_id": "c1", "title": "A"},
    ]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        app.screen.query_one("#sessions-delete", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, SessionDelete)
        app.screen.query_one("#delete-cancel", Button).press()
        await pilot.pause()
        # Cancelling returns to the picker; nothing was deleted.
        assert isinstance(app.screen, SessionPicker)

    assert fake_backend.deletes == []
    assert app.ctx.chat_id == "c1"


async def test_session_delete_active_switches(fake_backend, make_app):
    """Deleting the active chat switches to the most recent remaining chat."""
    fake_backend.chats = [
        {"chat_id": "c2", "title": "B"},
        {"chat_id": "c1", "title": "A"},
    ]
    fake_backend.messages["c1"] = [{"role": "user", "content": "hi1", "created_at": 0}]
    app = make_app(chat_id="c2")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert isinstance(app.screen, SessionPicker)
        app.screen.query_one("#sessions-delete", Button).press()
        await pilot.pause()
        assert isinstance(app.screen, SessionDelete)
        app.screen.query_one("#delete-confirm", Button).press()
        await _settle(pilot, app)

    assert fake_backend.deletes == ["c2"]
    assert app.ctx.chat_id == "c1"
    assert [m["text"] for m in chats[":c1"]["messages"]] == ["hi1"]


async def test_open_sessions_blocked_while_streaming(
    fake_backend, make_app, monkeypatch
):
    """Ctrl+S is refused while a run is streaming (I2)."""

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "x"}}
        await asyncio.Event().wait()

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert not isinstance(app.screen, SessionPicker)
        assert app._worker is not None
        app._worker.cancel()
        await pilot.pause()


async def test_exit_command_quits(fake_backend, make_app, monkeypatch):
    """A client ``/exit`` quits the application."""
    exited: list[bool] = []
    app = make_app(chat_id="c1")
    monkeypatch.setattr(app, "exit", lambda *a, **k: exited.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await pilot.pause()
        await _submit(pilot, app, "/exit")
        await _settle(pilot, app)
    assert exited == [True]


async def test_ctrl_q_confirms_before_quitting(fake_backend, make_app, monkeypatch):
    """Ctrl+Q opens the exit confirmation; confirming quits."""
    exited: list[bool] = []
    app = make_app(chat_id="c1")
    monkeypatch.setattr(app, "exit", lambda *a, **k: exited.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ExitConfirm)
        assert exited == []  # not until confirmed
        app.screen.query_one("#exit-confirm", Button).press()
        await _settle(pilot, app)
    assert exited == [True]


async def test_ctrl_q_cancel_does_not_quit(fake_backend, make_app, monkeypatch):
    """Cancelling the exit confirmation keeps the app running."""
    exited: list[bool] = []
    app = make_app(chat_id="c1")
    monkeypatch.setattr(app, "exit", lambda *a, **k: exited.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+q")
        await pilot.pause()
        assert isinstance(app.screen, ExitConfirm)
        app.screen.query_one("#exit-cancel", Button).press()
        await pilot.pause()
    assert exited == []


async def test_exit_command_stops_run_then_quits(fake_backend, make_app, monkeypatch):
    """``/exit`` while streaming stops the run, then quits."""

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "x"}}
        await asyncio.Event().wait()

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    cancelled: list[str] = []

    async def _fake_cancel(server_url, chat_id, user_id=""):
        cancelled.append(chat_id)

    monkeypatch.setattr("klea_utils.api.sse.request_cancel", _fake_cancel)

    exited: list[bool] = []
    app = make_app(chat_id="c1")
    monkeypatch.setattr(app, "exit", lambda *a, **k: exited.append(True))
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        assert app._streaming
        await _submit(pilot, app, "/exit")
        await _settle(pilot, app)
    assert cancelled == ["c1"]
    assert exited == [True]


async def test_toggle_inspector_view(fake_backend, make_app):
    """Tab (what a terminal sends for Ctrl+I) toggles the inspector view."""
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
        app.screen.query_one("#query-input", TextArea).focus()
        await pilot.press("tab")
        await pilot.pause()
        inspector = app.screen.query_one("#inspector")
        assert inspector.display is True
        assert list(inspector.query(Collapsible))
        # Toggling back returns to the transcript.
        await pilot.press("tab")
        await pilot.pause()
        assert inspector.display is False


async def test_tab_toggles_view_from_anywhere(fake_backend, make_app):
    """Tab (Ctrl+I) toggles the view even when the query input is not focused."""
    app = make_app()
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        app.screen.query_one("#query-input", TextArea).blur()
        await pilot.pause()
        await pilot.press("tab")
        await pilot.pause()
        assert app.screen.query_one("#inspector").display is True


async def test_status_panel_shows_state_and_usage(fake_backend, make_app):
    """The status panel renders state sections and token usage."""
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
                "data": {
                    "details": {
                        "input_tokens": 3,
                        "output_tokens": 1,
                        "cached_tokens": 2,
                    }
                },
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
        assert "Plan" in titles
        assert "context" not in titles
        assert any("3 in (2 cached) / 1 out" in text for text in statics)


async def test_status_panel_scrolls_when_content_overflows(fake_backend, make_app):
    """The status panel scrolls (and auto-scrolls to the end) on overflow."""
    app = make_app()
    async with app.run_test(size=(100, 30)) as pilot:
        await _settle(pilot, app)
        chat = _chat()
        chat["state_sections"] = {
            f"k{i}": {
                "heading": f"Section {i}",
                "display": "\n".join(f"line {j}" for j in range(4)),
            }
            for i in range(10)
        }
        await app.screen.refresh_status(chat)
        # Only the state-section body scrolls; the top (extras/models/usage) is
        # fixed.
        scroll = app.screen.query_one("#status-scroll", VerticalScroll)
        # scroll_end runs after the next refresh, which can take a few frames
        # on a slow runner.
        for _ in range(20):
            await pilot.pause()
            if scroll.max_scroll_y > 0 and scroll.scroll_y == scroll.max_scroll_y:
                break
        assert scroll.max_scroll_y > 0
        assert scroll.scroll_y == scroll.max_scroll_y
        extras = app.screen.query_one("#status-extras")
        assert extras.region.y < scroll.region.y  # fixed above the scroll body


async def test_status_panel_shows_models(fake_backend, make_app):
    """The status panel lists the selected models and flags a missing one."""
    fake_backend.model_info = {
        "chat": {"model": "openai:gpt-4o", "required": True, "credential": {}},
        "plan": {"model": "", "required": True, "credential": {}},
    }
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        panel = app.screen.query_one("#status")
        texts = [str(static.render()) for static in panel.query(Static)]
        assert any("gpt-4o" in text for text in texts)
        assert any("Not set" in text for text in texts)
        # Each role shows its provider, with the model indented beneath.
        assert "- Chat: openai" in texts
        assert any(text.strip() == "gpt-4o" for text in texts)
        assert any("Select a model to start" in text for text in texts)


async def test_models_group_is_a_persistent_collapsible(fake_backend, make_app):
    """The models group is collapsed by default and keeps its state on refresh."""
    fake_backend.model_info = {
        "chat": {"model": "openai:gpt-4o", "required": True, "credential": {}}
    }
    app = make_app()
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        models = app.screen.query_one("#status-models", Collapsible)
        assert models.collapsed is True
        models.collapsed = False
        await pilot.pause()
        assert _chat().get("status_models_expanded") is True
        # A status refresh rebuilds the group but keeps it expanded.
        await app.screen.refresh_status(_chat())
        await pilot.pause()
        assert app.screen.query_one("#status-models", Collapsible).collapsed is False


async def test_status_panel_shows_chat_name(fake_backend, make_app):
    """The status panel header shows the chat name and id."""
    app = make_app(chat_id="calm-river")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        text = str(app.screen.query_one("#chat-name", Static).render())
        assert "Calm River" in text
        assert "calm-river" in text


async def test_long_message_collapses_and_expands(fake_backend, make_app):
    """A long message is clipped to a few lines and can be expanded."""
    long_text = "\n\n".join(f"paragraph {i}" for i in range(20))
    fake_backend.scripts = [[{"type": "complete", "message_for_user": long_text}]]
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).last()
        scroll = block.query_one(".message-scroll", VerticalScroll)
        assert scroll.virtual_size.height > 4
        assert scroll.size.height == 4  # clipped to the collapsed height
        block.action_toggle_expand()
        await pilot.pause()
        assert scroll.size.height > 4  # expanded shows the full text


async def test_message_text_can_be_selected_and_copied(fake_backend, make_app):
    """A drag selects message text and Ctrl+C copies it."""
    fake_backend.scripts = [
        [{"type": "complete", "message_for_user": "Alpha beta gamma"}]
    ]
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).last()
        scroll = block.query_one(".message-scroll", VerticalScroll)
        body = scroll.query_one(SelectableStatic)
        await pilot.mouse_down(body, offset=(0, 0))
        await pilot.mouse_up(body, offset=(5, 0))
        await pilot.pause()

        selected = app.screen.get_selected_text()
        assert selected is not None
        assert "Alpha" in selected

        await pilot.press("ctrl+c")
        await pilot.pause()
        assert "Alpha" in app.clipboard


async def test_drag_selection_does_not_toggle_message_expansion(fake_backend, make_app):
    """A drag selects text without expanding the bubble; a click still expands."""
    long_text = "\n\n".join(f"paragraph {i}" for i in range(20))
    fake_backend.scripts = [[{"type": "complete", "message_for_user": long_text}]]
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).last()
        scroll = block.query_one(".message-scroll", VerticalScroll)
        body = scroll.query_one(SelectableStatic)
        assert not scroll.has_class("-expanded")

        await pilot.mouse_down(body, offset=(0, 0))
        await pilot.mouse_up(body, offset=(5, 1))
        await pilot.pause()
        assert not scroll.has_class("-expanded")
        assert app.screen.selections

        app.screen.clear_selection()
        await pilot.click(body)
        await pilot.pause()
        assert scroll.has_class("-expanded")


async def test_long_message_hint_mentions_drag_to_select(fake_backend, make_app):
    """The overflow hint explains both expansion and drag selection."""
    long_text = "\n\n".join(f"paragraph {i}" for i in range(20))
    fake_backend.scripts = [[{"type": "complete", "message_for_user": long_text}]]
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).last()
        hint = block.query_one(".message-more", Static)
        assert "drag to select" in str(hint.render())


async def test_expanded_message_body_does_not_scroll(fake_backend, make_app):
    """An expanded long message scrolls with the transcript, not on its own."""
    long_text = "\n\n".join(f"para {i}" for i in range(60))
    fake_backend.scripts = [[{"type": "complete", "message_for_user": long_text}]]
    app = make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).last()
        block.action_toggle_expand()
        await pilot.pause()
        scroll = block.query_one(".message-scroll", VerticalScroll)
        # Only the transcript scrolls when a message is expanded.
        assert scroll.styles.overflow_y == "hidden"
        assert not scroll.show_vertical_scrollbar


async def test_message_blocks_have_padding(fake_backend, make_app):
    """Transcript message blocks have vertical padding so they read as blocks."""
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).first()
        assert block.styles.padding.top >= 1
        assert block.styles.padding.bottom >= 1


async def test_short_message_is_not_collapsed(fake_backend, make_app):
    """A short message gets no clip and no expand hint."""
    app = make_app()
    async with app.run_test(size=(80, 30)) as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        block = app.screen.query(MessageBlock).last()
        scroll = block.query_one(".message-scroll", VerticalScroll)
        assert scroll.virtual_size.height <= 4
        assert not block.can_focus


async def test_status_details_with_brackets_render(fake_backend, make_app):
    """JSON details with ``[]``/``=`` do not crash Rich markup parsing."""
    fake_backend.scripts = [
        [
            {
                "type": "state",
                "node": "Planner",
                "data": {
                    "heading": "Plan",
                    "details": {
                        "steps": "[StepOutput(step='pwd', kind='tool', "
                        "suggested_tools=['run_command'], depends_on=[])]"
                    },
                },
            },
            {"type": "complete", "message_for_user": "done"},
        ]
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        panel = app.screen.query_one("#status")
        assert list(panel.query(Collapsible))


async def test_inspector_details_with_brackets_render(fake_backend, make_app):
    """An expanded inspector entry with bracketed details does not crash."""
    fake_backend.scripts = [
        [
            {
                "type": "inspect",
                "node": "N",
                "data": {
                    "heading": "N",
                    "summary": "s",
                    "details": {
                        "steps": "[StepOutput(step='pwd', kind='tool', "
                        "depends_on=[], status='pending')]"
                    },
                },
            },
            {"type": "complete", "message_for_user": "done"},
        ]
    ]
    app = make_app()
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        app.screen.query_one("#query-input", TextArea).focus()
        await pilot.press("tab")
        await pilot.pause()
        inspector = app.screen.query_one("#inspector")
        for collapsible in inspector.query(Collapsible):
            collapsible.collapsed = False
        await pilot.pause()
        assert list(inspector.query(Collapsible))


async def test_status_extras_render_in_panel(fake_backend, make_app):
    """App-registered status extras render at the top of the panel."""
    app = make_app(status_extras=[lambda ctx: Static(f"Mode: {ctx.title}")])
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        extras = app.screen.query_one("#status-extras")
        texts = [str(static.render()) for static in extras.query(Static)]
        assert "Mode: Klea Test" in texts


async def test_status_extras_survive_state_update(fake_backend, make_app):
    """Extras persist while the streamed state body is rebuilt."""
    fake_backend.scripts = [
        [
            {
                "type": "state",
                "node": "Planner",
                "data": {"heading": "Plan", "display": "step 1"},
            },
            {"type": "complete", "message_for_user": "done"},
        ]
    ]
    app = make_app(status_extras=[lambda ctx: Static("Access: read_only")])
    async with app.run_test() as pilot:
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        extras = app.screen.query_one("#status-extras")
        texts = [str(static.render()) for static in extras.query(Static)]
        assert "Access: read_only" in texts


async def test_status_extras_refresh_on_context(fake_backend, make_app):
    """A context event rebuilds the app slots (so a selector can update)."""

    def _render(ctx):
        chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}", {})
        mode = (chat.get("context") or {}).get("requested", "none")
        return Static(f"Mode: {mode}")

    fake_backend.scripts = [
        [
            {"type": "context", "data": {"requested": "scientific"}},
            {"type": "complete", "message_for_user": "done"},
        ]
    ]
    app = make_app(status_extras=[_render])
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        extras = app.screen.query_one("#status-extras")
        assert "Mode: none" in [str(static.render()) for static in extras.query(Static)]
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)
        extras = app.screen.query_one("#status-extras")
        assert "Mode: scientific" in [
            str(static.render()) for static in extras.query(Static)
        ]


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


async def test_sessions_command_opens_picker(fake_backend, make_app):
    """A client ``/sessions`` opens the session picker."""
    fake_backend.chats = [{"chat_id": "c1", "title": "A"}]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await _submit(pilot, app, "/sessions")
        await _settle(pilot, app)
        assert isinstance(app.screen, SessionPicker)

    assert fake_backend.calls == []


async def test_sessions_command_blocked_while_streaming(
    fake_backend, make_app, monkeypatch
):
    """``/sessions`` is refused while a run is streaming (I2)."""

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "x"}}
        await asyncio.Event().wait()

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        await _submit(pilot, app, "/sessions")
        await pilot.pause()
        assert not isinstance(app.screen, SessionPicker)
        assert app._worker is not None
        app._worker.cancel()
        await pilot.pause()


async def test_new_command_starts_a_fresh_chat(fake_backend, make_app):
    """A client ``/new`` switches to a fresh, empty chat."""
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await _submit(pilot, app, "/new")
        await _settle(pilot, app)
        assert app.ctx.chat_id != "c1"

    assert fake_backend.calls == []
    assert fake_backend.created == [app.ctx.chat_id]


async def test_new_command_blocked_while_streaming(fake_backend, make_app, monkeypatch):
    """``/new`` is refused while a run is streaming (I2)."""

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "x"}}
        await asyncio.Event().wait()

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        await _submit(pilot, app, "/new")
        await pilot.pause()
        assert app.ctx.chat_id == "c1"
        assert fake_backend.created == []
        assert app._worker is not None
        app._worker.cancel()
        await pilot.pause()


def _model_info() -> dict:
    """A two-role model config (chat + plan, both modifiable)."""
    return {
        "chat": {
            "model": "openai:gpt-4o",
            "modifiable": True,
            "model_type": "text_generation",
            "credential": {"provider": "openai", "requires_key": True},
        },
        "plan": {
            "model": "openai:gpt-4o",
            "modifiable": True,
            "model_type": "text_generation",
            "credential": {"provider": "openai", "requires_key": True},
        },
    }


async def test_model_command_opens_dialog(fake_backend, make_app):
    """A client ``/model`` opens the model dialog and forwards nothing."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await _submit(pilot, app, "/model")
        await _settle(pilot, app)
        assert isinstance(app.screen, ModelDialog)

    assert fake_backend.calls == []


async def test_ctrl_m_opens_dialog(fake_backend, make_app):
    """Ctrl+M opens the model dialog."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        assert isinstance(app.screen, ModelDialog)


async def test_model_dialog_roles_are_compact(fake_backend, make_app):
    """Each role's editor sizes to its content (the button row is not 1fr)."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test(size=(100, 40)) as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        assert isinstance(app.screen, ModelDialog)
        # The per-role button row (a Horizontal) must not balloon the panel;
        # it is one button row, not a 1fr filler.
        for row in app.screen.query(Horizontal):
            assert row.size.height <= 3


async def test_model_dialog_save_promotes(fake_backend, make_app):
    """Saving a changed role posts a promoting per-chat override."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        assert isinstance(app.screen, ModelDialog)
        app.screen.query_one("#model-provider-chat", Input).value = "anthropic"
        app.screen.query_one("#model-model-chat", Input).value = "claude"
        await pilot.pause()
        app.screen.query_one("#model-save", Button).press()
        await _settle(pilot, app)

    assert fake_backend.model_overrides == [
        {
            "chat_id": "c1",
            "role": "chat",
            "payload": {"model": "anthropic:claude", "promote_default": True},
        }
    ]


async def test_model_dialog_reset(fake_backend, make_app):
    """Reset clears the role's per-chat override."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        app.screen.query_one("#model-reset-chat", Button).press()
        await _settle(pilot, app)

    assert fake_backend.model_clears == ["chat"]


async def test_model_dialog_use_for_all(fake_backend, make_app):
    """Use-for-all copies the model onto the other same-kind role."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        app.screen.query_one("#model-provider-chat", Input).value = "anthropic"
        app.screen.query_one("#model-model-chat", Input).value = "claude"
        await pilot.pause()
        app.screen.query_one("#model-useall-chat", Button).press()
        await pilot.pause()
        assert app.screen.query_one("#model-model-plan", Input).value == "claude"
        assert app.screen.query_one("#model-provider-plan", Input).value == "anthropic"


async def test_model_dialog_blocked_while_streaming(
    fake_backend, make_app, monkeypatch
):
    """``/model`` is refused while a run is streaming (I2)."""

    async def _blocking(query, chat_id, server_url, user_id="", **kwargs):
        yield {"type": "progress", "node": "n", "data": {"heading": "x"}}
        await asyncio.Event().wait()

    monkeypatch.setattr("klea_utils.api.sse.stream_events", _blocking)
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _submit(pilot, app, "go")
        await pilot.pause()
        await _submit(pilot, app, "/model")
        await pilot.pause()
        assert not isinstance(app.screen, ModelDialog)
        assert app._worker is not None
        app._worker.cancel()
        await pilot.pause()


async def test_model_dialog_credentials_save(fake_backend, make_app):
    """Saving a key from the credentials dialog posts it to the server."""
    fake_backend.model_info = _model_info()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        app.screen.query_one("#model-keys", Button).press()
        await _settle(pilot, app)
        assert isinstance(app.screen, CredentialsDialog)
        app.screen.query_one("#cred-key-0", Input).value = "sk-test"
        app.screen.query_one("#cred-save-0", Button).press()
        await _settle(pilot, app)

    assert fake_backend.credentials_set == [
        {"provider": "openai", "secret": "sk-test", "endpoint": ""}
    ]


async def test_model_dialog_credentials_clear(fake_backend, make_app):
    """A stored key can be cleared from the credentials dialog."""
    fake_backend.model_info = _model_info()
    fake_backend.credentials = [
        {"provider": "openai", "endpoint": "", "masked": "sk-***"}
    ]
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await pilot.press("ctrl+m")
        await _settle(pilot, app)
        app.screen.query_one("#model-keys", Button).press()
        await _settle(pilot, app)
        assert isinstance(app.screen, CredentialsDialog)
        app.screen.query_one("#cred-clear-0", Button).press()
        await _settle(pilot, app)

    assert fake_backend.credentials_cleared == [{"provider": "openai", "endpoint": ""}]


def _model_info_incomplete() -> dict:
    """A required chat role with no model configured."""
    return {"chat": {"model": "", "required": True, "credential": {}}}


def _model_info_missing_key() -> dict:
    """A required chat role whose provider has no stored credential."""
    return {
        "chat": {
            "model": "openai:gpt-4o",
            "required": True,
            "credential": {
                "provider": "openai",
                "requires_key": True,
                "source": "none",
            },
        }
    }


def _model_info_ready() -> dict:
    """A required chat role with a model and a resolved credential."""
    return {
        "chat": {
            "model": "openai:gpt-4o",
            "required": True,
            "credential": {
                "provider": "openai",
                "requires_key": True,
                "source": "env",
            },
        }
    }


async def test_send_blocked_when_model_missing(fake_backend, make_app):
    """A send is blocked while a required model is unset."""
    fake_backend.model_info = _model_info_incomplete()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await _submit(pilot, app, "hi")
        await pilot.pause()

    assert fake_backend.calls == []


async def test_send_blocked_when_credential_missing(fake_backend, make_app):
    """A send is blocked while a required API key is unset."""
    fake_backend.model_info = _model_info_missing_key()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await _submit(pilot, app, "hi")
        await pilot.pause()

    assert fake_backend.calls == []


async def test_send_allowed_when_configured(fake_backend, make_app):
    """A send proceeds once the required model and key are resolved."""
    fake_backend.model_info = _model_info_ready()
    app = make_app(chat_id="c1")
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        await _submit(pilot, app, "hi")
        await _settle(pilot, app)

    assert [call["query"] for call in fake_backend.calls] == ["hi"]


async def test_client_command_allowed_when_setup_incomplete(fake_backend, make_app):
    """A client command still runs while required models are missing."""
    fake_backend.model_info = _model_info_incomplete()
    fake_backend.commands = [_mode_metadata()]
    app = make_app()
    async with app.run_test() as pilot:
        await _settle(pilot, app)
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
        field = app.screen.query_one("#query-input", TextArea)
        field.text = "/m"
        await pilot.pause()
        suggestions = app.screen.query_one("#command-suggestions", CommandSuggestions)
        assert suggestions.display is True
        assert [option.id for option in suggestions.options] == ["mode", "model"]

        field.text = "hi"
        await pilot.pause()
        assert suggestions.display is False


async def test_query_input_grows_then_caps(fake_backend, make_app):
    """The query input grows with its lines up to a capped height."""
    app = make_app()
    async with app.run_test(size=(80, 24)) as pilot:
        await _settle(pilot, app)
        frame = app.screen.query_one(QueryInput)
        field = app.screen.query_one("#query-input", TextArea)
        field.text = "one line"
        await pilot.pause()
        short = frame.size.height
        # One row of vertical padding around the single-row entry.
        assert frame.styles.padding.top >= 1
        assert frame.styles.padding.bottom >= 1
        field.text = "\n".join(f"line {i}" for i in range(30))
        await pilot.pause()
        tall = frame.size.height
        assert short < tall
        assert field.size.height <= 8  # the entry caps...
        assert field.max_scroll_y > 0  # ...and scrolls past the cap


async def test_query_input_ctrl_j_inserts_newline(fake_backend, make_app):
    """Ctrl+J inserts a newline instead of submitting the query."""
    app = make_app()
    async with app.run_test() as pilot:
        await _settle(pilot, app)
        field = app.screen.query_one("#query-input", TextArea)
        field.focus()
        field.text = "a"
        await pilot.press("ctrl+j")
        await pilot.pause()
        assert field.text.count("\n") == 1
        assert fake_backend.calls == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
