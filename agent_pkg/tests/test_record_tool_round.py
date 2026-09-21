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
from klea_agent.schemas import KleaAgentState, PlanSchema, StepOutput, StepSchema
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
    state = KleaAgentState(plan=PlanSchema())
    state.step_outputs = {1: [StepOutput(result=_result())]}
    update = agent._record_tool_round(state, [_result(), _result()], [False, False])
    assert len(update["step_outputs"][1]) == 3
    assert update["tool_retry_counts"] == {}


def test_caps_step_outputs():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema())
    batch = [_result() for _ in range(KleaAgent.MAX_STEP_RESULTS + 5)]
    update = agent._record_tool_round(state, batch, [False] * len(batch))
    assert len(update["step_outputs"][1]) == KleaAgent.MAX_STEP_RESULTS


def test_error_round_increments_tool_retry_counts():
    agent = _agent()
    # Step 1 is done, so the current step is 2 (frontier, ADR-0041).
    state = KleaAgentState(
        plan=PlanSchema(
            step_list=[
                StepSchema(step_number=1, status="done"),
                StepSchema(step_number=2),
            ]
        )
    )
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
    state = KleaAgentState(plan=PlanSchema())
    update = agent._record_tool_round(state, [_error_result("no such file")], [False])
    assert update["replan_reason"] == "no such file"


def test_clean_round_clears_replan_reason():
    """A clean batch clears a stale replan reason (progress)."""
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(), replan_reason="old failure")
    update = agent._record_tool_round(state, [_result()], [False])
    assert update["replan_reason"] == ""


def test_record_picker_failure_writes_synthetic_observation_and_reason():
    """A deliberate picker failure becomes an is_error StepOutput + reason."""
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema())
    update = agent._record_picker_failure(state, "no listed tool can do this")
    assert update["replan_reason"] == "no listed tool can do this"
    assert update["tool_results"][0].is_error
    entries = update["step_outputs"][1]
    assert len(entries) == 1
    assert entries[0].tool == ""
    assert entries[0].result.is_error


def test_clean_round_prunes_earlier_errors():
    """Once a call succeeds, the step's superseded failures are dropped."""
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema())
    state.step_outputs = {1: [StepOutput(result=_error_result("old failure"))]}
    update = agent._record_tool_round(state, [_result()], [False])
    entries = update["step_outputs"][1]
    assert len(entries) == 1
    assert not entries[0].result.is_error


def test_error_round_keeps_earlier_errors():
    """A still-failing round keeps the accumulated errors as evidence."""
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema())
    state.step_outputs = {1: [StepOutput(result=_error_result("first"))]}
    update = agent._record_tool_round(state, [_error_result("second")], [False])
    assert len(update["step_outputs"][1]) == 2


def test_records_tool_name_and_displayed_flag():
    """Each StepOutput carries the tool name and the displayed flag."""
    agent = _agent()
    state = KleaAgentState(
        plan=PlanSchema(),
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
