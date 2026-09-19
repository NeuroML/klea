#!/usr/bin/env python3
"""
Tests for the empty-answer fallback in AnswerFromContext.

File: tests/test_answer_from_context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_rag.nodes.answer_from_context import AnswerFromContext, AnswerSchema
from klea_rag.schemas import RAGState
from klea_utils.nodes.base import EMPTY_RESULT_FALLBACK
from langchain_core.messages import AIMessage


def _node() -> AnswerFromContext:
    return AnswerFromContext(
        logger=logging.getLogger("test_answer_from_context"),
        label="Answering",
        llm_models={"chat": None},
    )


def test_default_error_result_is_retry_message():
    """The node's default error result carries the user-facing retry text."""
    result = _node()._get_default_error_result()
    assert result == AnswerSchema(answer=EMPTY_RESULT_FALLBACK, references=[])


def test_process_output_empty_returns_retry_message():
    """An empty structured parse is replaced by the retry message."""
    node = _node()
    output = {
        "parsed": AnswerSchema(),
        "parsing_error": None,
        "raw": AIMessage(content="{}"),
    }
    result = node._process_output(output)
    assert result == AnswerSchema(answer=EMPTY_RESULT_FALLBACK, references=[])


def test_empty_reference_material_renders_sentinel():
    """An empty retrieval renders a sentinel, not an empty context label."""
    variables = _node()._get_prompt_variables(RAGState(query="q"))
    assert variables["reference_material"] == "(no reference material)"
