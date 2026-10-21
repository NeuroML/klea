#!/usr/bin/env python3
"""
Tests for the agent ResetState task boundary.

File: tests/test_reset_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Literal

import pytest
from klea_agent.nodes.reset_state import ResetState
from klea_agent.schemas import (
    ContinuationEvidence,
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlanRevision,
    PlanSchema,
    RouteSchema,
    StepSchema,
)


def _node(monkeypatch) -> ResetState:
    node = ResetState(logging.getLogger("test"), "Resetting state")
    monkeypatch.setattr(node, "write_custom_stream", lambda event: None)
    return node


def _state(
    route: Literal["task", "continue"], *, evidence: bool = True
) -> KleaAgentState:
    return KleaAgentState(
        route=RouteSchema(route=route),
        plan=PlanSchema(
            step_list=[StepSchema(description="old step")],
            plan_version=2,
        ),
        goal=GoalSchema(goal="old goal"),
        plan_history=[PlanRevision(version=1, abandoned_reason="old")],
        evaluation=EvaluationSchema(overall="abort"),
        step_outputs={1: []},
        tool_retry_counts={1: 2},
        step_attempt_counts={1: 1},
        picker_attempts=2,
        picker_step=1,
        failure_reason="boom",
        human_feedback="feedback",
        human_input={1: ["answer"]},
        replan_reason="reason",
        review_progress="progress",
        continuation_evidence=(
            ContinuationEvidence(goal="old goal") if evidence else None
        ),
    )


@pytest.mark.asyncio
async def test_task_route_clears_plan_and_evidence(monkeypatch):
    update = await _node(monkeypatch).execute(_state("task"))

    assert update["plan"] == PlanSchema()
    assert update["plan_history"] == []
    assert update["goal"] == GoalSchema()
    assert update["step_outputs"] == {}
    assert update["tool_retry_counts"] == {}
    assert update["step_attempt_counts"] == {}
    assert update["picker_attempts"] == 0
    assert update["picker_step"] == -1
    assert update["failure_reason"] == ""
    assert update["human_feedback"] == ""
    assert update["human_input"] == {}
    assert update["replan_reason"] == ""
    assert update["review_progress"] == ""
    assert update["evaluation"] == EvaluationSchema()
    assert update["continuation_evidence"] is None


@pytest.mark.asyncio
async def test_continue_route_preserves_evidence_but_replaces_task_state(monkeypatch):
    update = await _node(monkeypatch).execute(_state("continue"))

    assert "continuation_evidence" not in update
    assert update["plan"] == PlanSchema()
    assert update["plan_history"] == []
    assert update["goal"] == GoalSchema()
    assert update["step_outputs"] == {}
    assert update["replan_reason"] == ""
    assert update["failure_reason"] == ""
    assert update["human_feedback"] == ""
    assert update["human_input"] == {}


@pytest.mark.asyncio
async def test_continue_without_evidence_falls_back_to_full_reset(monkeypatch):
    update = await _node(monkeypatch).execute(_state("continue", evidence=False))

    assert update["continuation_evidence"] is None
    assert update["plan"] == PlanSchema()
    assert update["goal"] == GoalSchema()
