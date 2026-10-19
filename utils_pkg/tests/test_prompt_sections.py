#!/usr/bin/env python3
"""
Tests for the prompt-section helper (prompt conventions).

File: tests/test_prompt_sections.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar

from klea_utils.llm import LLMModel
from klea_utils.nodes.answer_general import AnswerGeneral
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.nodes.context import LLMNodeContext
from pydantic import BaseModel


def test_optional_section_omits_when_empty():
    assert BaseLLMNode._optional_section("Outcome details", "") == ""
    assert BaseLLMNode._optional_section("Outcome details", "   ") == ""


def test_optional_section_renders_heading_and_body():
    rendered = BaseLLMNode._optional_section(
        "Outcome details", "Pending question: why?"
    )
    assert rendered == "## Outcome details\n\nPending question: why?"


def test_optional_section_respects_heading_level():
    rendered = BaseLLMNode._optional_section("Detail", "x", level=3)
    assert rendered.startswith("### Detail\n\n")


def _answer_general(model_name: str) -> AnswerGeneral:
    return AnswerGeneral(
        logger=logging.getLogger("test_prompt_sections"),
        label="Answering",
        llm_models={"chat": LLMModel(instance=None, model_name=model_name)},
    )


def test_effective_model_name_uses_entry_model():
    assert _answer_general("openai:gpt-4o")._effective_model_name() == "openai:gpt-4o"


def test_effective_model_name_prefers_node_default():
    class _DefaultedAnswer(AnswerGeneral):
        model_defaults: ClassVar[dict[str, Any]] = {"model": "local:tiny"}

    node = _DefaultedAnswer(
        logger=logging.getLogger("test_prompt_sections"),
        label="Answering",
        llm_models={"chat": LLMModel(instance=None, model_name="openai:gpt-4o")},
    )
    assert node._effective_model_name() == "local:tiny"


def test_effective_model_name_empty_without_model():
    assert _answer_general("")._effective_model_name() == ""


def test_model_identity_is_opt_in():
    assert BaseLLMNode.include_model_identity is False
    assert AnswerGeneral.include_model_identity is True


def test_answer_general_renders_model_identity():
    class _State(BaseModel):
        query: str = ""

    node = _answer_general("openai:gpt-4o")
    prompt = node._get_system_prompt(_State(), LLMNodeContext())
    assert isinstance(prompt, str)
    assert "Model in use: openai:gpt-4o" in prompt
