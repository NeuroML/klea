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
from klea_agent.commands import _INIT_PROMPT, build_agent_commands
from klea_agent.klea_agent import KleaAgent
from klea_agent.schemas import KleaAgentState
from klea_utils.commands.common import Command, CommandRegistry, Subcommand
from klea_utils.commands.graph import (
    GraphCommandResult,
    command_query_router,
    command_route_router,
)
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

    def test_access_is_implemented_and_server_side(self):
        registry, handlers = build_agent_commands(source_available=False)
        access = registry.get("access")
        assert access is not None
        assert access.side == "server"
        assert access.klass == "session-state"
        assert access.implemented is True
        assert "access" in handlers

    def test_client_commands_are_not_in_the_graph_catalogue(self):
        # The backend cannot know client-side commands (that would be a
        # circular dependency); the frontends own them and handle them locally.
        registry, _ = build_agent_commands(source_available=False)
        assert registry.get("help") is None
        assert registry.get("model") is None

    def test_runlocal_is_omitted(self):
        registry, _ = build_agent_commands(source_available=False)
        assert registry.get("runlocal") is None

    def test_compact_is_registered_when_memory_is_on(self):
        registry, handlers = build_agent_commands(source_available=False, memory=True)
        compact = registry.get("compact")
        assert compact is not None
        assert compact.side == "server"
        assert compact.klass == "workflow"
        assert compact.persists == "checkpoint"
        assert compact.implemented is True
        assert "compact" in handlers

    def test_compact_is_not_registered_when_memory_is_off(self):
        registry, handlers = build_agent_commands(source_available=False, memory=False)
        assert registry.get("compact") is None
        assert "compact" not in handlers

    def test_init_is_implemented(self):
        registry, handlers = build_agent_commands(source_available=False)
        init = registry.get("init")
        assert init is not None
        assert init.side == "server"
        assert init.klass == "workflow"
        assert init.persists == "message"
        assert init.implemented is True
        assert "init" in handlers

    def test_cwd_is_implemented_and_server_side(self):
        registry, handlers = build_agent_commands(source_available=False)
        cwd = registry.get("cwd")
        assert cwd is not None
        assert cwd.side == "server"
        assert cwd.klass == "session-state"
        assert cwd.implemented is True
        assert "cwd" in handlers


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


class TestAccessHandler:
    async def test_set_read_only(self):
        events: list = []
        node = _agent_node(events)
        updates = await node.execute(_state("/access read_only"))
        assert updates["access_level"] == "read_only"
        assert "read_only" in updates["message_for_user"]
        # Ephemeral: the command turn is dropped from the message history.
        assert updates["messages"] == []
        assert any(e.get("type") == "inspect" for e in events)

    async def test_set_full(self):
        node = _agent_node([])
        updates = await node.execute(_state("/access full"))
        assert updates["access_level"] == "full"

    async def test_report_current(self):
        node = _agent_node([])
        updates = await node.execute(_state("/access"))
        assert "Tool access level" in updates["message_for_user"]

    async def test_unknown_level(self):
        node = _agent_node([])
        updates = await node.execute(_state("/access bogus"))
        assert "Unknown access level" in updates["message_for_user"]
        assert "access_level" not in updates


class TestCwdHandler:
    async def test_report_default(self):
        node = _agent_node([])
        updates = await node.execute(_state("/cwd"))
        assert updates["message_for_user"].startswith("Project root:")

    async def test_set_directory(self, tmp_path):
        target = tmp_path / "proj"
        target.mkdir()
        node = _agent_node([])
        updates = await node.execute(_state(f"/cwd {target}"))
        assert updates["project_root"] == str(target)
        assert "Project root set to" in updates["message_for_user"]

    async def test_not_a_directory(self, tmp_path):
        file = tmp_path / "f.txt"
        file.write_text("x")
        node = _agent_node([])
        updates = await node.execute(_state(f"/cwd {file}"))
        assert "Not a directory" in updates["message_for_user"]
        assert "project_root" not in updates

    async def test_relative_resolves_against_current_root(self, tmp_path):
        base = tmp_path / "base"
        base.mkdir()
        sub = base / "sub"
        sub.mkdir()
        state = KleaAgentState(
            query="/cwd sub",
            messages=[HumanMessage(content="/cwd sub")],
            project_root=str(base),
        )
        node = _agent_node([])
        updates = await node.execute(state)
        assert updates["project_root"] == str(sub)


