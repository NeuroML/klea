#!/usr/bin/env python3
"""
Tests for the agent chat flow driven through the page.

Sends a turn via the chat input against a scripted ``/query/stream``
response and asserts what the user sees: the answer, a resumable error's
Retry action, and the human-in-the-loop form (with the answer carried on
the follow-up request).  Uses the in-process user simulation and fake
backend from ``conftest.py``.

File: tests/ui/web/test_chat_flow.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from nicegui import ui


def _send(user, text: str) -> None:
    """Type *text* into the chat input and press Enter."""
    user.find(ui.textarea).type(text)
    user.find(ui.textarea).trigger("keydown.enter.exact.prevent")


async def test_send_renders_the_answer(fake_backend, agent_user):
    """A completed turn shows the user message and the answer."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "Hi there"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Hello")
    await agent_user.should_see("Hi there", retries=50)


async def test_selected_mode_and_access_are_sent(fake_backend, agent_user):
    """The selected mode and access level ride on the next query."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "first"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    # The first turn creates the chat (default selection).
    _send(agent_user, "Hello")
    await agent_user.should_see("first", retries=50)

    # Select for the chat's next turn.
    agent_user.find("Scientific").click()
    agent_user.find("Read-only").click()
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "second"}]
    _send(agent_user, "Again")
    await agent_user.should_see("second", retries=50)

    body = fake_backend.stream_bodies()[-1]
    assert body["mode"] == "scientific"
    assert body["access_level"] == "read_only"


@pytest.mark.xfail(
    reason=(
        "Selecting a mode/access level before the first message is lost: the "
        "chat is created on send, then run_stream refreshes the status pane, "
        "which resolves the new chat's default and overwrites query_extra "
        "before the request is sent."
    ),
    strict=True,
)
async def test_first_message_carries_a_preselected_mode(fake_backend, agent_user):
    """A mode chosen before the first message should ride on that first query."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "done"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find("Scientific").click()
    _send(agent_user, "Hello")
    await agent_user.should_see("done", retries=50)

    assert fake_backend.stream_bodies()[-1]["mode"] == "scientific"


async def test_resumable_error_renders_retry(fake_backend, agent_user):
    """A resumable stream error renders the inline Retry action."""
    fake_backend.stream_events = [
        {
            "type": "error",
            "message": "boom",
            "error_type": "RuntimeError",
            "node": "Planner",
            "resumable": True,
        }
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Retry", retries=50)


async def test_interrupt_renders_form_and_answer_is_sent(fake_backend, agent_user):
    """A paused run renders the ask; answering sends ``interrupt_response``."""
    fake_backend.stream_events = [
        {
            "type": "interrupt",
            "node": "Awaiting input",
            "data": {
                "kind": "input",
                "questions": [{"step_number": 1, "question": "which file?"}],
                "interrupt_id": "i1",
            },
        }
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("which file?", retries=50)
    await agent_user.should_see("Answer")

    # Answering starts a follow-up run carrying the resume mapping.
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "ok"}]
    agent_user.find("Answer").click()
    await agent_user.should_see("ok", retries=50)

    assert any("interrupt_response" in body for body in fake_backend.stream_bodies())
