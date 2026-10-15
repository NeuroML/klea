#!/usr/bin/env python3
"""
Tests for the server-message to frontend-message mapping.

``server_message_to_data`` (``klea_utils/ui/client.py``) rebuilds the
frontend ``MessageData`` shape from a ``GET .../messages`` row; a ``tool``
row must round-trip its chat-block fields (header/mime/data/meta/is_error)
from the stored ``metadata`` so a reloaded chat matches the live stream.

File: tests/test_client_messages.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.ui.client import server_message_to_data


def test_user_and_agent_rows_map_to_roles():
    """Non-tool roles map to ``user``/``agent`` with no tool fields."""
    user = server_message_to_data({"role": "user", "content": "hi", "created_at": 0})
    assert user["role"] == "user"
    assert user["text"] == "hi"
    assert "mime" not in user

    agent = server_message_to_data(
        {"role": "assistant", "content": "yo", "created_at": 0}
    )
    assert agent["role"] == "agent"
    assert agent["text"] == "yo"


def test_tool_row_rebuilds_the_block_from_metadata():
    """A tool row's block fields are restored from its stored metadata."""
    row = {
        "role": "tool",
        "content": "edit_file diff",
        "created_at": 0,
        "metadata": {
            "header": "edit_file",
            "mime": "text/x-diff",
            "data": "--- a\n+++ b\n",
            "meta": {"k": "v"},
            "is_error": True,
        },
    }
    data = server_message_to_data(row)
    assert data["role"] == "tool"
    assert data["text"] == "edit_file diff"
    assert data["header"] == "edit_file"
    assert data["mime"] == "text/x-diff"
    assert data["data"] == "--- a\n+++ b\n"
    assert data["meta"] == {"k": "v"}
    assert data["is_error"] is True


def test_tool_row_defaults_when_metadata_is_absent():
    """A tool row with no metadata still maps to a valid tool message."""
    data = server_message_to_data({"role": "tool", "content": "out", "created_at": 0})
    assert data["role"] == "tool"
    assert data["header"] == ""
    assert data["mime"] == ""
    assert data["data"] == ""
    assert data["meta"] == {}
    assert data["is_error"] is False
