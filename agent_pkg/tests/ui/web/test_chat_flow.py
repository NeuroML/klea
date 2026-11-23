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


async def test_user_message_is_rendered_verbatim(fake_backend, agent_user):
    """User input is not Markdown-decorated (underscores, '#', newlines kept)."""
    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "my_file_name.py\n# not a heading")
    await agent_user.should_see("my_file_name.py")
    await agent_user.should_see("# not a heading")
    await agent_user.should_see("Run complete!", retries=50)


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
    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    agent_user.find("Answer").click()
    await agent_user.should_see("Run complete!", retries=50)

    assert any("interrupt_response" in body for body in fake_backend.stream_bodies())


async def test_input_interrupt_renders_questions_without_plan_history(
    fake_backend, agent_user
):
    """An input ask shows its question without abandoned plan versions."""
    fake_backend.stream_events = [
        {
            "type": "interrupt",
            "node": "Awaiting input",
            "data": {
                "kind": "input",
                "questions": [{"step_number": 2, "question": "how should I proceed?"}],
                "interrupt_id": "i1",
            },
        }
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("User input", retries=50)
    await agent_user.should_not_see("Plan version 1 (abandoned)")
    await agent_user.should_not_see("Abandoned: tool failed")
    await agent_user.should_see("how should I proceed?", retries=50)


async def test_interrupt_replaces_the_composer(fake_backend, agent_user):
    """While an ask is active the composer is hidden, and restored after."""
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
    # The ask replaces the composer.
    await agent_user.should_not_see(kind=ui.textarea)

    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    agent_user.find("Answer").click()
    await agent_user.should_see("Run complete!", retries=50)
    await agent_user.should_see(kind=ui.textarea)


async def test_review_interrupt_renders_headline_and_sections(fake_backend, agent_user):
    """A review checkpoint renders the adaptive headline and sections."""
    fake_backend.stream_events = [
        {
            "type": "interrupt",
            "node": "Awaiting review",
            "data": {
                "kind": "review",
                "headline": "Review the work done and approve the remaining 1 step",
                "question": "Approve, request changes, or cancel.",
                "sections": [
                    {
                        "title": "Completed since the last review (step 1)",
                        "body": "[x] Step 1: copy the file\n    - run_command: ok",
                    },
                    {"title": "Remaining (1 step)", "body": "[ ] Step 3: write"},
                ],
                "interrupt_id": "r1",
            },
        }
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Plan review", retries=50)
    await agent_user.should_see("Review the work done", retries=50)
    await agent_user.should_see("Step 1: copy the file", retries=50)
    await agent_user.should_see("run_command: ok", retries=50)
    await agent_user.should_see("Remaining (1 step)", retries=50)

    # Titles render verbatim: no synthetic "Option N" prefix is added.
    labels = [
        str(getattr(element, "text", ""))
        for element in agent_user.client.elements.values()
    ]
    assert not any("Option " in text for text in labels)

    # The ask offers the current review controls, not the retired wording.
    await agent_user.should_see("Continue", retries=50)
    await agent_user.should_see("Revise with feedback", retries=50)
    await agent_user.should_not_see("Request changes")
    assert "Request changes" not in labels
    # The feedback entry prompts for the revise path.
    placeholders = [
        getattr(element, "_props", {}).get("placeholder")
        for element in agent_user.client.elements.values()
        if isinstance(element, ui.textarea)
    ]
    assert "Feedback (required to revise)" in placeholders

    # The ask body is scoped (ADR-0051): the ask column carries ``hitl-ask``,
    # its feedback textarea the ``chat-input`` class, and every rendered
    # section body the ask-body classes, so the transcript's global code-block
    # and chat-input styling stay untouched.
    elements = list(agent_user.client.elements.values())
    assert any("hitl-ask" in getattr(element, "_classes", ()) for element in elements)
    assert any(
        isinstance(element, ui.textarea)
        and "chat-input" in getattr(element, "_classes", ())
        for element in elements
    )
    bodies = [element for element in elements if isinstance(element, ui.code)]
    assert bodies, "the review sections render as code bodies"
    for body in bodies:
        assert "hitl-ask-body" in body._classes
        assert "nicegui-code-noformat" in body._classes


async def test_generic_tool_event_renders_header_and_payload(fake_backend, agent_user):
    """A non-run_command tool entry renders its header and payload in chat."""
    fake_backend.stream_events = [
        {
            "type": "tool",
            "node": "Running tools",
            "data": {
                "tools": [
                    {
                        "tool": "read_file",
                        "title": "Read file",
                        "header": "Read file",
                        "mime": "text/x-shell",
                        "data": 'read_file(path="notes.txt")\nhello from notes',
                        "meta": {},
                        "display": 'read_file(path="notes.txt")\nhello from notes',
                        "is_error": False,
                    }
                ]
            },
        },
        {"type": "complete", "message_for_user": "Done"},
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Read file", retries=50)
    await agent_user.should_see("hello from notes", retries=50)


def _permission_form_separators(user):
    """Return the dividers the permission form itself draws."""
    separators = []
    for element in user.client.elements.values():
        if not isinstance(element, ui.separator):
            continue
        slot = getattr(element, "parent_slot", None)
        while slot is not None:
            parent = getattr(slot, "parent", None)
            if parent is None:
                break
            if "border-primary" in getattr(parent, "_classes", ()):
                separators.append(element)
                break
            slot = getattr(parent, "parent_slot", None)
    return separators


def test_permission_step_heading_caps_a_long_summary():
    """An over-long step summary wraps to two lines and ends in ``...``."""
    from klea_utils.ui.web.nicegui.components.chat_area import (
        MAX_PERMISSION_STEP_HEADING_LINE_CHARS,
        MAX_PERMISSION_STEP_HEADING_LINES,
        _permission_step_heading,
    )

    heading = _permission_step_heading(1, " ".join(["word"] * 80))
    lines = heading.splitlines()
    assert lines[0].startswith("Step 1: word")
    assert len(lines) == MAX_PERMISSION_STEP_HEADING_LINES
    assert lines[-1].endswith("...")
    for line in lines:
        assert len(line) <= MAX_PERMISSION_STEP_HEADING_LINE_CHARS


async def test_permission_step_context_renders(fake_backend, agent_user):
    """A permission ask shows the plan-step context it carries."""
    event = _permission_event(
        {"kind": "outside", "path": "/etc/hosts", "directory": "/etc"}
    )
    event["data"]["steps"] = [{"step_number": 1, "summary": "Read the hosts file"}]
    fake_backend.stream_events = [event]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    await agent_user.should_see("Step 1: Read the hosts file")

    # The heading is a bold header set apart by a blank line (``mt-2``), not
    # muted hint text like the tool-call context beneath the path.
    headings = [
        element
        for element in agent_user.client.elements.values()
        if str(getattr(element, "text", "")) == "Step 1: Read the hosts file"
    ]
    assert headings, "the step heading label is rendered"
    for heading in headings:
        assert "font-bold" in heading._classes
        assert "mt-2" in heading._classes
    # The wrap class keeps a capped, multi-line summary readable.
    assert all("whitespace-pre-line" in heading._classes for heading in headings)
    # A single group draws no divider at all.
    assert _permission_form_separators(agent_user) == []


async def test_permission_requests_grouped_by_step(fake_backend, agent_user):
    """Per-request step context groups each path under its step heading."""
    event = {
        "type": "interrupt",
        "node": "Running tools",
        "data": {
            "kind": "permission",
            "question": "Allow the agent to access these paths?",
            "requests": [
                {
                    "kind": "outside",
                    "path": "/etc/hosts",
                    "directory": "/etc",
                    "steps": [{"step_number": 2, "summary": "Edit the hosts file"}],
                },
                {
                    "kind": "outside",
                    "path": "/etc/passwd",
                    "directory": "/etc",
                    "steps": [{"step_number": 1, "summary": "Read the passwd file"}],
                },
                {
                    "kind": "outside",
                    "path": "/etc/group",
                    "directory": "/etc",
                    "steps": [
                        {"step_number": 1, "summary": "Read the passwd file"},
                        {"step_number": 2, "summary": "Edit the hosts file"},
                    ],
                },
                {"kind": "outside", "path": "/tmp/x", "directory": "/tmp"},
            ],
            "steps": [],
            "interrupt_id": "p1",
        },
    }
    fake_backend.stream_events = [event]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    # One heading per plan step, with that step's path rows beneath it.
    await agent_user.should_see("Step 1: Read the passwd file", retries=50)
    await agent_user.should_see("Step 2: Edit the hosts file", retries=50)

    # Every group heading is a bold header with a blank line above it.
    headings = [
        element
        for element in agent_user.client.elements.values()
        if str(getattr(element, "text", ""))
        in {"Step 1: Read the passwd file", "Step 2: Edit the hosts file"}
    ]
    assert {str(heading.text) for heading in headings} == {
        "Step 1: Read the passwd file",
        "Step 2: Edit the hosts file",
    }
    for heading in headings:
        assert "font-bold" in heading._classes
        assert "mt-2" in heading._classes
    await agent_user.should_see("Outside project path: /etc/passwd", retries=50)
    await agent_user.should_see("Outside project path: /etc/hosts", retries=50)
    # A request serving several steps sits under its lowest and says so.
    await agent_user.should_see("Outside project path: /etc/group", retries=50)
    await agent_user.should_see("Also serves step 2", retries=50)
    # The hint is drawn once beneath its step group, never inside a path row.
    also_labels = [
        element
        for element in agent_user.client.elements.values()
        if str(getattr(element, "text", "")) == "Also serves step 2"
    ]
    assert len(also_labels) == 1
    # Each group carries one muted coverage line summarising its paths.
    await agent_user.should_see("1 path: 1 outside the project", retries=50)
    # Adjacent groups are separated by a single divider (three groups here).
    assert len(_permission_form_separators(agent_user)) >= 2
    # A request without step context falls into the trailing bucket.
    await agent_user.should_see("Other paths", retries=50)
    await agent_user.should_see("Outside project path: /tmp/x", retries=50)


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
                "argument": "path",
                "raw": "/etc/hosts",
                "argument_preview": "/etc/hosts",
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
    await agent_user.should_see("Outside project path: /etc/hosts")
    await agent_user.should_see("Session approval covers: /etc")

    agent_user.find("Session").click()
    await asyncio.sleep(0.1)
    # The form re-renders on select, so the chosen option is highlighted.
    active = [
        element
        for element in agent_user.find("Session").elements
        if "choice-btn--active" in list(element.classes)
    ]
    assert active, "the selected option should be highlighted"

    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    agent_user.find("Submit").click()
    await agent_user.should_see("Run complete!", retries=50)

    body = next(
        body for body in fake_backend.stream_bodies() if "interrupt_response" in body
    )
    assert body["interrupt_response"]["decisions"] == [
        {"key": "/etc", "decision": "session"}
    ]


async def test_permission_long_path_is_truncated(fake_backend, agent_user):
    """A pathological guessed path is middle-truncated in the ask."""
    long_path = "/home/user/" + ("a" * 400) + "/file.txt"
    fake_backend.stream_events = [
        _permission_event(
            {
                "kind": "outside",
                "tool": "read_file",
                "argument": "path",
                "raw": long_path,
                "raw_preview": "... " + long_path[:80] + " ...",
                "argument_preview": "... " + long_path[:80] + " ...",
                "path": long_path,
                "path_display": "/home/user/aaa... [400 chars omitted] .../file.txt",
                "directory": "/home/user",
                "directory_display": "/home/user",
                "confidence": "guess",
                "source": "name:path",
            }
        )
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    await agent_user.should_see("Outside project path: /home/user/aaa")
    await agent_user.should_see("chars omitted")
    await agent_user.should_see("Session approval covers: /home/user")


async def test_permission_coverage_lists_all_paths(fake_backend, agent_user):
    """A directory approval lists every path it covers."""
    fake_backend.stream_events = [
        _permission_event(
            {
                "kind": "outside",
                "tool": "run_command",
                "argument": "command",
                "raw": "/tmp/a",
                "argument_preview": "tee /tmp/a /tmp/b",
                "path": "/tmp/a",
                "directory": "/tmp",
                "confidence": "guess",
                "source": "value:command",
                "covered_paths": ["/tmp/a", "/tmp/b"],
                "covered_paths_display": ["/tmp/a", "/tmp/b"],
            }
        )
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    await agent_user.should_see("Covers:")
    await agent_user.should_see("/tmp/a")
    await agent_user.should_see("/tmp/b")


async def test_state_edit_interrupt_renders_and_saves(fake_backend, agent_user):
    """A generic state-edit ask renders its fields and submits their values."""
    fake_backend.stream_events = [
        {
            "type": "interrupt",
            "node": "Running command",
            "data": {
                "kind": "state_edit",
                "title": "Manage paths",
                "description": "Session only",
                "fields": [
                    {
                        "key": "allowed_dirs",
                        "label": "Allowed directories",
                        "kind": "path_list",
                        "value": ["/tmp/a", "/tmp/b"],
                        "addable": True,
                        "removable": True,
                    },
                    {
                        "key": "note",
                        "label": "Note",
                        "kind": "text",
                        "value": "old",
                    },
                ],
                "interrupt_id": "s1",
            },
        }
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Manage paths", retries=50)
    await agent_user.should_see("Allowed directories")
    await agent_user.should_see("/tmp/a")
    await agent_user.should_see("/tmp/b")

    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Updated paths."}
    ]
    agent_user.find("Save").click()
    await agent_user.should_see("Updated paths.", retries=50)

    body = next(
        body for body in fake_backend.stream_bodies() if "interrupt_response" in body
    )
    assert body["interrupt_response"] == {
        "action": "save",
        "values": {"allowed_dirs": ["/tmp/a", "/tmp/b"], "note": "old"},
    }


async def test_permission_guess_offers_not_a_path(fake_backend, agent_user):
    """A guessed path value offers a "Not a path" dismissal that submits."""
    fake_backend.stream_events = [
        _permission_event(
            {
                "kind": "outside",
                "tool": "run_command",
                "argument": "command",
                "raw": "/tmp/",
                "raw_preview": "/tmp/",
                "argument_preview": "echo /tmp/",
                "path": "/tmp",
                "directory": "/tmp",
                "confidence": "guess",
                "source": "value:command",
            }
        )
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    # The tool call is shown for context, and the guess offers "Not a path".
    await agent_user.should_see("run_command")
    await agent_user.should_see("echo /tmp/")
    await agent_user.should_see("Source: value:command")
    await agent_user.should_see("Not a path")

    agent_user.find("Not a path").click()
    await asyncio.sleep(0.1)

    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    agent_user.find("Submit").click()
    await agent_user.should_see("Run complete!", retries=50)

    body = next(
        body for body in fake_backend.stream_bodies() if "interrupt_response" in body
    )
    assert body["interrupt_response"]["decisions"] == [
        {"key": "/tmp", "decision": "not_a_path"}
    ]


async def test_permission_hint_escapes_html_and_marks_the_token(
    fake_backend, agent_user
):
    """The context hint escapes model text and marks only the detected token.

    The argument preview is model-provided, so an embedded ``<script>`` must
    reach the page escaped; the token span from
    ``preview_token_start``/``preview_token_end`` is wrapped in ``<mark>``.
    """
    preview = "echo <script> /tmp/out.txt"
    token = "/tmp/out.txt"
    start = preview.index(token)
    fake_backend.stream_events = [
        _permission_event(
            {
                "kind": "outside",
                "tool": "run_command",
                "argument": "command",
                "raw": token,
                "argument_preview": preview,
                "preview_token_start": start,
                "preview_token_end": start + len(token),
                "preview_omitted_before": 0,
                "preview_omitted_after": 0,
                "argument_length": len(preview),
                "token_line": 1,
                "path": token,
                "directory": "/tmp",
                "confidence": "guess",
                "confidence_reason": "heuristic: found in the argument value",
                "source": "value:command",
            }
        )
    ]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("Permission required", retries=50)
    await agent_user.should_see("heuristic: found in the argument value")

    markup = "\n".join(
        str(element.content) for element in agent_user.find(ui.html).elements
    )

    assert "<mark><code>/tmp/out.txt</code></mark>" in markup
    assert "&lt;script&gt;" in markup
    assert "<script>" not in markup


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

    fake_backend.stream_events = [
        {"type": "complete", "message_for_user": "Run complete!"}
    ]
    agent_user.find("Submit").click()
    await agent_user.should_see("Run complete!", retries=50)

    body = next(
        body for body in fake_backend.stream_bodies() if "interrupt_response" in body
    )
    assert body["interrupt_response"]["decisions"] == [
        {"key": "/proj/.env", "decision": "deny"}
    ]


async def test_chat_list_spinner_clears_after_run(fake_backend, agent_user):
    """The chat-list run indicator stops spinning once the run completes."""
    fake_backend.stream_events = [{"type": "complete", "message_for_user": "done"}]
    await agent_user.open("/")
    await agent_user.should_not_see("Backend is starting")
    _send(agent_user, "Hello")
    await agent_user.should_see("done", retries=50)
    await asyncio.sleep(0.1)

    # No spinner should remain (the list re-renders to the idle icon).
    with pytest.raises(AssertionError):
        agent_user.find(ui.spinner)
