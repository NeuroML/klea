#!/usr/bin/env python3
"""
Tests for RAG compatibility with the shared memory summariser.

File: tests/test_memory.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_rag.schemas import RAGState
from klea_utils.nodes.context import LLMNodeContext, NodeContext
from klea_utils.nodes.summarise_memory import SummariseMemoryNode
from langchain_core.messages import AIMessage, HumanMessage


def _conversation() -> list:
    return [
        message
        for index in range(3)
        for message in (
            HumanMessage(content=f"user question {index}"),
            AIMessage(content=f"assistant answer {index}"),
        )
    ]


def _node() -> SummariseMemoryNode:
    return SummariseMemoryNode(
        logger=logging.getLogger("test_rag_memory"),
        label="Summarizing history",
        llm_models={"chat": None},
        summarisation_threshold_chars=1,
        num_history_chars=50,
    )


def test_rag_summarisation_has_no_internal_stream():
    """RAG has no ``messages_internal``; the shared summariser still works."""
    node = _node()
    state = RAGState(messages=_conversation(), summarised_till=0)

    assert node._pre_exec(state, NodeContext()) is True

    variables = node._get_prompt_variables(state, LLMNodeContext())
    assert variables["internal_history"] == ""

    updates = node._update_state(
        AIMessage(content="a summary"), state, LLMNodeContext()
    )
    assert "summarised_till" in updates
    assert "summarised_internal_till" not in updates
