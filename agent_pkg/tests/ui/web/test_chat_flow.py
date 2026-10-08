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

import asyncio

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


async def test_first_message_carries_preselected_controls(fake_backend, agent_user):
    """A mode and access level chosen before the first message ride on it."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "done"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    agent_user.find("Scientific").click()
    agent_user.find("Read-only").click()
    _send(agent_user, "Hello")
    await agent_user.should_see("done", retries=50)

    body = fake_backend.stream_bodies()[-1]
    assert body["mode"] == "scientific"
    assert body["access_level"] == "read_only"


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


def _permission_event(request: dict) -> dict:
    return {
        "type": "interrupt",
        "node": "Running tools",
        "data": {
            "kind": "permission",
            "question": "Allow the agent to access these paths?",
            "requests": [request],
            "interrupt_id": "p1",
        },
    }


async def test_permission_interrupt_outside_renders_and_submits(
    fake_backend, agent_user
):
    """An outside-path ask renders choices; Submit sends the decision."""
    fake_backend.stream_events = [
        _permission_event(
            {
                "kind": "outside",
                "tool": "read_file",
                "path": "/etc/hosts",
                "directory": "/etc",
                "confidence": "declared",
                "source": "checkpaths:path",
            }
        )
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    await agent_user.should_see("/etc")

    agent_user.find("Session").click()
    await asyncio.sleep(0.1)
    # The form re-renders on select, so the chosen option is highlighted.
    active = [
        element
        for element in agent_user.find("Session").elements
        if "choice-btn--active" in list(element.classes)
    ]
    assert active, "the selected option should be highlighted"

    fake_backend.stream_events = [{"type": "complete", "message_for_user": "ok"}]
    agent_user.find("Submit").click()
    await agent_user.should_see("ok", retries=50)

    body = next(
        body for body in fake_backend.stream_bodies() if "interrupt_response" in body
    )
    assert body["interrupt_response"]["decisions"] == [
        {"key": "/etc", "decision": "session"}
    ]


async def test_permission_interrupt_sensitive_renders_file(fake_backend, agent_user):
    """A sensitive-file ask names the file; the default decision is deny."""
    fake_backend.stream_events = [
        _permission_event(
            {
                "kind": "sensitive",
                "tool": "read_file",
                "path": "/proj/.env",
                "directory": "/proj",
                "confidence": "declared",
                "source": "checkpaths:path",
            }
        )
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Sensitive file: /proj/.env", retries=50)

    fake_backend.stream_events = [{"type": "complete", "message_for_user": "ok"}]
    agent_user.find("Submit").click()
    await agent_user.should_see("ok", retries=50)

    body = next(
        body for body in fake_backend.stream_bodies() if "interrupt_response" in body
    )
    assert body["interrupt_response"]["decisions"] == [
        {"key": "/proj/.env", "decision": "deny"}
    ]
