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

import pytest
from klea_agent.klea_agent import KleaAgent
from klea_utils.llm import LLMModel


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
    """Entry routing branches answer | act | plan; the work loop is wired."""
    graph = await _compile(monkeypatch)
    node_names = {n.name for n in graph.nodes.values()}
    edges = {(e.source, e.target) for e in graph.edges}

    for expected in (
        "Deciding route",
        "Setting goal",
        "Planning",
        "Selecting tools",
        "Running tools",
        "Evaluating",
        "Preparing response",
    ):
        assert expected in node_names

    # Entry: guard -> route decision; route -> answer | act | plan.
    assert ("Checking safety", "Deciding route") in edges
    assert ("Deciding route", "Preparing response") in edges  # answer
    assert ("Deciding route", "Selecting tools") in edges  # act
    assert ("Deciding route", "Setting goal") in edges  # plan

    # Work loop (ADR-0035): act batch -> deterministic triage -> evaluator.
    assert ("Setting goal", "Planning") in edges
    assert ("Planning", "Selecting tools") in edges
    assert ("Selecting tools", "Running tools") in edges
    assert ("Running tools", "Selecting tools") in edges  # retry
    assert ("Running tools", "Evaluating") in edges  # evaluate
    assert ("Running tools", "Setting goal") in edges  # replan
    assert ("Evaluating", "Selecting tools") in edges  # step_incomplete/step_done
    assert ("Evaluating", "Setting goal") in edges  # need_replan
    assert ("Evaluating", "Preparing response") in edges  # plan_done


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
