#!/usr/bin/env python3
"""
Tests for the generic state-edit HITL helper.

File: tests/test_commands_state_edit.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from klea_utils.commands.state_edit import (
    StateEditRequest,
    StateField,
    edit_state_fields,
)


def test_state_field_defaults():
    field = StateField(key="name", label="Name")
    assert field.kind == "text"
    assert field.value is None
    assert field.addable is True
    assert field.removable is True
    assert field.options == []


def test_state_edit_request_serialises_fields():
    request = StateEditRequest(
        title="Manage paths",
        description="Session only",
        fields=[
            StateField(
                key="allowed_dirs",
                label="Allowed directories",
                kind="path_list",
                value=["/tmp"],
            )
        ],
    )

    data = request.model_dump()
    assert data["kind"] == "state_edit"
    assert data["title"] == "Manage paths"
    assert data["fields"][0]["key"] == "allowed_dirs"
    assert data["fields"][0]["value"] == ["/tmp"]


def test_edit_state_fields_returns_saved_values():
    seen: list[dict] = []

    def interrupt_fn(payload):
        seen.append(payload)
        return {"action": "save", "values": {"allowed_dirs": ["/tmp"]}}

    values = edit_state_fields(
        title="Manage paths",
        description="Session only",
        fields=[
            StateField(
                key="allowed_dirs",
                label="Allowed directories",
                kind="path_list",
                value=[],
            )
        ],
        interrupt_fn=interrupt_fn,
    )

    assert values == {"allowed_dirs": ["/tmp"]}
    assert seen[0]["kind"] == "state_edit"
    assert seen[0]["fields"][0]["kind"] == "path_list"


def test_edit_state_fields_cancel_returns_none():
    for response in (None, {"action": "cancel"}, {"action": "cancel", "values": {}}):
        values = edit_state_fields(
            title="Manage paths",
            fields=[],
            interrupt_fn=lambda _payload, response=response: response,
        )
        assert values is None


def test_edit_state_fields_invalid_response_returns_none():
    values = edit_state_fields(
        title="Manage paths",
        fields=[],
        interrupt_fn=lambda _payload: {"action": "bogus", "values": {}},
    )
    assert values is None
