#!/usr/bin/env python3
"""
Tests for the RAG Evaluator node prompt variables.

File: tests/test_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_rag.nodes.evaluator import Evaluator
from klea_rag.schemas import RAGState
from langchain_core.messages import AIMessage


def _node() -> Evaluator:
    return Evaluator(
        logger=logging.getLogger("test_evaluator"),
        label="Evaluating",
        llm_models={"chat": None},
    )


def test_empty_context_renders_sentinel():
    """An empty retrieval renders a sentinel, not an empty context label."""
    state = RAGState(query="q", messages=[AIMessage(content="an answer")])
    variables = _node()._get_prompt_variables(state)
    assert variables["context"] == "(no context)"
    assert variables["question"] == "q"
    assert variables["answer"] == "an answer"
