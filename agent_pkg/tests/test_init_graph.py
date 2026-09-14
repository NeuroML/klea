#!/usr/bin/env python3
"""
Tests for the agent InitGraphState node.

File: tests/test_init_graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

import pytest
from klea_agent.nodes.init_graph import InitGraphState
from klea_agent.schemas import (
    EvaluationSchema,
    KleaAgentState,
    Mode,
    PlanSchema,
    RouteSchema,
    StepSchema,
)
from langchain_core.messages import HumanMessage


@pytest.mark.asyncio
async def test_init_resets_ephemeral_and_preserves_session_fields(monkeypatch):
    """Per-turn fields reset; session-scoped fields are left untouched."""
    node = InitGraphState(logging.getLogger("test"), "Initializing")
    monkeypatch.setattr(node, "write_custom_stream", lambda ev: None)

    state = KleaAgentState(
        query="q",
        context_summary="summary",
        summarised_till=3,
        mode=Mode(resolved="general"),
        plan=PlanSchema(step_list=[StepSchema(description="x")]),
        tool_retry_counts={0: 2},
        step_attempt_counts={0: 1},
        plan_revisions=2,
        tool_rounds=4,
        failure_reason="boom",
        evaluation=EvaluationSchema(evaluation="step_done"),
    )
    update = await node.execute(state)

    assert update["plan"].step_list == []
    assert update["goal"].goal == ""
    assert update["tool_retry_counts"] == {}
    assert update["step_attempt_counts"] == {}
    assert update["plan_revisions"] == 0
    assert update["tool_rounds"] == 0
    assert update["failure_reason"] == ""
    assert update["route"] == RouteSchema()
    assert update["evaluation"].evaluation == "plan_done"
    # Session-scoped fields are not in the reset dict, so the graph keeps them.
    assert "mode" not in update
    assert "context_summary" not in update
    # The query is appended to the run history (messages is preserved + query).
    assert len(update["messages"]) == 1
    assert isinstance(update["messages"][-1], HumanMessage)
    assert update["messages"][-1].content == "q"
