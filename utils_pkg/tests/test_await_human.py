#!/usr/bin/env python3
"""
Tests for the generic human-in-the-loop AwaitHumanNode (ADR-0046).

File: tests/test_await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any

import pytest
from klea_utils.nodes.await_human import AwaitHumanNode
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field


class _State(BaseModel):
    """Minimal state for the generic node's hooks."""

    question: str = ""
    answer: str = ""
    cancelled: bool = False
    messages: list[AnyMessage] = Field(default_factory=list)


class _Node(AwaitHumanNode[_State]):
    """A concrete app-style subclass providing the three policy hooks."""

    def _question(self, state: _State) -> str:
        return state.question or "question?"

    def _on_answer(self, state: _State, text: str) -> dict[str, Any]:
        return {"messages": [*state.messages, HumanMessage(content=text)]}

    def _on_cancel(self, state: _State, question: str) -> dict[str, Any]:
        return {
            "cancelled": True,
            "messages": [*state.messages, AIMessage(content="cancelled")],
        }


def _compile():
    node = _Node(
        logging.getLogger("test"), "Awaiting", kind="review", answer_field="answer"
    )
    workflow = StateGraph(_State)
    workflow.add_node(node.label, node.execute)
    workflow.add_edge(START, node.label)
    workflow.add_edge(node.label, END)
    return workflow.compile(checkpointer=InMemorySaver())


async def test_answer_writes_field_and_message():
    """An interrupt pauses; an answer resumes and writes the field."""
    graph = _compile()
    config = {"configurable": {"thread_id": "a"}}

    await graph.ainvoke(_State(question="Q?"), config=config)

    paused = await graph.aget_state(config)
    assert paused.next == ("Awaiting",)
    assert paused.tasks[0].interrupts[0].value == {
        "kind": "review",
        "question": "Q?",
    }

    result = await graph.ainvoke(
        Command(resume={"action": "answer", "text": "yes"}), config=config
    )

    assert result["answer"] == "yes"
    assert isinstance(result["messages"][-1], HumanMessage)
    assert (await graph.aget_state(config)).next == ()


async def test_cancel_clears_field_and_marks():
    """A cancel runs the cancel hook and clears the answer field."""
    graph = _compile()
    config = {"configurable": {"thread_id": "b"}}

    await graph.ainvoke(_State(question="Q?"), config=config)
    result = await graph.ainvoke(Command(resume={"action": "cancel"}), config=config)

    assert result["cancelled"] is True
    assert result["answer"] == ""
    assert isinstance(result["messages"][-1], AIMessage)
    assert (await graph.aget_state(config)).next == ()


async def test_bare_string_resume_is_an_answer():
    """A bare string resume value is accepted (no sentinel envelope)."""
    graph = _compile()
    config = {"configurable": {"thread_id": "c"}}

    await graph.ainvoke(_State(question="Q?"), config=config)
    result = await graph.ainvoke(Command(resume="plain"), config=config)

    assert result["answer"] == "plain"


def test_question_hook_must_be_overridden():
    """The base question hook raises until an app overrides it."""
    node = AwaitHumanNode(
        logging.getLogger("test"), "Awaiting", kind="x", answer_field="answer"
    )
    with pytest.raises(NotImplementedError):
        node._question(_State())
