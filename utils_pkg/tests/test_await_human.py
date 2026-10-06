#!/usr/bin/env python3
"""
Tests for the generic human-in-the-loop AwaitHumanNode (ADR-0046).

File: tests/test_await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, Literal

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
    """A concrete app-style subclass: free-text answer, cancel hook."""

    def _ask(self, state: _State) -> dict[str, Any]:
        return {"kind": "test", "question": state.question or "question?"}

    def _on_answer(self, state: _State, answers: dict[str, Any]) -> dict[str, Any]:
        text = str(answers.get("text", ""))
        return {
            "answer": text,
            "messages": [*state.messages, HumanMessage(content=text)],
        }

    def _on_cancel(self, state: _State, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "cancelled": True,
            "messages": [*state.messages, AIMessage(content="cancelled")],
        }


class _ChoiceResponse(BaseModel):
    """A typed resume payload used by the hitl_response_schema test."""

    action: Literal["answer", "cancel"] = "answer"
    option: str = ""


class _SchemaNode(AwaitHumanNode[_State]):
    """A subclass that supplies a typed ``hitl_response_schema``."""

    def _ask(self, state: _State) -> dict[str, Any]:
        return {"kind": "choice", "question": "pick one"}

    def _hitl_response_schema(
        self, state: _State, payload: dict[str, Any]
    ) -> type[BaseModel] | None:
        return _ChoiceResponse

    def _on_answer(self, state: _State, answers: dict[str, Any]) -> dict[str, Any]:
        return {"answer": str(answers.get("option", ""))}

    def _on_cancel(self, state: _State, payload: dict[str, Any]) -> dict[str, Any]:
        return {"cancelled": True}


def _compile(node_cls: type[AwaitHumanNode] = _Node):
    node = node_cls(logging.getLogger("test"), "Awaiting", kind="test")
    workflow = StateGraph(_State)
    workflow.add_node(node.label, node.execute)
    workflow.add_edge(START, node.label)
    workflow.add_edge(node.label, END)
    return workflow.compile(checkpointer=InMemorySaver())


async def test_answer_writes_field_and_message():
    """An interrupt pauses; an answer resumes and runs the answer hook."""
    graph = _compile()
    config = {"configurable": {"thread_id": "a"}}

    await graph.ainvoke(_State(question="Q?"), config=config)

    paused = await graph.aget_state(config)
    assert paused.next == ("Awaiting",)
    assert paused.tasks[0].interrupts[0].value == {"kind": "test", "question": "Q?"}

    result = await graph.ainvoke(
        Command(resume={"action": "answer", "text": "yes"}), config=config
    )

    assert result["answer"] == "yes"
    assert isinstance(result["messages"][-1], HumanMessage)
    assert (await graph.aget_state(config)).next == ()


async def test_cancel_runs_cancel_hook():
    """A cancel runs the cancel hook and never the answer hook."""
    graph = _compile()
    config = {"configurable": {"thread_id": "b"}}

    await graph.ainvoke(_State(question="Q?"), config=config)
    result = await graph.ainvoke(Command(resume={"action": "cancel"}), config=config)

    assert result["cancelled"] is True
    assert result["answer"] == ""
    assert isinstance(result["messages"][-1], AIMessage)


async def test_hitl_response_schema_is_published_and_validated():
    """A node's hitl_response_schema is exposed on the interrupt and validated."""
    graph = _compile(_SchemaNode)
    config = {"configurable": {"thread_id": "c"}}

    await graph.ainvoke(_State(), config=config)
    intr = (await graph.aget_state(config)).tasks[0].interrupts[0]
    assert intr.response_schema is not None
    assert "option" in intr.response_schema["properties"]

    result = await graph.ainvoke(
        Command(resume={"action": "answer", "option": "x"}), config=config
    )
    assert result["answer"] == "x"


def test_parse_response_accepts_model_dict_and_string():
    """_parse_response normalises a model, a mapping, and a bare string."""
    assert AwaitHumanNode._parse_response("hi") == ("answer", {"answer": "hi"})
    assert AwaitHumanNode._parse_response({"action": "cancel"}) == ("cancel", {})
    assert AwaitHumanNode._parse_response({"a": "b"}) == ("answer", {"a": "b"})
    assert AwaitHumanNode._parse_response(_ChoiceResponse(option="x")) == (
        "answer",
        {"option": "x"},
    )


def test_ask_hook_must_be_overridden():
    """The base ask hook raises until an app overrides it."""
    node = AwaitHumanNode(logging.getLogger("test"), "Awaiting", kind="x")
    with pytest.raises(NotImplementedError):
        node._ask(_State())
