#!/usr/bin/env python3
"""
Tests for the fallback warning logic in AnswerGeneral.

File: tests/test_answer_general.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from types import SimpleNamespace

from klea_utils.nodes.answer_general import AnswerGeneral, FallbackConfig
from klea_utils.nodes.base import EMPTY_RESULT_FALLBACK
from langchain_core.messages import AIMessage

WARNING = "Answer from training data; sources could not be verified."


def _node(fallback_config: FallbackConfig | None) -> AnswerGeneral:
    logger = logging.getLogger("test_answer_general")
    return AnswerGeneral(
        logger=logger,
        label="Answering generally",
        llm_models={"chat": None},
        fallback_config=fallback_config,
    )


def _state(query_domains=None, messages=None, **extra):
    state = {
        "query": "some question",
        "messages": messages or [],
        **extra,
    }
    if query_domains is not None:
        state["query_domains"] = query_domains
    return SimpleNamespace(**state)


def _update(node: AnswerGeneral, state) -> str:
    result = AIMessage(content="<think>thinking</think>\nThe answer text.")
    updates = node._update_state(result, state)
    return updates["message_for_user"]


def test_fallback_warning_added_for_domain_query():
    """Warning is appended when the query matched a real domain."""
    node = _node(FallbackConfig(enabled=True, warning=WARNING))
    answer = _update(node, _state(query_domains=["NeuroML"]))
    assert WARNING in answer


def test_no_warning_for_undefined_domain():
    """No warning for a genuinely non-domain query."""
    node = _node(FallbackConfig(enabled=True, warning=WARNING))
    answer = _update(node, _state(query_domains=["undefined"]))
    assert WARNING not in answer


def test_no_warning_when_fallback_disabled():
    """No warning when the fallback is not enabled, even for a domain query."""
    node = _node(FallbackConfig(enabled=False, warning=WARNING))
    answer = _update(node, _state(query_domains=["NeuroML"]))
    assert WARNING not in answer


def test_no_warning_when_no_warning_text():
    """No warning when the warning text is empty."""
    node = _node(FallbackConfig(enabled=True, warning=""))
    answer = _update(node, _state(query_domains=["NeuroML"]))
    assert WARNING not in answer


def test_no_warning_when_no_domains_attribute():
    """States without ``query_domains`` never show the warning."""
    node = _node(FallbackConfig(enabled=True, warning=WARNING))
    answer = _update(node, _state())
    assert WARNING not in answer


def test_blank_answer_uses_fallback_message():
    """A blank generation surfaces the retry message, not an empty reply."""
    node = _node(None)
    result = AIMessage(content="<think>thinking</think>\n   ")
    updates = node._update_state(result, _state())
    assert updates["message_for_user"] == EMPTY_RESULT_FALLBACK
