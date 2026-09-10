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
        step_retry_counts={0: 2},
        route=RouteSchema(route="act"),
        evaluation=EvaluationSchema(next_step="step_done"),
    )
    update = await node.execute(state)

    assert update["plan"].step_list == []
    assert update["goal"].goal == ""
    assert update["step_retry_counts"] == {}
    assert update["route"].route == "answer"
    assert update["evaluation"].next_step == "plan_done"
    # Session-scoped fields are not in the reset dict, so the graph keeps them.
    assert "mode" not in update
    assert "messages" not in update
    assert "context_summary" not in update
