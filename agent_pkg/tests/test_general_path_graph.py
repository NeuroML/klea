#!/usr/bin/env python3
"""
Structural test: the general-path graph matches ADR-0035.

Compiles the graph without a full setup (dummy models, no MCP client) and
asserts the entry routing and work loop, and that no scientific or old
exploration stages are present on the general path (ADR-0036).

File: tests/test_general_path_graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from typing import Literal

import pytest
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import (
    KleaAgentState,
    PlanSchema,
    StepSchema,
)
from klea_utils.llm import LLMModel
from klea_utils.mcp.schemas import ToolCallSchema


async def _compile(monkeypatch):
    """Compile the agent graph with dummy models and no MCP client."""
    agent = KleaAgent(checkpoint="inmemory")
    agent.llm_models = {
        "chat": LLMModel(instance=None, model_name=""),
        "plan": LLMModel(instance=None, model_name=""),
        "guard": LLMModel(instance=None, model_name=""),
    }
    agent.mcp_client = None
    agent.mcp_tools = None
    agent.tools_info = {}
    agent.retriever_config = None
    monkeypatch.setattr(agent, "_export_graph_png", lambda filename: None)
    await agent._create_graph()
    assert agent.graph is not None
    return agent.graph.get_graph()


@pytest.mark.asyncio
async def test_general_path_work_loop(monkeypatch):
    """The narrow router splits chat | task; the task path runs the loop."""
    graph = await _compile(monkeypatch)
    node_names = {n.name for n in graph.nodes.values()}
    edges = {(e.source, e.target) for e in graph.edges}

    for expected in (
        "Deciding route",
        "Planning",
        "Awaiting review",
        "Selecting tools",
        "Running tools",
        "Reasoning",
        "Evaluating",
        "Composing answer",
        "Preparing response",
    ):
        assert expected in node_names

    assert "Setting goal" not in node_names

    # Entry: guard -> route decision; chat -> answer, task -> planner.
    assert ("Checking safety", "Deciding route") in edges
    assert ("Deciding route", "Preparing response") in edges  # chat
    assert ("Deciding route", "Planning") in edges  # task

    # Planner routing on plan.status and step kind.
    assert ("Planning", "Composing answer") in edges  # unplannable -> failure
    assert ("Planning", "Awaiting review") in edges  # in_review
    assert ("Planning", "Selecting tools") in edges  # in_progress, tool step
    assert ("Planning", "Reasoning") in edges  # in_progress, reasoning step

    # Human review loops back to the Planner (ADR-0035).
    assert ("Awaiting review", "Planning") in edges

    # Work loop (ADR-0035): act batch -> deterministic triage -> evaluator.
    assert ("Selecting tools", "Running tools") in edges  # dispatch (incl. empty round)
    assert ("Selecting tools", "Selecting tools") in edges  # empty pick -> retry
    assert ("Selecting tools", "Planning") in edges  # deliberate/exhausted pick failure
    assert ("Running tools", "Selecting tools") in edges  # retry
    assert ("Running tools", "Evaluating") in edges  # evaluate
    assert ("Running tools", "Planning") in edges  # replan
    assert ("Reasoning", "Evaluating") in edges  # reasoning step judged
    assert ("Evaluating", "Selecting tools") in edges  # step_incomplete/step_done, tool
    assert ("Evaluating", "Reasoning") in edges  # step_incomplete/step_done, reasoning
    assert ("Evaluating", "Planning") in edges  # need_replan
    assert ("Evaluating", "Composing answer") in edges  # plan_done/abort
    assert ("Composing answer", "Preparing response") in edges


@pytest.mark.asyncio
async def test_general_path_has_no_scientific_or_exploration_stages(monkeypatch):
    """The general path carries no exploration or epistemic stages."""
    graph = await _compile(monkeypatch)
    node_names = {n.name for n in graph.nodes.values()}
    # Old prototype stages removed.
    assert "Exploring" not in node_names
    assert "Routing tools" not in node_names
    # No mandatory scientific stages (ADR-0036).
    lowered = {n.lower() for n in node_names}
    assert not any("retriev" in n for n in lowered)
    assert not any("verif" in n for n in lowered)


@pytest.mark.asyncio
async def test_picker_router_retries_bad_names(monkeypatch):
    """No usable call (empty list or empty names) retries the picker.

    Unknown-but-non-empty names are left to dispatch; only empty/whitespace
    names and empty lists are picker failures.
    """
    agent = KleaAgent(checkpoint="inmemory")
    state = KleaAgentState()

    # Usable name -> dispatch regardless of the counter.
    state.tool_calls = [ToolCallSchema(tool="run")]
    state.picker_attempts = 5
    assert await agent._picker_router(state) == "dispatch"

    # Empty list within budget -> retry.
    state.tool_calls = []
    state.picker_attempts = 1
    assert await agent._picker_router(state) == "retry_picker"

    # Empty-name list within budget -> retry (same as an empty list).
    state.tool_calls = [ToolCallSchema(tool="")]
    state.picker_attempts = 1
    assert await agent._picker_router(state) == "retry_picker"

    # Budget exhausted -> escalate to the Planner.
    state.picker_attempts = agent._max_picker_retries + 1
    assert await agent._picker_router(state) == "replan"

    # An unknown-but-non-empty name is a dispatch concern, not a retry.
    state.tool_calls = [ToolCallSchema(tool="made_up")]
    state.picker_attempts = agent._max_picker_retries + 1
    assert await agent._picker_router(state) == "dispatch"

    # A deliberate failure (empty tool + reason) goes straight to the Planner.
    state.tool_calls = [ToolCallSchema(tool="", reason="no tool can do this")]
    state.picker_attempts = 0
    assert await agent._picker_router(state) == "replan"


def _plan_with_step(
    kind: Literal["tool", "reasoning"], status: Literal["in_progress"] = "in_progress"
) -> KleaAgentState:
    state = KleaAgentState()
    state.plan = PlanSchema(
        step_list=[StepSchema(step_number=1, description="s", kind=kind)],
        status=status,
    )
    return state


@pytest.mark.asyncio
async def test_planner_router_dispatches_by_step_kind():
    """in_progress routes to the picker or the reasoning node by step kind."""
    agent = KleaAgent(checkpoint="inmemory")
    assert await agent._planner_router(_plan_with_step("tool")) == "tool"
    assert await agent._planner_router(_plan_with_step("reasoning")) == "reasoning"


@pytest.mark.asyncio
async def test_evaluation_router_routes_on_plan_state():
    """The Evaluator leaves the plan in a routing state (ADR-0041)."""
    agent = KleaAgent(checkpoint="inmemory")

    # in_progress with no replan reason: dispatch the next runnable step.
    assert await agent._evaluation_router(_plan_with_step("reasoning")) == "reasoning"
    assert await agent._evaluation_router(_plan_with_step("tool")) == "tool"

    replan = _plan_with_step("tool")
    replan.replan_reason = "no progress"
    assert await agent._evaluation_router(replan) == "need_replan"

    completed = _plan_with_step("tool")
    completed.plan.status = "completed"
    assert await agent._evaluation_router(completed) == "plan_done"

    aborted = _plan_with_step("tool")
    aborted.plan.status = "aborted"
    assert await agent._evaluation_router(aborted) == "abort"
