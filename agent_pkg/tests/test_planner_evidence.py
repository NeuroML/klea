#!/usr/bin/env python3
"""
Tests for the ``KleaAgentState.planner_evidence`` observations delegate.

``planner_evidence`` is a thin delegate: it supplies the graph state to
``klea_agent.evidence.render_plan_evidence`` and returns that render's
return value.  These tests pin the delegation for the no-flag, evaluator-flagged
and abandoned-revision cases and assert the plain-string return type, so the
method is checked against the selector instead of duplicating its logic.

File: tests/test_planner_evidence.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from typing import Literal, cast

from klea_agent.evidence import NO_EVIDENCE, render_plan_evidence
from klea_agent.schemas import (
    EvaluationSchema,
    KleaAgentState,
    PlannerPlanSchema,
    PlanRevision,
    PlanSchema,
    StepEvaluation,
    StepOutput,
    StepSchema,
)


def _step(
    number: int,
    *,
    kind: Literal["tool", "reasoning", "review"] = "tool",
    status: Literal["pending", "done", "skipped", "failed"] = "pending",
    depends_on: list[int] | None = None,
) -> StepSchema:
    """Build a plan step for the delegate tests."""
    return StepSchema(
        step_number=number,
        description=f"step {number}",
        kind=kind,
        status=status,
        depends_on=list(depends_on or []),
    )


def _output(text: str) -> StepOutput:
    """A text step output (a reasoning-style result)."""
    return StepOutput(result=text)


def _state(
    plan: PlannerPlanSchema,
    outputs: dict[int, list[StepOutput]],
    *,
    flagged: tuple[int, ...] = (),
    abandoned: PlanRevision | None = None,
) -> KleaAgentState:
    """Build a graph state whose plan, outputs, flags and history are set."""
    state = KleaAgentState()
    state.plan = cast("PlanSchema", plan)
    state.step_outputs = dict(outputs)
    state.evaluation = EvaluationSchema(
        evaluations=[
            StepEvaluation(
                step_number=number,
                verdict="step_incomplete",
                reason=f"step {number} not complete",
            )
            for number in flagged
        ]
    )
    state.plan_history = [abandoned] if abandoned is not None else []
    return state


def test_returns_plain_string():
    """The method returns the selector's text, not the render tuple."""
    state = _state(
        PlannerPlanSchema(step_list=[_step(1, status="done")]),
        {1: [_output("work")]},
    )

    assert isinstance(state.planner_evidence(), str)


def test_no_flag_matches_selector_text():
    """With no flags or history it equals the selector's default render."""
    plan = PlannerPlanSchema(
        step_list=[
            _step(1, status="done"),
            _step(2, status="done"),
            _step(3, depends_on=[2]),
        ]
    )
    outputs = {1: [_output("older")], 2: [_output("newest")]}
    state = _state(plan, outputs)

    assert state.planner_evidence() == render_plan_evidence(plan, outputs)
    assert "### Step 2\n" in state.planner_evidence()


def test_evaluator_flagged_matches_selector_text():
    """A ``step_incomplete`` verdict in state is passed through as a flag."""
    plan = PlannerPlanSchema(
        step_list=[
            _step(1, status="done"),
            _step(2, status="done"),
            _step(3, depends_on=[2]),
        ]
    )
    outputs = {1: [_output("flagged payload")], 2: [_output("last done")]}
    state = _state(plan, outputs, flagged=(1,))

    expected = render_plan_evidence(plan, outputs, flagged={1})
    assert state.planner_evidence() == expected
    assert "flagged payload" in state.planner_evidence()


def test_abandoned_revision_matches_selector_text():
    """The latest ``plan_history`` revision is passed through as ``abandoned``."""
    plan = PlannerPlanSchema(step_list=[_step(1), _step(2, status="done")])
    outputs = {1: [_output("old attempt payload")], 2: [_output("current")]}
    abandoned = PlanRevision(
        version=1,
        steps=[_step(1)],
        abandoned_reason="the old approach failed",
    )
    state = _state(plan, outputs, abandoned=abandoned)

    expected = render_plan_evidence(plan, outputs, abandoned=abandoned)
    assert state.planner_evidence() == expected
    assert "old attempt payload" in state.planner_evidence()


def test_no_recorded_outputs_returns_sentinel():
    """An empty recording degrades to the selector's ``NO_EVIDENCE`` sentinel."""
    state = _state(PlannerPlanSchema(step_list=[_step(1)]), {})

    assert state.planner_evidence() == NO_EVIDENCE
