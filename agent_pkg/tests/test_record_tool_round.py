#!/usr/bin/env python3
"""
Tests for the agent's post-dispatch tool-round recorder.

File: tests/test_record_tool_round.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import KleaAgentState, PlanSchema, StepOutput
from klea_utils.mcp.schemas import ToolCallSchema
from mcp.types import TextContent


def _result(is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[], structured_content=None, meta=None, is_error=is_error
    )


def _error_result(text: str) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=None,
        meta=None,
        is_error=True,
    )


def _agent() -> KleaAgent:
    agent = KleaAgent.__new__(KleaAgent)
    agent.logger = logging.getLogger("test")
    return agent


def test_accumulates_step_outputs():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=0))
    state.step_outputs = {1: [StepOutput(result=_result())]}
    update = agent._record_tool_round(state, [_result(), _result()], [False, False])
    assert len(update["step_outputs"][1]) == 3
    assert update["tool_retry_counts"] == {}


def test_caps_step_outputs():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=0))
    batch = [_result() for _ in range(KleaAgent.MAX_STEP_RESULTS + 5)]
    update = agent._record_tool_round(state, batch, [False] * len(batch))
    assert len(update["step_outputs"][1]) == KleaAgent.MAX_STEP_RESULTS


def test_error_round_increments_tool_retry_counts():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=1))
    update = agent._record_tool_round(state, [_result(is_error=True)], [False])
    assert update["tool_retry_counts"] == {2: 1}
    assert update["step_outputs"][2]


def test_increments_tool_rounds():
    agent = _agent()
    state = KleaAgentState(tool_rounds=2)
    update = agent._record_tool_round(state, [_result()], [False])
    assert update["tool_rounds"] == 3


def test_failed_round_sets_replan_reason():
    """A failed batch exposes its error text as the unified replan reason."""
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=0))
    update = agent._record_tool_round(state, [_error_result("no such file")], [False])
    assert update["replan_reason"] == "no such file"


def test_clean_round_clears_replan_reason():
    """A clean batch clears a stale replan reason (progress)."""
    agent = _agent()
    state = KleaAgentState(
        plan=PlanSchema(current_step_index=0), replan_reason="old failure"
    )
    update = agent._record_tool_round(state, [_result()], [False])
    assert update["replan_reason"] == ""


def test_records_tool_name_and_displayed_flag():
    """Each StepOutput carries the tool name and the displayed flag."""
    agent = _agent()
    state = KleaAgentState(
        plan=PlanSchema(current_step_index=0),
        tool_calls=[
            ToolCallSchema(tool="edit_file"),
            ToolCallSchema(tool="run_command"),
        ],
    )
    update = agent._record_tool_round(state, [_result(), _result()], [True, False])

    entries = update["step_outputs"][1]
    assert [(e.tool, e.displayed) for e in entries] == [
        ("edit_file", True),
        ("run_command", False),
    ]
