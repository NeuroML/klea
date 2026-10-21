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
    PlanRevision,
    PlanSchema,
    RouteSchema,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolCallSchema
from langchain_core.messages import AIMessage, HumanMessage


@pytest.mark.asyncio
async def test_init_resets_ephemeral_and_preserves_session_fields(
    monkeypatch, tmp_path
):
    """Per-turn fields reset; session-scoped fields are left untouched."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "AGENTS.md").write_text("project rules")
    node = InitGraphState(logging.getLogger("test"), "Initializing")
    monkeypatch.setattr(node, "write_custom_stream", lambda ev: None)

    state = KleaAgentState(
        query="q",
        context_summary="summary",
        summarised_till=3,
        mode=Mode(resolved="general"),
        plan=PlanSchema(
            step_list=[StepSchema(description="x")],
            plan_version=2,
            human_feedback_rounds=1,
            automated_plan_revisions=1,
        ),
        plan_history=[PlanRevision(version=1, abandoned_reason="old plan")],
        tool_retry_counts={0: 2},
        step_attempt_counts={0: 1},
        binding_calls=[ToolCallSchema(tool="read_file", step=1)],
        messages_internal=[AIMessage(content="Plan v1")],
        summarised_internal_till=7,
        failure_reason="boom",
        evaluation=EvaluationSchema(overall="abort"),
        replan_reason="tool failed",
        allowed_dirs=["/tmp/approved"],
        allowed_files=["/tmp/approved/.env"],
    )
    update = await node.execute(state)

    assert update["plan"].step_list == []
    assert update["plan"].plan_version == 0
    assert update["plan"].human_feedback_rounds == 0
    assert update["plan"].automated_plan_revisions == 0
    assert update["plan_history"] == []
    assert update["goal"].goal == ""
    assert update["tool_retry_counts"] == {}
    assert update["step_attempt_counts"] == {}
    assert update["binding_calls"] == []
    # Internal run history is session-scoped and must survive a new turn.
    assert "messages_internal" not in update
    assert "summarised_internal_till" not in update
    assert update["failure_reason"] == ""
    assert update["replan_reason"] == ""
    assert update["human_input"] == {}
    assert update["route"] == RouteSchema()
    assert update["evaluation"].overall == ""
    assert update["evaluation"].evaluations == []
    # The project instruction file is loaded into session-scoped discovery.
    assert update["discovery_persistent"].items[0].source == "AGENTS.md"
    assert update["discovery_persistent"].items[0].content == "project rules"
    # Session-scoped fields are not in the reset dict, so the graph keeps them.
    assert "mode" not in update
    assert "context_summary" not in update
    # Artefacts are session-scoped: never cleared by the per-run reset.
    assert "artefacts" not in update
    # Session-approved directories/files survive across turns too.
    assert "allowed_dirs" not in update
    assert "allowed_files" not in update
    # The query is appended to the run history (messages is preserved + query).
    assert len(update["messages"]) == 1
    assert isinstance(update["messages"][-1], HumanMessage)
    assert update["messages"][-1].content == "q"


@pytest.mark.asyncio
async def test_init_reads_discovery_from_project_root(monkeypatch, tmp_path):
    """The instruction file is read from ``state.project_root`` when set.

    ``InitGraphState`` must not reset ``project_root`` (session-scoped).
    """
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    root = tmp_path / "root"
    root.mkdir()
    (root / "AGENTS.md").write_text("root rules")
    # cwd has no instruction file, so only the session root can supply it.
    monkeypatch.chdir(cwd)

    node = InitGraphState(logging.getLogger("test"), "Initializing")
    monkeypatch.setattr(node, "write_custom_stream", lambda ev: None)

    update = await node.execute(KleaAgentState(query="q", project_root=str(root)))

    assert update["discovery_persistent"].items[0].source == "AGENTS.md"
    assert update["discovery_persistent"].items[0].content == "root rules"
    # project_root is session-scoped: never reset per turn.
    assert "project_root" not in update
