#!/usr/bin/env python3
"""
Tests for the agent schemas (literals, defaults) and msgpack checkpointing.

File: tests/test_schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import pytest
from fastmcp.client.client import CallToolResult
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlannerOutput,
    PlannerPlanSchema,
    PlanSchema,
    RouteSchema,
    StepOutput,
    StepSchema,
)
from klea_utils.graph.schemas import TokenUsage
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.mcp.schemas import ToolCallSchema
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import StateGraph
from mcp.types import TextContent


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
        assert state.access_level == "full"

    def test_shared_and_app_channels_present(self):
        channels = StateGraph(KleaAgentState).channels
        assert {"messages", "tool_calls", "tool_results", "context_summary"} <= set(
            channels
        )
        assert {"mode", "plan", "route"} <= set(channels)
        assert isinstance(channels["usage_metrics"], BinaryOperatorAggregate)


class TestStepOutputRender:
    """StepOutput.render and KleaAgentState.observations_text."""

    @staticmethod
    def _result() -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text='{"a": 1}')],
            structured_content={"a": 1},
            meta=None,
        )

    def test_render_includes_tool_and_displayed(self):
        entry = StepOutput(result=self._result(), tool="edit_file", displayed=True)
        rendered = entry.render()
        assert rendered.startswith("### edit_file (displayed_to_user: yes)")
        assert '{"a": 1}' in rendered

    def test_observations_text_groups_by_step(self):
        state = KleaAgentState(
            step_outputs={
                2: [
                    StepOutput(result=self._result(), tool="edit_file", displayed=True),
                    StepOutput(
                        result=self._result(), tool="run_command", displayed=False
                    ),
                ]
            }
        )
        text = state.observations_text()
        assert text.startswith("Step 2:")
        assert "### edit_file (displayed_to_user: yes)" in text
        assert "### run_command (displayed_to_user: no)" in text

    def test_observations_text_empty(self):
        assert KleaAgentState().observations_text() == "(no observations)"


class TestPlannerPlanSchema:
    """The Planner sees only the statuses it may set (ADR-0035 2026-09-19)."""

    def test_default_plan_status_is_in_progress(self):
        assert PlannerOutput().plan.status == "in_progress"

    def test_planner_status_enum_excludes_runtime_statuses(self):
        status = PlannerPlanSchema.model_json_schema()["properties"]["status"]
        assert set(status["enum"]) == {"in_progress", "in_review", "unplannable"}


class TestCheckpointMsgpack:
    """Nested state models round-trip through the checkpoint serializer."""

    def test_nested_models_roundtrip(self):
        allowed = KleaAgent.__new__(KleaAgent).get_allowed_msgpack_modules()
        serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
        payload = {
            "route": RouteSchema(route="chat", answer="hi"),
            "planner_output": PlannerOutput(
                goal=GoalSchema(goal="g", success_criteria="c"),
                plan=PlannerPlanSchema(
                    step_list=[StepSchema(description="s")], status="in_progress"
                ),
            ),
            "evaluation": EvaluationSchema(evaluation="abort"),
            "tool_calls": [ToolCallSchema(tool="list_files", args={"path": "."})],
            "step_outputs": {
                1: [
                    StepOutput(
                        result=CallToolResult(
                            content=[],
                            structured_content=None,
                            meta=None,
                        ),
                        tool="list_files",
                        displayed=False,
                    )
                ]
            },
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
