#!/usr/bin/env python3
"""
Tests for the shared NiceGUI stream event application logic.

The :func:`apply_stream_event` function is pure (mutates the chat dict
only), so it is unit-tested without any NiceGUI rendering.

File: tests/test_stream_events.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_utils.ui.web.nicegui.components.stream import (
    INSPECTOR_BUFFER_KEY,
    apply_stream_event,
)
from klea_utils.ui.web.nicegui.state import chats, ensure_chat


@pytest.fixture
def chat():
    """A fresh chat dict via the shared ``ensure_chat`` store."""
    chats.clear()
    return ensure_chat("test-user", "test-chat")


class TestApplyStreamEvent:
    """Unit tests for :func:`apply_stream_event`."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    def test_progress_ignored(self, chat):
        """Progress events mutate nothing and return None."""
        result = apply_stream_event(chat, {"type": "progress", "node": "Planner"})
        assert result is None
        assert chat["messages"] == []
        assert chat["token_usage"]["total_tokens"] == 0

    def test_info_and_token_ignored(self, chat):
        """info and token events mutate nothing."""
        assert apply_stream_event(chat, {"type": "info", "data": {}}) is None
        assert apply_stream_event(chat, {"type": "token", "content": "hi"}) is None

    def test_debug_buffers_inspector_entry(self, chat):
        """debug events append to the inspector buffer."""
        result = apply_stream_event(
            chat,
            {
                "type": "debug",
                "node": "Planner",
                "data": {
                    "heading": "Plan",
                    "summary": "Made a plan",
                    "details": {"steps": 3},
                    "timing_seconds": 1.2,
                },
            },
        )
        assert result == "debug"
        assert len(chat[INSPECTOR_BUFFER_KEY]) == 1
        entry = chat[INSPECTOR_BUFFER_KEY][0]
        assert entry["heading"] == "Plan"
        assert entry["summary"] == "Made a plan"
        assert entry["details"] == {"steps": 3}
        assert entry["timing_seconds"] == 1.2
        assert entry["node"] == "Planner"

    def test_usage_accumulates_tokens(self, chat):
        """usage events accumulate token totals across events."""
        assert (
            apply_stream_event(
                chat,
                {
                    "type": "usage",
                    "node": "Planner",
                    "data": {
                        "details": {
                            "input_tokens": 10,
                            "output_tokens": 5,
                            "total_tokens": 15,
                        }
                    },
                },
            )
            == "usage"
        )
        assert (
            apply_stream_event(
                chat,
                {
                    "type": "usage",
                    "node": "Answer",
                    "data": {
                        "details": {
                            "input_tokens": 2,
                            "output_tokens": 8,
                            "total_tokens": 10,
                        }
                    },
                },
            )
            == "usage"
        )
        assert chat["token_usage"] == {
            "input_tokens": 12,
            "output_tokens": 13,
            "total_tokens": 25,
        }

    def test_state_section_stored(self, chat):
        """state events store per-node status-pane sections."""
        result = apply_stream_event(
            chat,
            {
                "type": "state",
                "node": "Retrieve",
                "data": {
                    "heading": "Retrieval",
                    "display": "- 2 docs",
                    "summary": "",
                    "details": {},
                },
            },
        )
        assert result == "state"
        assert chat["state_sections"]["Retrieve"] == {
            "heading": "Retrieval",
            "display": "- 2 docs",
            "summary": "",
            "details": {},
        }

    def test_complete_appends_message(self, chat):
        """complete events append the final assistant message."""
        result = apply_stream_event(
            chat, {"type": "complete", "message_for_user": "Final answer"}
        )
        assert result == "complete"
        assert len(chat["messages"]) == 1
        text, stamp, is_user = chat["messages"][0]
        assert text == "Final answer"
        assert stamp
        assert is_user is False

    def test_unknown_type_ignored(self, chat):
        """Unknown event types mutate nothing and return None."""
        assert apply_stream_event(chat, {"type": "mystery"}) is None

    def test_error_action(self, chat):
        """error events map to the error action without mutation."""
        assert apply_stream_event(chat, {"type": "error", "message": "boom"}) == "error"
        assert chat["messages"] == []
