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
    RouteSchema,
    StepSchema,
)
from klea_utils.llm import LLMModel
from klea_utils.mcp.schemas import ToolCallSchema


async def _build_agent(monkeypatch) -> KleaAgent:
    """Build the agent with dummy models and no MCP client."""
    agent = KleaAgent(checkpoint="inmemory")
    agent.llm_models = {
        "chat": LLMModel(instance=None, model_name=""),
        "plan": LLMModel(instance=None, model_name=""),
        "tool_picker": LLMModel(instance=None, model_name=""),
        "guard": LLMModel(instance=None, model_name=""),
    }
    agent.mcp_client = None
    agent.mcp_tools = None
    agent.tools_info = {}
    agent.retriever_config = None
    monkeypatch.setattr(agent, "_export_graph_png", lambda filename: None)
    await agent._create_graph()
    assert agent.graph is not None
    return agent


async def _compile(monkeypatch):
    """Compile the agent graph with dummy models and no MCP client."""
    agent = await _build_agent(monkeypatch)
    graph = agent.graph
    assert graph is not None
    return graph.get_graph()


@pytest.mark.asyncio
async def test_tools_caller_asks_for_path_permission(monkeypatch):
    """The agent opts into interactive path approval (ADR-0007 update 2026-10-08)."""
    agent = await _build_agent(monkeypatch)
    assert agent._tools_caller_node._permission_policy == "ask"