class TestNodeBehaviour:
    async def test_stub_replies_not_implemented(self):
        node = _agent_node([])
        updates = await node.execute(_state("/file /tmp/x"))
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

    async def test_async_handler_is_awaited(self):
        # A workflow command may await an LLM/tool call (e.g. ``/compact``).
        registry = CommandRegistry()
        registry.register(
            Command(
                name="compact", summary="compact", side="server", persists="checkpoint"
            )
        )

        async def handler(_state, _parsed):
            return GraphCommandResult(message="awaited")

        node = CommandNode(
            logger,
            "Running command",
            registry=registry,
            handlers={"compact": handler},
        )
        cast(Any, node).write_custom_stream = lambda _event: None
        updates = await node.execute(_state("/compact"))
        assert updates["message_for_user"] == "awaited"
        # Ephemeral: the command turn is dropped from the message history.
        assert updates["messages"] == []

    async def test_server_command_resolves_the_verb(self):
        registry = CommandRegistry()
        registry.register(
            Command(
                name="sess",
                summary="sessions",
                side="server",
                subcommands=(
                    Subcommand(name="switch", summary="switch", arg_hint="<n>"),
                ),
            )
        )
        seen: dict[str, Any] = {}

        def handler(_state, parsed):
            seen["verb"] = parsed.verb
            seen["args"] = parsed.args
            return GraphCommandResult(message="ok")

        node = CommandNode(
            logger, "Running command", registry=registry, handlers={"sess": handler}
        )
        cast(Any, node).write_custom_stream = lambda _event: None
        updates = await node.execute(_state("/sess switch 2"))
        assert updates["message_for_user"] == "ok"
        assert seen["verb"] == "switch"
        assert seen["args"] == ["2"]

    async def test_server_command_unknown_verb_is_rejected(self):
        registry = CommandRegistry()
        registry.register(
            Command(
                name="sess",
                summary="sessions",
                side="server",
                subcommands=(Subcommand(name="switch", summary="switch"),),
            )
        )
        node = CommandNode(
            logger,
            "Running command",
            registry=registry,
            handlers={"sess": lambda _s, _p: GraphCommandResult(message="ok")},
        )
        cast(Any, node).write_custom_stream = lambda _event: None
        updates = await node.execute(_state("/sess bogus"))
        assert "Unknown subcommand" in updates["message_for_user"]

    async def test_terminal_command_writes_an_empty_route(self):
        # A command with no handoff leaves ``command_route`` empty (-> END).
        node = _agent_node([])
        updates = await node.execute(_state("/mode general"))
        assert updates["command_route"] == ""

    async def test_handler_route_is_written_to_command_route(self):
        # A workflow command hands off via ``GraphCommandResult.route``.
        registry = CommandRegistry()
        registry.register(
            Command(
                name="compact", summary="compact", side="server", persists="checkpoint"
            )
        )
        node = CommandNode(
            logger,
            "Running command",
            registry=registry,
            handlers={
                "compact": lambda _s, _p: GraphCommandResult(
                    message="ok", route="compact"
                )
            },
        )
        cast(Any, node).write_custom_stream = lambda _event: None
        updates = await node.execute(_state("/compact"))
        assert updates["command_route"] == "compact"


class TestCommandRouteRouter:
    def test_terminal_route_is_empty(self):
        assert command_route_router(_state("x")) == ""

    def test_route_is_returned(self):
        state = _state("x")
        state.command_route = "compact"
        assert command_route_router(state) == "compact"


class TestCompactCommand:
    async def test_compact_hands_off_to_the_compaction_node(self):
        # ``/compact`` is a handoff command: it routes to the compaction node
        # and sets a neutral fallback message the node overwrites on success.
        node = _agent_node([])
        updates = await node.execute(_state("/compact"))
        assert updates["command_route"] == "compact"
        assert updates["message_for_user"] == "Nothing to compact yet."
        # ``persists=checkpoint``: the command turn is dropped from history.
        assert updates["messages"] == []


class TestInitCommand:
    async def test_init_hands_off_to_the_normal_path_with_the_prompt(self):
        # ``/init`` rejoins the normal path with a synthesised query; the
        # trailing ``/init`` turn is replaced by the prompt.
        node = _agent_node([])
        updates = await node.execute(_state("/init"))
        assert updates["command_route"] == "init"
        assert updates["query"] == _INIT_PROMPT
        assert [m.content for m in updates["messages"]] == [_INIT_PROMPT]


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
    # ``/compact``'s handoff target exists and is wired (memory is on).
    assert "Compacting memory" in names
    assert ("Running command", "Compacting memory") in edges
    # ``/init`` hands off back into the normal path.
    assert ("Running command", "Determining mode") in edges

    assert await agent._command_router(_state("/mode scientific")) == "command"
    assert await agent._command_router(_state("plain query")) == "continue"
