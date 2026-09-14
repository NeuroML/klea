#!/usr/bin/env python3
"""
Tests for the agent AnswerUser node.

File: tests/test_answer_user.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

import pytest
from klea_agent.nodes.answer_user import AnswerUser
from klea_agent.schemas import KleaAgentState, Mode
from klea_utils.nodes.base import EMPTY_RESULT_FALLBACK


@pytest.mark.asyncio
async def test_answer_user_reports_assurance(monkeypatch):
    """The response info carries the structured assurance label."""
    node = AnswerUser(logging.getLogger("test"), "Preparing response")
    emitted: list[dict] = []
    monkeypatch.setattr(node, "write_custom_stream", emitted.append)

    state = KleaAgentState(message_for_user="done", mode=Mode(assurance="unverified"))
    result = await node.execute(state)

    assert result["message_for_user"] == "done"
    assert result["messages"][-1].content == "done"
    info = next(e for e in emitted if e["type"] == "info")
    assert info["data"]["details"]["assurance"] == "unverified"


@pytest.mark.asyncio
async def test_answer_user_falls_back_on_blank_message(monkeypatch):
    """A blank ``message_for_user`` is never delivered as an empty reply."""
    node = AnswerUser(logging.getLogger("test"), "Preparing response")
    monkeypatch.setattr(node, "write_custom_stream", lambda event: None)

    state = KleaAgentState(message_for_user="   ")
    result = await node.execute(state)

    assert result["message_for_user"] == EMPTY_RESULT_FALLBACK
    assert result["messages"][-1].content == EMPTY_RESULT_FALLBACK
