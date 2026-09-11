#!/usr/bin/env python3
"""
Tests for the shared MCP tools caller node.

File: utils_pkg/tests/test_nodes_tools_caller.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, cast

from fastmcp.client.client import CallToolResult
from klea_utils.mcp.schemas import ToolCallSchema
from klea_utils.nodes.tools_caller import ToolsCallerNode
from pydantic import BaseModel, Field


class MiniState(BaseModel):
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
    tool_results: list[CallToolResult] = Field(default_factory=list)


class FakeMCPClient:
    """Minimal MCP client fake recording calls made to it."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def call_tool(self, name, arguments, raise_on_error=False):
        self.calls.append((name, arguments))
        return CallToolResult(content=[], structured_content=None, meta=None)


def _make_node(
    client: FakeMCPClient | None = None,
    tools_meta: dict | None = None,
    project_root: str | None = None,
    post_dispatch=None,
) -> ToolsCallerNode:
    return ToolsCallerNode(
        logger=logging.getLogger("test"),
        label="Running tools",
        mcp_client=client,
        tools_meta=tools_meta,
        project_root=project_root,
        post_dispatch=post_dispatch,
    )


def _record_stream(node: ToolsCallerNode, events: list[dict]) -> None:
    """Swap the node's stream writer for a recorder (test-only)."""
    cast(Any, node).write_custom_stream = events.append


async def test_always_writes_tool_results():
    """No calls (or no client) still writes empty results, never stale ones."""
    node = _make_node()
    events: list[dict] = []
    _record_stream(node, events)
    assert await node.execute(MiniState()) == {"tool_results": []}
    assert [e["type"] for e in events] == ["progress", "info", "debug"]

    node = _make_node(client=FakeMCPClient())
    _record_stream(node, events)
    assert await node.execute(MiniState()) == {"tool_results": []}


async def test_pre_exec_gates_on_tool_calls_and_client():
    client = FakeMCPClient()
    node = _make_node(client=client)
    assert node._pre_exec(MiniState(tool_calls=[ToolCallSchema(tool="a")])) is True
    assert node._pre_exec(MiniState()) is False
    no_client = _make_node()
    assert (
        no_client._pre_exec(MiniState(tool_calls=[ToolCallSchema(tool="a")])) is False
    )


async def test_dispatches_and_returns_tool_results():
    client = FakeMCPClient()
    node = _make_node(client=client)
    events: list[dict] = []
    _record_stream(node, events)

    state = MiniState(
        tool_calls=[
            ToolCallSchema(tool="a", args={"x": 1}),
            ToolCallSchema(tool="b", args={"y": 2}),
        ]
    )
    updates = await node.execute(state)

    assert [r.is_error for r in updates["tool_results"]] == [False, False]
    assert client.calls == [("a", {"x": 1}), ("b", {"y": 2})]
    event_types = [e["type"] for e in events]
    assert event_types == ["progress", "info", "debug"]

    info = events[1]["data"]
    assert info["summary"] == "Called 2 tool(s), 2 succeeded"
    assert info["details"]["tool_names"] == ["a", "b"]
    assert info["details"]["failed_calls"] == 0
    debug = events[2]["data"]
    assert debug["details"]["tool_calls"][0]["tool"] == "a"


async def test_streaming_uses_shared_hooks():
    """A bare AbstractLangGraphNode subclass emits info/debug/status via the
    base streaming contract, and never emits a usage event (LLM-only)."""
    from klea_utils.nodes.abstract import AbstractLangGraphNode, NodeStreamData

    class BareNode(AbstractLangGraphNode[BaseModel, dict[str, Any]]):
        async def execute(self, state):
            self._last_state = state
            self._pre_exec_stream()
            self._post_exec_stream()
            return {}

        def _get_info(self) -> NodeStreamData:
            return NodeStreamData(summary="info-summary", details={"k": "v"})

        def _get_debug(self) -> NodeStreamData:
            return NodeStreamData(summary="debug-summary")

        def _get_status(self) -> NodeStreamData:
            return NodeStreamData(summary="status-summary", display="**status**")

    node = BareNode(logging.getLogger("test"), "Bare")
    events: list[dict] = []
    cast(Any, node).write_custom_stream = events.append

    await node.execute(MiniState())

    event_types = [e["type"] for e in events]
    assert event_types == ["progress", "info", "debug", "state"]
    assert "usage" not in event_types
    assert events[1]["data"]["summary"] == "info-summary"


async def test_denies_path_arg_without_server_call(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()

    client = FakeMCPClient()
    node = _make_node(
        client=client,
        tools_meta={"list_files": {"checkpaths": ["path"]}},
        project_root=str(root),
    )
    _record_stream(node, [])

    state = MiniState(
        tool_calls=[ToolCallSchema(tool="list_files", args={"path": str(outside)})]
    )
    updates = await node.execute(state)

    result = updates["tool_results"][0]
    assert result.is_error
    assert "denied" in str(result.content)
    assert client.calls == []


async def test_post_dispatch_callback_extras():
    client = FakeMCPClient()

    def post_dispatch(state, results):
        return {"plan_status": "done"}

    node = _make_node(client=client, post_dispatch=post_dispatch)
    _record_stream(node, [])

    state = MiniState(tool_calls=[ToolCallSchema(tool="a")])
    updates = await node.execute(state)

    assert updates["tool_results"]
    assert updates["plan_status"] == "done"


async def test_post_dispatch_can_update_plan_step():
    class Step(BaseModel):
        status: str = "pending"

    class PlanLike(BaseModel):
        current_step_index: int = 0
        step_list: list[Step] = Field(default_factory=list)

    class AgentState(MiniState):
        plan: PlanLike = Field(default_factory=PlanLike)

    client = FakeMCPClient()

    def post_dispatch(state, results):
        step = state.plan.step_list[state.plan.current_step_index]
        step.status = "done"
        state.plan.current_step_index += 1
        return {"plan": state.plan}

    node = _make_node(client=client, post_dispatch=post_dispatch)
    _record_stream(node, [])

    plan = PlanLike(step_list=[Step()])
    state = AgentState(tool_calls=[ToolCallSchema(tool="a")], plan=plan)
    updates = await node.execute(state)

    assert updates["plan"].step_list[0].status == "done"
    assert updates["plan"].current_step_index == 1
