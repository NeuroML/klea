#!/usr/bin/env python3
"""
Tests for the agent AwaitHuman node (HITL interrupt/resume, ADR-0046).

File: tests/test_await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Literal

from klea_agent.nodes.await_human import AwaitHuman
from klea_agent.schemas import PlanSchema
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field


class _HitlState(BaseModel):
    """Minimal state carrying just the fields AwaitHuman reads/writes."""

    pending_question: str = ""
    human_feedback: str = ""
    human_input: str = ""
    replan_reason: str = ""
    messages: list[AnyMessage] = Field(default_factory=list)
    plan: PlanSchema = Field(default_factory=PlanSchema)


def _compile(kind: Literal["review", "input"], answer_field: str):
    """Compile a tiny START -> AwaitHuman -> END graph with a checkpointer."""
    node = AwaitHuman(
        logging.getLogger("test"), "Awaiting", kind=kind, answer_field=answer_field
    )
    workflow = StateGraph(_HitlState)
    workflow.add_node(node.label, node.execute)
    workflow.add_edge(START, node.label)
    workflow.add_edge(node.label, END)
    return workflow.compile(checkpointer=InMemorySaver())


async def test_pause_then_answer_writes_field():
    """An interrupt pauses the run; an answer resumes it and writes the field."""
    graph = _compile("input", "human_input")
    config = {"configurable": {"thread_id": "answer"}}

    await graph.ainvoke(_HitlState(pending_question="Which file?"), config=config)

    paused = await graph.aget_state(config)
    assert paused.next == ("Awaiting",)
    assert paused.tasks[0].interrupts[0].value == {
        "kind": "input",
        "question": "Which file?",
    }

    result = await graph.ainvoke(
        Command(resume={"action": "answer", "text": "a.txt"}), config=config
    )

    assert result["human_input"] == "a.txt"
    assert result["human_feedback"] == ""
    assert isinstance(result["messages"][-1], HumanMessage)
    assert result["messages"][-1].content == "a.txt"
    assert (await graph.aget_state(config)).next == ()


async def test_bare_string_resume_is_an_answer():
    """A bare string resume value is accepted (no sentinel envelope)."""
    graph = _compile("review", "human_feedback")
    config = {"configurable": {"thread_id": "bare"}}

    await graph.ainvoke(_HitlState(), config=config)
    result = await graph.ainvoke(Command(resume="looks good"), config=config)

    assert result["human_feedback"] == "looks good"
    assert result["messages"][-1].content == "looks good"


async def test_review_question_names_kind():
    """The review instance's ask carries kind=review and a non-empty question."""
    graph = _compile("review", "human_feedback")
    config = {"configurable": {"thread_id": "review"}}

    await graph.ainvoke(_HitlState(), config=config)

    ask = (await graph.aget_state(config)).tasks[0].interrupts[0].value
    assert ask["kind"] == "review"
    assert ask["question"]


async def test_cancel_is_terminal_and_marks_user_cancelled():
    """A cancel sets user_cancelled, writes an AI note, and clears the field."""
    graph = _compile("input", "human_input")
    config = {"configurable": {"thread_id": "cancel"}}

    await graph.ainvoke(_HitlState(pending_question="Which file?"), config=config)
    result = await graph.ainvoke(Command(resume={"action": "cancel"}), config=config)

    assert result["plan"].status == "user_cancelled"
    assert result["human_input"] == ""
    assert isinstance(result["messages"][-1], AIMessage)
    assert "cancelled" in result["messages"][-1].content.lower()
    assert (await graph.aget_state(config)).next == ()
