#!/usr/bin/env python3
"""
Tests for the agent session-command catalogue and command node (ADR-0047).

File: tests/test_commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur at gmail DOT com>
"""

import logging
from typing import Any, cast

import pytest
from klea_agent.commands import build_agent_commands
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import KleaAgentState
from klea_utils.commands.common import Command, CommandRegistry
from klea_utils.commands.graph import GraphCommandResult, command_query_router
from klea_utils.llm import LLMModel
from klea_utils.nodes.command import CommandNode
from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


def _state(query: str) -> KleaAgentState:
    """Return a minimal agent state with *query* as the only history entry."""
    return KleaAgentState(query=query, messages=[HumanMessage(content=query)])


def _agent_node(events: list, *, source_available: bool = False) -> CommandNode:
    """Return an agent CommandNode capturing emitted stream events."""
    registry, handlers = build_agent_commands(source_available=source_available)
    node = CommandNode(logger, "Running command", registry=registry, handlers=handlers)
    cast(Any, node).write_custom_stream = events.append
    return node


class TestCatalogue:
    def test_mode_is_implemented_and_server_side(self):
        registry, handlers = build_agent_commands(source_available=False)
        mode = registry.get("mode")
        assert mode is not None
        assert mode.side == "server"
        assert mode.klass == "session-state"
        assert mode.implemented is True
        assert "mode" in handlers

    def test_client_commands_are_not_in_the_graph_catalogue(self):
        # The backend cannot know client-side commands (that would be a
        # circular dependency); the frontends own them and handle them locally.
        registry, _ = build_agent_commands(source_available=False)
        assert registry.get("help") is None
        assert registry.get("model") is None

    def test_runlocal_is_omitted(self):
        registry, _ = build_agent_commands(source_available=False)
        assert registry.get("runlocal") is None


class TestDisabledCommands:
    def test_disabled_command_is_not_registered(self):
        registry, handlers = build_agent_commands(
            source_available=False, disabled=["mode"]
        )
        assert registry.get("mode") is None
        assert "mode" not in handlers

    def test_disabled_is_case_insensitive_and_trimmed(self):
        registry, _ = build_agent_commands(source_available=False, disabled=["  MODE "])
        assert registry.get("mode") is None

    def test_other_commands_are_unaffected(self):
        registry, _ = build_agent_commands(source_available=False, disabled=["mode"])
        # A server stub (not yet implemented) is still registered.
        assert registry.get("access") is not None


class TestModeHandler:
    async def test_set_general(self):
        events: list = []
        node = _agent_node(events)
        updates = await node.execute(_state("/mode general"))
        assert updates["message_for_user"] == "Operating mode set to general."
        assert updates["mode"].resolved == "general"
        # Ephemeral: the command turn is dropped from the message history.
        assert updates["messages"] == []
        assert any(e.get("type") == "inspect" for e in events)

    async def test_scientific_without_source_is_not_silently_downgraded(self):
        node = _agent_node([])
        updates = await node.execute(_state("/mode scientific"))
        assert updates["mode"].resolved == "general"
        assert "curated knowledge source" in updates["message_for_user"]

    async def test_report_current(self):
        node = _agent_node([])
        updates = await node.execute(_state("/mode"))
        assert "Operating mode: general" in updates["message_for_user"]

    async def test_unknown_mode(self):
        node = _agent_node([])
        updates = await node.execute(_state("/mode bogus"))
        assert "Unknown mode" in updates["message_for_user"]


class TestNodeBehaviour:
    async def test_stub_replies_not_implemented(self):
        node = _agent_node([])
        updates = await node.execute(_state("/access full"))
        assert "not implemented" in updates["message_for_user"]

    async def test_unknown_command_rejected(self):
        node = _agent_node([])
        updates = await node.execute(_state("/nope"))
        assert "Unknown command" in updates["message_for_user"]
        # The command turn is dropped from the message history.
        assert updates["messages"] == []

    async def test_client_command_is_unknown_to_the_graph(self):
        # A client-side command is not in the graph catalogue, so the graph
        # treats it as unknown (the frontend intercepts it before this point).
        node = _agent_node([])
        updates = await node.execute(_state("/help"))
        assert "Unknown command" in updates["message_for_user"]

    async def test_reject_emits_no_inspect(self):
        events: list = []
        node = _agent_node(events)
        await node.execute(_state("/nope"))
        assert not any(e.get("type") == "inspect" for e in events)

    async def test_inspect_event_names_the_command(self):
        events: list = []
        node = _agent_node(events)
        await node.execute(_state("/mode general"))
        inspect = next(e for e in events if e.get("type") == "inspect")
        assert inspect["data"]["heading"] == "/mode"

    async def test_message_persisting_command_keeps_the_turn(self):
        registry = CommandRegistry()
        registry.register(
            Command(
                name="keep",
                summary="keep it",
                side="server",
                persists="message",
            )
        )
        node = CommandNode(
            logger,
            "Running command",
            registry=registry,
            handlers={"keep": lambda _s, _p: GraphCommandResult(message="kept")},
        )
        cast(Any, node).write_custom_stream = lambda _event: None
        updates = await node.execute(_state("/keep"))
        assert updates["message_for_user"] == "kept"
        # A ``persists: message`` command does not strip the turn.
        assert "messages" not in updates


class TestRouter:
    def test_command_query_routes_to_node(self):
        assert command_query_router(_state("/mode scientific")) == "command"

    def test_any_leading_slash_routes_to_node(self):
        # The router only decides command-vs-general; the node validates.
        assert command_query_router(_state("/run ls")) == "command"
        assert command_query_router(_state("/help")) == "command"
        assert command_query_router(_state("/nope")) == "command"

    def test_plain_query_continues(self):
        assert command_query_router(_state("hello there")) == "continue"

    def test_escaped_literal_slash_continues(self):
        assert command_query_router(_state("//not a command")) == "continue"


@pytest.mark.asyncio
async def test_graph_wires_the_command_node(monkeypatch):
    """The compiled graph exposes the command node and its edges (ADR-0047)."""
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
    graph = agent.graph.get_graph()
    names = {n.name for n in graph.nodes.values()}
    assert "Running command" in names

    edges = {(e.source, e.target) for e in graph.edges}
    assert ("Initializing", "Running command") in edges
    assert ("Running command", "__end__") in edges

    assert await agent._command_router(_state("/mode scientific")) == "command"
    assert await agent._command_router(_state("plain query")) == "continue"
