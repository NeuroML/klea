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
    RouteSchema,
    StepSchema,
)
from klea_utils.graph.schemas import TokenUsage
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.mcp.schemas import ToolCallSchema
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import StateGraph


class TestRouteSchema:
    """Entry routing defaults to the fail-closed ``task``."""

    def test_defaults_to_task(self):
        assert RouteSchema().route == "task"

    @pytest.mark.parametrize("route", ["chat", "task"])
    def test_accepts_routes(self, route):
        assert RouteSchema(route=route).route == route


class TestPlanSchema:
    """Entry routing statuses on the plan."""

    @pytest.mark.parametrize("status", ["in_review", "in_progress", "unplannable"])
    def test_entry_statuses_accepted(self, status):
        assert PlanSchema(status=status).status == status


class TestEvaluationSchema:
    """The evaluator verdict gains an explicit abort outcome."""

    def test_evaluation_accepts_abort(self):
        assert EvaluationSchema(evaluation="abort").evaluation == "abort"


class TestStateDefaults:
    """New budget/history fields default cleanly."""

    def test_new_fields_default(self):
        state = KleaAgentState()
        assert state.tool_retry_counts == {}
        assert state.step_attempt_counts == {}
        assert state.plan_revisions == 0
        assert state.tool_rounds == 0
        assert state.failure_reason == ""
        assert state.human_feedback == ""
        assert state.route == RouteSchema()


class TestSharedStateInheritance:
    """KleaAgentState extends the shared BaseGraphSchema (ADR-0032)."""

    def test_is_base_graph_schema(self):
        assert issubclass(KleaAgentState, BaseGraphSchema)

    def test_shared_defaults(self):
        state = KleaAgentState()
        assert state.query == ""
        assert state.messages == []
        assert state.guard_decision == "safe"
        assert state.summarised_till == 0
        assert state.message_for_user == ""
        assert state.tool_calls == []
        assert state.tool_results == []
        assert state.usage_metrics == TokenUsage()

    def test_shared_and_app_channels_present(self):
        channels = StateGraph(KleaAgentState).channels
        assert {"messages", "tool_calls", "tool_results", "context_summary"} <= set(
            channels
        )
        assert {"mode", "plan", "route"} <= set(channels)
        assert isinstance(channels["usage_metrics"], BinaryOperatorAggregate)


class TestCheckpointMsgpack:
    """Nested state models round-trip through the checkpoint serializer."""

    def test_nested_models_roundtrip(self):
        allowed = KleaAgent.__new__(KleaAgent).get_allowed_msgpack_modules()
        serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
        payload = {
            "route": RouteSchema(route="chat", answer="hi"),
            "planner_output": PlannerOutput(
                goal=GoalSchema(goal="g", success_criteria="c"),
                plan=PlanSchema(
                    step_list=[StepSchema(description="s")], status="in_progress"
                ),
            ),
            "evaluation": EvaluationSchema(evaluation="abort"),
            "tool_calls": [ToolCallSchema(tool="list_files", args={"path": "."})],
            "usage_metrics": TokenUsage(
                input_tokens=1, output_tokens=2, total_tokens=3
            ),
            "step_attempt_counts": {0: 2},
            "plan_revisions": 1,
            "tool_rounds": 3,
            "failure_reason": "x",
        }
        type_name, data = serde.dumps_typed(payload)
        assert serde.loads_typed((type_name, data)) == payload
