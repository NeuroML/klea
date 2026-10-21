#!/usr/bin/env python3
"""
Tests for the agent AwaitHuman node (HITL interrupt/resume, ADR-0046).

File: tests/test_await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur at gmail dot com>
"""

import logging
from typing import Literal

from klea_agent.nodes.await_human import AwaitHuman
from klea_agent.schemas import (
    KleaAgentState,
    PlanRevision,
    PlanSchema,
    StepSchema,
)
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command
from pydantic import BaseModel, Field


class _HitlState(BaseModel):
    """Minimal state carrying just the fields AwaitHuman reads/writes."""

    human_feedback: str = ""
    human_input: dict[int, list[str]] = Field(default_factory=dict)
    replan_reason: str = ""
    messages: list[AnyMessage] = Field(default_factory=list)
    plan: PlanSchema = Field(default_factory=PlanSchema)

    def review_sections(self) -> tuple[str, list[dict[str, str]]]:
        """Minimal stand-in for ``KleaAgentState.review_sections``."""
        return "(headline)", [{"title": "Plan", "body": "(plan)"}]


def _compile(kind: Literal["review", "input"]):
    """Compile a tiny START -> AwaitHuman -> END graph with a checkpointer."""
    node = AwaitHuman(logging.getLogger("test"), "Awaiting", kind=kind)
    workflow = StateGraph(_HitlState)
    workflow.add_node(node.label, node.execute)
    workflow.add_edge(START, node.label)
    workflow.add_edge(node.label, END)
    return workflow.compile(checkpointer=InMemorySaver())


async def test_input_batches_step_questions_and_writes_answers():
    """The input instance asks the blocked steps' questions and writes answers."""
    graph = _compile("input")
    config = {"configurable": {"thread_id": "input"}}
    plan = PlanSchema(
        status="needs_input",
        step_list=[
            StepSchema(step_number=1, description="find", success_criteria="found"),
            StepSchema(
                step_number=2,
                description="deploy",
                needs_input=["Which file?", "Which mode?"],
            ),
        ],
    )

    await graph.ainvoke(_HitlState(plan=plan), config=config)

    intr = (await graph.aget_state(config)).tasks[0].interrupts[0]
    assert intr.value == {
        "kind": "input",
        "questions": [
            {"step_number": 2, "question": "Which file?"},
            {"step_number": 2, "question": "Which mode?"},
        ],
    }
    # One interrupt, a typed form with a positional answers list.
    assert set(intr.response_schema["properties"]) == {"action", "answers"}

    result = await graph.ainvoke(
        Command(resume={"action": "answer", "answers": ["a.txt", "fast"]}),
        config=config,
    )

    assert result["human_input"] == {2: ["a.txt", "fast"]}
    assert isinstance(result["messages"][-1], HumanMessage)
    assert (await graph.aget_state(config)).next == ()


def test_input_ask_includes_abandoned_plan_sections():
    """A guidance input ask carries the failed plan versions for context."""
    node = AwaitHuman(logging.getLogger("test"), "Awaiting", kind="input")
    state = KleaAgentState(
        plan=PlanSchema(
            status="needs_input",
            plan_version=2,
            step_list=[
                StepSchema(
                    step_number=2,
                    description="Provide guidance to continue",
                    needs_input=["How should I proceed?"],
                )
            ],
        ),
        plan_history=[
            PlanRevision(
                version=1,
                steps=[
                    StepSchema(
                        step_number=1,
                        description="old approach",
                        suggested_tools=["read_file"],
                    )
                ],
                abandoned_reason="tool failed",
            )
        ],
    )

    payload = node._ask(state)

    assert payload["kind"] == "input"
    assert payload["questions"] == [
        {"step_number": 2, "question": "How should I proceed?"}
    ]
    assert [section["title"] for section in payload["sections"]] == [
        "Plan version 1 (abandoned)",
        "Current plan version 2",
    ]
    assert "tool failed" in payload["sections"][0]["body"]
    assert "old approach" in payload["sections"][0]["body"]


