#!/usr/bin/env python3
"""
Tests for the agent schemas (literals, defaults) and msgpack checkpointing.

File: tests/test_schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import pytest
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlannerOutput,
    PlanSchema,
    StepSchema,
)
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer


class TestPlanSchema:
    """Entry routing statuses on the plan."""

    @pytest.mark.parametrize(
        "status", ["not_needed", "in_review", "in_progress", "unplannable"]
    )
    def test_entry_statuses_accepted(self, status):
        assert PlanSchema(status=status).status == status


class TestEvaluationSchema:
    """The evaluator verdict gains an explicit abort outcome."""

    def test_next_step_accepts_abort(self):
        assert EvaluationSchema(next_step="abort").next_step == "abort"


class TestStateDefaults:
    """New budget/history fields default cleanly."""

    def test_new_fields_default(self):
        state = KleaAgentState()
        assert state.tool_retry_counts == {}
        assert state.step_attempt_counts == {}
        assert state.plan_revisions == 0
        assert state.turn_iterations == 0
        assert state.failure_reason == ""
        assert state.human_feedback == ""


class TestCheckpointMsgpack:
    """Nested state models round-trip through the checkpoint serializer."""

    def test_nested_models_roundtrip(self):
        allowed = KleaAgent.__new__(KleaAgent).get_allowed_msgpack_modules()
        serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
        payload = {
            "planner_output": PlannerOutput(
                goal=GoalSchema(goal="g", success_criteria="c"),
                plan=PlanSchema(
                    step_list=[StepSchema(description="s")], status="in_progress"
                ),
                direct_answer="",
            ),
            "evaluation": EvaluationSchema(next_step="abort"),
            "step_attempt_counts": {0: 2},
            "plan_revisions": 1,
            "turn_iterations": 3,
            "failure_reason": "x",
        }
        type_name, data = serde.dumps_typed(payload)
        assert serde.loads_typed((type_name, data)) == payload