@pytest.mark.asyncio
async def test_general_path_work_loop(monkeypatch):
    """The narrow router splits chat | task; the task path runs the loop."""
    graph = await _compile(monkeypatch)
    node_names = {n.name for n in graph.nodes.values()}
    edges = {(e.source, e.target) for e in graph.edges}

    for expected in (
        "Deciding route",
        "Planning",
        "Preparing review",
        "Awaiting review",
        "Awaiting input",
        "Cancelled",
        "Binding tool calls",
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
    assert ("Planning", "Preparing review") in edges  # review step at the frontier
    assert ("Planning", "Binding tool calls") in edges  # in_progress, tool step
    assert ("Planning", "Reasoning") in edges  # in_progress, reasoning step
    assert ("Planning", "Deciding route") in edges  # chat -> forced router re-entry

    # Human-in-the-loop (ADR-0046/ADR-0051): both await nodes loop back to the
    # Planner; a review approval can reach a further review step; a cancel
    # routes to the terminal "Cancelled" node.  Every review gate is reached
    # through the progress-digest node, never directly.
    assert ("Preparing review", "Awaiting review") in edges
    assert ("Awaiting review", "Planning") in edges
    assert ("Awaiting review", "Preparing review") in edges  # further review step
    assert ("Planning", "Awaiting review") not in edges
    assert ("Evaluating", "Awaiting review") not in edges
    assert ("Awaiting review", "Awaiting review") not in edges
    assert ("Planning", "Awaiting input") in edges  # needs_input
    assert ("Awaiting input", "Planning") in edges
    assert ("Awaiting review", "Cancelled") in edges
    assert ("Awaiting input", "Cancelled") in edges

    # Work loop (ADR-0035): act batch -> deterministic triage -> evaluator.
    assert (
        "Binding tool calls",
        "Running tools",
    ) in edges  # dispatch (incl. empty round)
    assert ("Binding tool calls", "Binding tool calls") in edges  # empty pick -> retry
    assert (
        "Binding tool calls",
        "Planning",
    ) in edges  # deliberate/exhausted pick failure
    assert ("Running tools", "Binding tool calls") in edges  # retry
    assert ("Running tools", "Evaluating") in edges  # evaluate
    assert ("Running tools", "Planning") in edges  # replan
    assert ("Reasoning", "Evaluating") in edges  # reasoning step judged
    assert (
        "Evaluating",
        "Binding tool calls",
    ) in edges  # step_incomplete/step_done, tool
    assert ("Evaluating", "Reasoning") in edges  # step_incomplete/step_done, reasoning
    assert ("Evaluating", "Preparing review") in edges  # next frontier head is review
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


@pytest.mark.asyncio
async def test_binder_router_binds_groups_before_dispatch(monkeypatch):
    """A usable group routes back to the binder until the batch is bound."""
    agent = KleaAgent(checkpoint="inmemory")
    state = KleaAgentState()
    state.plan = PlanSchema(
        step_list=[
            StepSchema(step_number=1, description="read a"),
            StepSchema(step_number=2, description="read b"),
        ],
        status="in_progress",
    )
    first = ToolCallSchema(tool="read_file", step=1)
    state.tool_calls = [first]
    state.binding_calls = [first]

    assert await agent._picker_router(state) == "bind_next"

    state.binding_calls.append(ToolCallSchema(tool="read_file", step=2))
    assert await agent._picker_router(state) == "dispatch"


def _plan_with_step(
    kind: Literal["tool", "reasoning", "review"],
    status: Literal["in_progress"] = "in_progress",
) -> KleaAgentState:
    state = KleaAgentState()
    state.plan = PlanSchema(
        step_list=[StepSchema(step_number=1, description="s", kind=kind)],
        status=status,
    )
    return state


@pytest.mark.asyncio
async def test_planner_router_dispatches_by_step_kind():
    """in_progress routes to the picker, reasoning, or review by step kind."""
    agent = KleaAgent(checkpoint="inmemory")
    assert await agent._planner_router(_plan_with_step("tool")) == "tool"
    assert await agent._planner_router(_plan_with_step("reasoning")) == "reasoning"
    # A review that gates pending work dispatches to the review node.
    review = KleaAgentState()
    review.plan = PlanSchema(
        step_list=[
            StepSchema(step_number=1, description="Review the plan", kind="review"),
            StepSchema(step_number=2, description="work", kind="tool"),
        ],
        status="in_progress",
    )
    assert await agent._planner_router(review) == "review"


@pytest.mark.asyncio
async def test_planner_router_resolves_a_trailing_review():
    """A review with no pending work after it resolves to the answer."""
    agent = KleaAgent(checkpoint="inmemory")
    assert await agent._planner_router(_plan_with_step("review")) == "plan_done"


@pytest.mark.asyncio
async def test_review_router_routes_on_the_review_step():
    """A revision (feedback set) -> Planner; an approval -> the next step."""
    agent = KleaAgent(checkpoint="inmemory")

    # Revision: human_feedback is set, the review step is unresolved.
    revise = _plan_with_step("review")
    revise.human_feedback = "change it"
    assert await agent._review_router(revise) == "planner"

    # Approval: feedback cleared and the frontier review step marked done, so
    # the next pending step dispatches by kind.
    approved = KleaAgentState()
    approved.plan = PlanSchema(
        step_list=[
            StepSchema(
                step_number=1, description="review", kind="review", status="done"
            ),
            StepSchema(step_number=2, description="work", kind="tool"),
        ],
        status="in_progress",
    )
    assert await agent._review_router(approved) == "tool"

    # Approval resolving the last step -> the answer.
    finished = _plan_with_step("review")
    finished.plan.step_list[0].status = "done"
    assert await agent._review_router(finished) == "plan_done"

    # Cancel is terminal.
    cancelled = _plan_with_step("review")
    cancelled.plan.status = "user_cancelled"
    assert await agent._review_router(cancelled) == "cancelled"


@pytest.mark.asyncio
async def test_planner_chat_reentry_forces_route_decision_chat():
    """A chat plan returns to RouteDecision, which is forced to answer as chat.

    The stale first-pass ``task`` route must not send the request back to the
    Planner (that would loop, since the Planner already declined to plan).
    """
    agent = KleaAgent(checkpoint="inmemory")
    state = KleaAgentState()
    state.route = RouteSchema(route="task")
    state.plan = PlanSchema(status="chat")

    assert await agent._planner_router(state) == "chat"
    assert await agent._route_decision_router(state) == "chat"


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
