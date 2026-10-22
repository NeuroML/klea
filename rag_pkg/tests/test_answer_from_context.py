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
from klea_utils.llm import LLMModel
from klea_utils.nodes.base import EMPTY_RESULT_FALLBACK
from klea_utils.nodes.context import LLMNodeContext
from langchain_core.messages import AIMessage


def _node() -> AnswerFromContext:
    return AnswerFromContext(
        logger=logging.getLogger("test_answer_from_context"),
        label="Answering",
        llm_models={"chat": None},
    )


def test_default_error_result_is_retry_message():
    """The node's default error result carries the user-facing retry text."""
    result = _node()._get_default_error_result(LLMNodeContext())
    assert result == AnswerSchema(answer=EMPTY_RESULT_FALLBACK, references=[])


def test_prose_reply_is_recovered_as_the_answer():
    """A prose reply that fails JSON parsing is used as the answer."""
    result = _node()._process_output(
        LLMNodeContext(
            output=AIMessage(content="The context says X."),
            output_schema=AnswerSchema,
        )
    )
    assert result == AnswerSchema(answer="The context says X.", references=[])


def test_prose_recovery_strips_thought_prefix():
    """Only the text after ``</think>`` becomes the recovered answer."""
    result = _node()._process_output(
        LLMNodeContext(
            output=AIMessage(content="internal thought</think>The result."),
            output_schema=AnswerSchema,
        )
    )
    assert result == AnswerSchema(answer="The result.", references=[])


def test_broken_json_keeps_the_retry_message():
    """Broken JSON-looking output keeps the generic retry fallback."""
    result = _node()._get_default_error_result(
        LLMNodeContext(output=AIMessage(content='{"answer": '))
    )
    assert result == AnswerSchema(answer=EMPTY_RESULT_FALLBACK, references=[])


def test_process_output_empty_returns_retry_message():
    """An empty structured parse is replaced by the retry message."""
    node = _node()
    output = {
        "parsed": AnswerSchema(),
        "parsing_error": None,
        "raw": AIMessage(content="{}"),
    }
    result = node._process_output(
        LLMNodeContext(output=output, output_schema=AnswerSchema)
    )
    assert result == AnswerSchema(answer=EMPTY_RESULT_FALLBACK, references=[])


def test_empty_reference_material_renders_sentinel():
    """An empty retrieval renders a sentinel, not an empty context label."""
    variables = _node()._get_prompt_variables(RAGState(query="q"), LLMNodeContext())
    assert variables["reference_material"] == "(no reference material)"


def test_system_prompt_reports_model_identity():
    """The RAG answer node can answer which model is replying."""
    node = AnswerFromContext(
        logger=logging.getLogger("test_answer_from_context"),
        label="Answering",
        llm_models={"chat": LLMModel(instance=None, model_name="openai:gpt-4o")},
    )
    prompt = node._get_system_prompt(RAGState(query="q"), LLMNodeContext())
    assert isinstance(prompt, str)
    assert "Model in use: openai:gpt-4o" in prompt
