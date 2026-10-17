#!/usr/bin/env python3
"""
Tests for the ReviewProgress node (ADR-0051).

File: tests/test_review_progress.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_agent.nodes.review_progress import ReviewProgress, ReviewProgressSchema
from klea_agent.schemas import (
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    StepOutput,
    StepSchema,
)
from klea_utils.nodes.context import LLMNodeContext
from langchain_core.prompts import ChatPromptTemplate


def _node() -> ReviewProgress:
    """Build the node with a placeholder model (no LLM is called in these tests)."""
    return ReviewProgress(
        logger=logging.getLogger("test"),
        label="Preparing review",
        llm_models={"chat": object()},
    )


def _state(*, with_work: bool) -> KleaAgentState:
    """Build a checkpoint state with an optional completed step output."""
    state = KleaAgentState(query="update the config")
    state.goal = GoalSchema(goal="update the config", success_criteria="updated")
    state.plan = PlanSchema(
        step_list=[
            StepSchema(step_number=1, description="edit the config", status="done"),
            StepSchema(
                step_number=2,
                kind="review",
                description="Review the config change",
                depends_on=[1],
            ),
            StepSchema(step_number=3, description="deploy"),
        ]
    )
    if with_work:
        state.step_outputs = {
            1: [StepOutput(result="changed timeout to 30", tool="reasoning")]
        }
    return state


async def test_execute_skips_without_work():
    """A whole-plan approval (no work) skips the LLM entirely."""
    update = await _node().execute(_state(with_work=False))
    assert update == {}


def test_update_state_writes_the_stripped_summary():
    update = _node()._update_state(
        ReviewProgressSchema(summary="  did the thing  "),
        _state(with_work=True),
        LLMNodeContext(),
    )
    assert update == {"review_progress": "did the thing"}


def test_prompt_variables_include_goal_gate_and_work():
    variables = _node()._get_prompt_variables(_state(with_work=True), LLMNodeContext())
    assert "update the config" in variables["goal"]
    assert "Review the config change" in variables["review_step"]
    assert "changed timeout to 30" in variables["progress_observations"]


def test_default_error_result_is_empty():
    assert _node()._get_default_error_result(LLMNodeContext()) == ReviewProgressSchema()


def test_user_prompt_renders_the_work_section():
    node = _node()
    variables = node._get_prompt_variables(_state(with_work=True), LLMNodeContext())
    template = ChatPromptTemplate(
        [("human", node._load_prompt_file("ReviewProgress_user"))]
    )
    text = template.format_messages(**variables)[0].content
    assert "Work completed since the last review" in text
    assert "changed timeout to 30" in text
