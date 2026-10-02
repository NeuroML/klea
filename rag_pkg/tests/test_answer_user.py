#!/usr/bin/env python3
"""
Tests for the RAG answer user node.

File: rag_pkg/tests/test_answer_user.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_rag.nodes.answer_user import AnswerUser
from klea_rag.schemas import RAGState
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


def _make_node() -> AnswerUser:
    node = object.__new__(AnswerUser)
    node.logger = logging.getLogger("test_answer_user")
    node.label = "Preparing response"
    node.write_custom_stream = lambda event: None
    return node


async def test_answer_user_continue_no_warning():
    """A clean 'continue' verdict returns the answer untouched."""
    node = _make_node()
    state = RAGState(
        messages=[AIMessage(content="a real answer")],
        route="continue",
    )

    result = await node.execute(state)

    assert result["message_for_user"] == "a real answer"
    assert AnswerUser.BEST_EFFORT_WARNING not in result["message_for_user"]


async def test_answer_user_best_effort_appends_warning():
    """A best-effort delivery appends the hardcoded warning."""
    node = _make_node()
    state = RAGState(
        messages=[AIMessage(content="a partial answer")],
        route="best_effort",
    )

    result = await node.execute(state)

    assert "a partial answer" in result["message_for_user"]
    assert AnswerUser.BEST_EFFORT_WARNING in result["message_for_user"]
