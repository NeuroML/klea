#!/usr/bin/env python3
"""
Tests for the agent's post-dispatch tool-round recorder.

File: tests/test_record_tool_round.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import KleaAgentState, PlanSchema


def _result(is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[], structured_content=None, meta=None, is_error=is_error
    )


def _agent() -> KleaAgent:
    agent = KleaAgent.__new__(KleaAgent)
    agent.logger = logging.getLogger("test")
    return agent


def test_accumulates_step_outputs():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=0))
    state.step_outputs = {0: [_result()]}
    update = agent._record_tool_round(state, [_result(), _result()])
    assert len(update["step_outputs"][0]) == 3
    assert update["tool_retry_counts"] == {}


def test_caps_step_outputs():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=0))
    batch = [_result() for _ in range(KleaAgent.MAX_STEP_RESULTS + 5)]
    update = agent._record_tool_round(state, batch)
    assert len(update["step_outputs"][0]) == KleaAgent.MAX_STEP_RESULTS


def test_error_round_increments_tool_retry_counts():
    agent = _agent()
    state = KleaAgentState(plan=PlanSchema(current_step_index=1))
    update = agent._record_tool_round(state, [_result(is_error=True)])
    assert update["tool_retry_counts"] == {1: 1}
    assert update["step_outputs"][1]


def test_increments_tool_rounds():
    agent = _agent()
    state = KleaAgentState(tool_rounds=2)
    update = agent._record_tool_round(state, [_result()])
    assert update["tool_rounds"] == 3
