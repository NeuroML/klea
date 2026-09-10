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


@pytest.mark.asyncio
async def test_answer_user_reports_assurance(monkeypatch):
    """The response info carries the structured assurance label."""
    node = AnswerUser(logging.getLogger("test"), "Preparing response")
    emitted: list[dict] = []
    monkeypatch.setattr(node, "write_custom_stream", emitted.append)

    state = KleaAgentState(message_for_user="done", mode=Mode(assurance="unverified"))
    result = await node.execute(state)

    assert result["message_for_user"] == "done"
    info = next(e for e in emitted if e["type"] == "info")
    assert info["data"]["details"]["assurance"] == "unverified"
