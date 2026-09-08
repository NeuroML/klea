#!/usr/bin/env python3
"""
Structural test: the compiled agent graph has the ADR-0030 mode branch.

Compiles the graph without a full setup (dummy models, no MCP client) and
asserts the ``Determining mode`` node routes to either the safety guard
(``proceed``) or the ``Informing about mode`` terminal node (``inform``).

File: tests/test_mode_graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_agent.klea_agent import KleaAgent
from klea_utils.llm import LLMModel


@pytest.mark.asyncio
async def test_graph_has_mode_decision_branch(monkeypatch):
    """Compiled graph exposes the mode decision node and both edges."""
    logger = logging.getLogger(f"{__name__}.test_graph")
    agent = KleaAgent(checkpoint="inmemory")
    # Dummy models / client so the graph compiles without any setup or LLM.
    agent.llm_models = {
        "chat": LLMModel(instance=None, model_name=""),
        "plan": LLMModel(instance=None, model_name=""),
        "guard": LLMModel(instance=None, model_name=""),
    }
    agent.mcp_client = None
    agent.mcp_tools = None
    agent.tools_info = {}
    agent.retriever_config = None
    # Don't draw the PNG/mermaid export during the test.
    monkeypatch.setattr(agent, "_export_graph_png", lambda filename: None)

    await agent._create_graph()
    assert agent.graph is not None
    graph = agent.graph.get_graph()
    node_names = {n.name for n in graph.nodes.values()}
    logger.debug("nodes: %s", node_names)
    assert "Determining mode" in node_names
    assert "Informing about mode" in node_names

    mode_edges = {
        (e.source, e.target) for e in graph.edges if e.source == "Determining mode"
    }
    logger.debug("Determining mode edges: %s", mode_edges)
    assert ("Determining mode", "Checking safety") in mode_edges  # proceed
    assert ("Determining mode", "Informing about mode") in mode_edges  # inform


@pytest.mark.asyncio
async def test_scientific_without_source_produces_inform_plan():
    """The mode decision routes scientific-without-source to inform."""
    from klea_agent.nodes.mode_router import decide_mode
    from klea_agent.schemas import KleaAgentState, Mode

    resolved, note = decide_mode("scientific", source_available=False)
    assert resolved == "general"
    assert note
    # The graph router keys off mode.note being set:
    state = KleaAgentState(
        mode=Mode(requested="scientific", resolved=resolved, note=note)
    )
    agent = KleaAgent(checkpoint="inmemory")
    route = await agent._mode_router_node(state)
    assert route == "inform"