async def test_review_approve_runs_the_plan():
    """Approval resolves the review step, counts the round, clears feedback."""
    graph = _compile("review")
    config = {"configurable": {"thread_id": "approve"}}
    plan = PlanSchema(
        status="in_progress",
        step_list=[StepSchema(step_number=1, kind="review", description="Review")],
    )

    await graph.ainvoke(_HitlState(plan=plan), config=config)

    intr = (await graph.aget_state(config)).tasks[0].interrupts[0]
    assert intr.value["kind"] == "review"
    # The form carries the adaptive headline + sections, not the raw plan.
    assert intr.value["headline"] == "(headline)"
    assert intr.value["sections"] == [{"title": "Plan", "body": "(plan)"}]
    assert "plan" not in intr.value
    assert "progress" not in intr.value
    assert intr.response_schema is not None  # typed review form

    result = await graph.ainvoke(
        Command(resume={"action": "answer", "decision": "approve"}), config=config
    )

    assert result["plan"].status == "in_progress"
    assert result["plan"].step_list[0].status == "done"  # review step resolved
    assert result["plan"].human_feedback_rounds == 1
    assert result["human_feedback"] == ""


async def test_review_checkpoint_carries_sections():
    """A review step after completed work is a checkpoint carrying sections."""
    graph = _compile("review")
    config = {"configurable": {"thread_id": "checkpoint"}}
    plan = PlanSchema(
        status="in_progress",
        step_list=[
            StepSchema(step_number=1, description="fetch", status="done"),
            StepSchema(
                step_number=2,
                kind="review",
                description="Review progress",
                depends_on=[1],
            ),
        ],
    )

    await graph.ainvoke(_HitlState(plan=plan), config=config)
    intr = (await graph.aget_state(config)).tasks[0].interrupts[0]
    assert intr.value["kind"] == "review"
    # The adaptive headline + sections are carried (not the full plan).
    assert intr.value["headline"] == "(headline)"
    assert intr.value["sections"] == [{"title": "Plan", "body": "(plan)"}]


async def test_review_revise_returns_feedback():
    """A revision keeps the review step pending and passes the feedback on."""
    graph = _compile("review")
    config = {"configurable": {"thread_id": "revise"}}
    plan = PlanSchema(
        status="in_progress",
        step_list=[StepSchema(step_number=1, kind="review", description="Review")],
    )

    await graph.ainvoke(_HitlState(plan=plan), config=config)
    result = await graph.ainvoke(
        Command(
            resume={
                "action": "answer",
                "decision": "revise",
                "feedback": "use api v2",
            }
        ),
        config=config,
    )

    assert result["human_feedback"] == "use api v2"
    assert result["plan"].step_list[0].status == "pending"  # not resolved
    assert result["plan"].status == "in_progress"


async def test_review_revise_without_feedback_sets_signal():
    """An empty-feedback revision still sets the router's revision signal."""
    graph = _compile("review")
    config = {"configurable": {"thread_id": "revise-empty"}}
    plan = PlanSchema(
        status="in_progress",
        step_list=[StepSchema(step_number=1, kind="review", description="Review")],
    )

    await graph.ainvoke(_HitlState(plan=plan), config=config)
    result = await graph.ainvoke(
        Command(resume={"action": "answer", "decision": "revise"}), config=config
    )
    assert result["human_feedback"]  # non-empty: the revision signal


async def test_cancel_marks_user_cancelled():
    """A cancel sets user_cancelled and writes an AI note."""
    graph = _compile("input")
    config = {"configurable": {"thread_id": "cancel"}}

    plan = PlanSchema(
        status="needs_input",
        step_list=[StepSchema(step_number=1, needs_input=["Which file?"])],
    )
    await graph.ainvoke(_HitlState(plan=plan), config=config)
    result = await graph.ainvoke(Command(resume={"action": "cancel"}), config=config)

    assert result["plan"].status == "user_cancelled"
    assert isinstance(result["messages"][-1], AIMessage)
    assert "cancelled" in result["messages"][-1].content.lower()
    assert (await graph.aget_state(config)).next == ()
