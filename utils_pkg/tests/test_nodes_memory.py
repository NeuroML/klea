#!/usr/bin/env python3
"""
Tests for the memory-based prompt injection and the summarise memory node.

File: tests/test_nodes_memory.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_utils.llm import get_recent_messages
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.nodes.summarise_memory import SummariseMemoryNode
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class MemoryState(BaseModel):
    """Minimal state exposing the fields memory nodes read."""

    messages: list = Field(default_factory=list)
    context_summary: str = ""
    summarised_till: int = 0
    query: str = ""


class DummyNode(BaseLLMNode[MemoryState, BaseModel]):
    """Concrete BaseLLMNode for testing memory prompt injection."""

    model_type = "chat"

    def _get_prompt_variables(self, state: BaseModel) -> dict:
        return {"query": getattr(state, "query", "")}

    def _update_state(self, result, state: BaseModel) -> dict:
        return {}

    def _get_default_error_result(self) -> AIMessage:
        return AIMessage(content="")


def _conversation(n_pairs: int = 5) -> list:
    """Build an interleaved human/ai conversation of *n_pairs* turns."""
    return [
        message
        for i in range(n_pairs)
        for message in (
            HumanMessage(content=f"user message number {i} with some padding"),
            AIMessage(content=f"assistant reply number {i} with some padding"),
        )
    ]


def _make_summarise_node(**kwargs) -> SummariseMemoryNode:
    return SummariseMemoryNode(
        logger=logging.getLogger("test_nodes_memory"),
        label="Summarising",
        llm_models={"chat": None},
        **kwargs,
    )


def test_summarise_skips_below_threshold():
    node = _make_summarise_node(
        summarisation_threshold_chars=10_000, num_history_chars=10_000
    )
    state = MemoryState(messages=_conversation(2), summarised_till=0)
    assert node._pre_exec(state) is False


def test_summarise_triggers_and_excludes_window():
    node = _make_summarise_node(summarisation_threshold_chars=1, num_history_chars=50)
    msgs = _conversation(5)
    state = MemoryState(messages=msgs, summarised_till=0)
    assert node._pre_exec(state) is True

    expected_start = len(msgs) - len(get_recent_messages(msgs, 50))
    logger.debug(f"{node._window_start = } {expected_start = }")
    assert node._window_start == expected_start
    assert 0 < node._window_start < len(msgs)  # recent window excluded


def test_summarise_update_state_uses_window_start():
    node = _make_summarise_node(summarisation_threshold_chars=1, num_history_chars=50)
    msgs = _conversation(5)
    state = MemoryState(messages=msgs, summarised_till=0)
    node._pre_exec(state)

    updates = node._update_state(AIMessage(content="a summary"), state)
    logger.debug(f"{updates = } {node._window_start = } {len(msgs) = }")
    assert "a summary" in updates["context_summary"]
    assert updates["summarised_till"] == node._window_start
    assert updates["summarised_till"] < len(msgs)  # no overlap with window


def test_summarise_skips_when_nothing_new_old():
    node = _make_summarise_node(summarisation_threshold_chars=1, num_history_chars=50)
    msgs = _conversation(2)
    # Everything already summarised up to the current message count.
    state = MemoryState(messages=msgs, summarised_till=len(msgs))
    assert node._pre_exec(state) is False


def _prompt_dir(tmp_path):
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "DummyNode_system.md").write_text("Base system prompt.")
    (prompts / "DummyNode_user.md").write_text("User query: {query}")
    return prompts


def _dummy_node(tmp_path, memory: bool) -> DummyNode:
    node = DummyNode(
        logger=logging.getLogger("test_nodes_memory"),
        label="Dummy",
        llm_models={"chat": None},
        output_schema=None,
        memory=memory,
    )
    node.prompt_registry_location = _prompt_dir(tmp_path)
    return node


def test_memory_injection_returns_list_with_real_messages(tmp_path):
    node = _dummy_node(tmp_path, memory=True)
    msgs = [
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
    ]
    state = MemoryState(messages=msgs, context_summary="a summary", query="latest")

    system = node._get_system_prompt(state)
    logger.debug(f"{system = }")
    assert isinstance(system, list)
    assert system[0][0] == "system"
    assert "a summary" in system[0][1]

    history = system[1:]
    assert all(isinstance(m, (HumanMessage, AIMessage)) for m in history)
    assert [m.content for m in history] == ["q1", "a1", "q2"]

    template = node._create_prompt_template(system, node._get_human_prompt(state))
    prompt = template.invoke({"query": "latest"})
    messages = prompt.to_messages()
    roles = [m.type for m in messages]
    logger.debug(f"{roles = }")
    assert roles == ["system", "human", "ai", "human", "human"]
    assert [m.content for m in messages[1:-1]] == ["q1", "a1", "q2"]
    assert messages[-1].content == "User query: latest"


def test_memory_injection_off_returns_string(tmp_path):
    node = _dummy_node(tmp_path, memory=False)
    state = MemoryState(messages=[HumanMessage(content="q1")], query="latest")
    system = node._get_system_prompt(state)
    logger.debug(f"{system = }")
    assert isinstance(system, str)
    assert system.startswith("Base system prompt.")


if __name__ == "__main__":
    pytest.main()
