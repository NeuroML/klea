#!/usr/bin/env python3
"""
Tests for the agent's tool-call binder.

File: tests/test_tool_call_binder.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_agent.nodes.tool_call_binder import ToolCallBinder
from klea_agent.schemas import (
    EvaluationSchema,
    KleaAgentState,
    PlanSchema,
    StepEvaluation,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolCallSchema
from klea_utils.nodes.context import LLMNodeContext
from mcp.types import TextContent


def _binder() -> ToolCallBinder:
    return ToolCallBinder(
        logger=logging.getLogger("test"),
        label="Binding tool calls",
        llm_models={"tool_picker": object()},
        tools_info={},
    )


def _state(evaluation: EvaluationSchema | None = None) -> KleaAgentState:
    return KleaAgentState(
        query="q",
        plan=PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="read the file",
                    success_criteria="the content is available",
                    suggested_tools=["read_file"],
                )
            ],
            status="in_progress",
        ),
        evaluation=evaluation or EvaluationSchema(),
    )


def test_binder_uses_its_prompt_and_enables_binding_mode():
    binder = ToolCallBinder(
        logger=logging.getLogger("test"),
        label="Binding tool calls",
        llm_models={"tool_picker": object()},
        tools_info={},
    )

    assert binder.prompt_prefix == "ToolCallBinder"
    assert binder._binding_enabled() is True
    assert binder.prompt_registry_location.name == "prompts"
    assert (binder.prompt_registry_location / "ToolCallBinder_system.md").is_file()
    assert "tool-call binder" in binder._load_prompt_file("ToolCallBinder_system")


def test_binder_step_feedback_includes_evaluator_incomplete_reason():
    """The binder sees why the evaluator judged the current step incomplete."""
    evaluation = EvaluationSchema(
        evaluations=[
            StepEvaluation(
                step_number=1,
                verdict="step_incomplete",
                reason="state the mechanism explicitly",
            )
        ]
    )
    variables = _binder()._get_prompt_variables(_state(evaluation), LLMNodeContext())

    assert "picker_feedback" not in variables
    feedback = variables["step_feedback"]
    assert "state the mechanism explicitly" in feedback
    assert "do not repeat" in feedback.lower()


def test_binder_step_feedback_ignores_other_steps_and_verdicts():
    """Only the current binding group's incomplete verdicts are surfaced."""
    evaluation = EvaluationSchema(
        evaluations=[
            StepEvaluation(step_number=1, verdict="step_done", reason="ok"),
            StepEvaluation(
                step_number=2,
                verdict="step_incomplete",
                reason="another step",
            ),
        ]
    )
    variables = _binder()._get_prompt_variables(_state(evaluation), LLMNodeContext())

    assert variables["step_feedback"] == ""


def test_binder_step_feedback_combines_evaluator_and_call_failures():
    """Semantic and mechanical feedback share the one step_feedback block."""
    evaluation = EvaluationSchema(
        evaluations=[
            StepEvaluation(
                step_number=1,
                verdict="step_incomplete",
                reason="the criterion is not met",
            )
        ]
    )
    error = CallToolResult(
        content=[TextContent(type="text", text="permission denied")],
        structured_content=None,
        meta=None,
        data=None,
        is_error=True,
    )
    state = _state(evaluation)
    state.tool_calls = [ToolCallSchema(tool="read_file", args={"path": "a.txt"})]
    state.tool_results = [error]

    feedback = _binder()._get_prompt_variables(state, LLMNodeContext())["step_feedback"]

    assert "the criterion is not met" in feedback
    assert "permission denied" in feedback
