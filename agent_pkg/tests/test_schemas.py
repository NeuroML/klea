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
    KleaAgentState,
    PlanSchema,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolCallSchema, ToolCallsSchema
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from pydantic import ValidationError


class TestToolCallsSchema:
    """Picker output: tool calls plus the explicit suitability assessment."""

    def test_defaults_are_tools(self):
        schema = ToolCallsSchema()
        assert schema.tool_calls == []
        assert schema.assessment == "tools"
        assert schema.reason == ""

    @pytest.mark.parametrize("assessment", ["tools", "reasoning", "unavailable"])
    def test_accepts_known_assessments(self, assessment):
        schema = ToolCallsSchema(assessment=assessment)
        assert schema.assessment == assessment

    def test_rejects_unknown_assessment(self):
        with pytest.raises(ValidationError):
            ToolCallsSchema.model_validate({"assessment": "nonsense"})


class TestStepAndPlanSchema:
    """New step kind and plan status values."""

    def test_step_kind_defaults_to_tool(self):
        assert StepSchema().kind == "tool"

    def test_step_kind_accepts_reasoning(self):
        assert StepSchema(kind="reasoning").kind == "reasoning"

    def test_step_kind_rejects_unknown(self):
        with pytest.raises(ValidationError):
            StepSchema.model_validate({"kind": "chat"})

    def test_plan_status_accepts_unplannable(self):
        assert PlanSchema(status="unplannable").status == "unplannable"


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
        assert state.tool_selection == ToolCallsSchema()


class TestCheckpointMsgpack:
    """Nested state models round-trip and are in the checkpoint allowlist."""

    def test_models_are_allowlisted(self):
        allowed = KleaAgent.__new__(KleaAgent).get_allowed_msgpack_modules()
        assert ToolCallsSchema in allowed

    def test_nested_models_roundtrip(self):
        allowed = KleaAgent.__new__(KleaAgent).get_allowed_msgpack_modules()
        serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
        payload = {
            "tool_selection": ToolCallsSchema(
                assessment="reasoning",
                reason="no tool needed",
                tool_calls=[ToolCallSchema(tool="list_files")],
            ),
            "plan": PlanSchema(
                step_list=[StepSchema(description="s", kind="reasoning")],
                status="unplannable",
            ),
            "evaluation": EvaluationSchema(next_step="abort"),
            "step_attempt_counts": {0: 2},
            "plan_revisions": 1,
            "turn_iterations": 3,
            "failure_reason": "x",
        }
        type_name, data = serde.dumps_typed(payload)
        assert serde.loads_typed((type_name, data)) == payload
