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
from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo
from klea_utils.nodes.tools_caller import ToolsCallerNode
from mcp.types import ImageContent
from pydantic import BaseModel, Field


class MiniState(BaseModel):
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
    tool_results: list[CallToolResult] = Field(default_factory=list)
    access_level: str = "full"


class FakeMCPClient:
    """Minimal MCP client fake recording calls made to it."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def call_tool(self, name, arguments, raise_on_error=False, timeout=None):
        self.calls.append((name, arguments))
        return CallToolResult(content=[], structured_content=None, meta=None)


def _make_node(
    client: FakeMCPClient | None = None,
    tool_infos: dict | None = None,
    project_root: str | None = None,
    post_dispatch=None,
) -> ToolsCallerNode:
    return ToolsCallerNode(
        logger=logging.getLogger("test"),
        label="Running tools",
        mcp_client=client,
        tool_infos=tool_infos,
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
    assert [e["type"] for e in events] == ["progress", "inspect"]

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
    assert event_types == ["progress", "inspect", "state"]

    info = events[1]["data"]
    assert info["summary"] == "Called 2 tool(s), 2 succeeded"
    assert info["details"]["tool_names"] == ["a", "b"]
    assert info["details"]["failed_calls"] == 0
    assert info["details"]["tool_calls"][0]["tool"] == "a"
    status = events[2]["data"]
    assert status["display"] == "- **a**: ok\n- **b**: ok"


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

        def _get_inspect(self) -> NodeStreamData:
            return NodeStreamData(summary="inspect-summary", details={"k": "v"})

        def _get_status(self) -> NodeStreamData:
            return NodeStreamData(summary="status-summary", display="**status**")

    node = BareNode(logging.getLogger("test"), "Bare")
    events: list[dict] = []
    cast(Any, node).write_custom_stream = events.append

    await node.execute(MiniState())

    event_types = [e["type"] for e in events]
    assert event_types == ["progress", "inspect", "state"]
    assert "usage" not in event_types
    assert events[1]["data"]["summary"] == "inspect-summary"


async def test_denies_path_arg_without_server_call(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()

    client = FakeMCPClient()
    node = _make_node(
        client=client,
        tool_infos={"list_files": ToolInfo(meta={"checkpaths": ["path"]})},
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


async def test_invalid_tool_names_rejected_without_server_call():
    """Empty/unknown tool names never reach the server (weak-model guard)."""
    client = FakeMCPClient()
    node = _make_node(client=client, tool_infos={"read": ToolInfo()})
    _record_stream(node, [])

    state = MiniState(
        tool_calls=[
            ToolCallSchema(tool=""),
            ToolCallSchema(tool="nonexistent", args={"x": 1}),
            ToolCallSchema(tool="read"),
        ]
    )
    updates = await node.execute(state)

    results = updates["tool_results"]
    assert [r.is_error for r in results] == [True, True, False]
    assert "Unknown tool" in str(results[0].content)
    assert "Unknown tool" in str(results[1].content)
    # Only the valid call was dispatched, in its original position.
    assert client.calls == [("read", {})]


async def test_empty_tool_name_rejected_without_catalogue():
    """With no catalogue, an empty name is still rejected before dispatch."""
    client = FakeMCPClient()
    node = _make_node(client=client, tool_infos=None)
    _record_stream(node, [])

    state = MiniState(tool_calls=[ToolCallSchema(tool=""), ToolCallSchema(tool="any")])
    updates = await node.execute(state)

    assert [r.is_error for r in updates["tool_results"]] == [True, False]
    assert client.calls == [("any", {})]


async def test_access_level_gate_denies_read_only():
    """read_only denies a destructive tool at dispatch (ADR-0037)."""
    client = FakeMCPClient()
    node = _make_node(
        client=client,
        tool_infos={
            "read": ToolInfo(read_only=True),
            "delete": ToolInfo(destructive=True),
        },
    )
    _record_stream(node, [])

    state = MiniState(
        access_level="read_only",
        tool_calls=[ToolCallSchema(tool="read"), ToolCallSchema(tool="delete")],
    )
    updates = await node.execute(state)

    assert [r.is_error for r in updates["tool_results"]] == [False, True]
    assert client.calls == [("read", {})]


async def test_access_level_gate_full_allows():
    client = FakeMCPClient()
    node = _make_node(client=client, tool_infos={"delete": ToolInfo(destructive=True)})
    _record_stream(node, [])

    state = MiniState(access_level="full", tool_calls=[ToolCallSchema(tool="delete")])
    updates = await node.execute(state)

    assert [r.is_error for r in updates["tool_results"]] == [False]
    assert client.calls == [("delete", {})]


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


def test_get_status_lists_tool_outcomes_with_titles():
    """Status pane shows executed tools with a title and ok/error label."""
    node = _make_node(tool_infos={"a": ToolInfo(title="Alpha tool")})
    node._last_state = MiniState(
        tool_calls=[ToolCallSchema(tool="a"), ToolCallSchema(tool="b")]
    )
    node._last_tool_results = [
        CallToolResult(content=[], structured_content=None, meta=None, is_error=False),
        CallToolResult(content=[], structured_content=None, meta=None, is_error=True),
    ]

    status = node._get_status()

    assert status is not None
    assert status.heading == "Tool Execution"
    assert status.summary == "Called 2 tool(s)"
    assert status.display == "- **Alpha tool**: ok\n- **b**: error"


def test_get_status_none_without_tool_calls():
    """An empty round emits no status section."""
    node = _make_node()
    node._last_state = MiniState()
    node._last_tool_results = []

    assert node._get_status() is None


def test_get_status_skips_empty_name_calls():
    """A call with an empty name is not rendered as a meaningless '****: error'."""
    node = _make_node(tool_infos={"read": ToolInfo(title="Read file")})
    node._last_state = MiniState(
        tool_calls=[ToolCallSchema(tool=""), ToolCallSchema(tool="read")]
    )
    node._last_tool_results = [
        CallToolResult(content=[], structured_content=None, meta=None, is_error=True),
        CallToolResult(content=[], structured_content=None, meta=None, is_error=False),
    ]

    status = node._get_status()

    assert status is not None
    assert status.display == "- **Read file**: ok"


def test_get_status_none_when_all_names_empty():
    """An all-empty-name round emits no status section."""
    node = _make_node()
    node._last_state = MiniState(tool_calls=[ToolCallSchema(tool="")])
    node._last_tool_results = [
        CallToolResult(content=[], structured_content=None, meta=None, is_error=True)
    ]

    assert node._get_status() is None


def test_tool_display_entries_for_diff_and_text():
    """Renders a fenced diff for file edits and passthrough text otherwise."""
    node = _make_node(tool_infos={"edit_file": ToolInfo(title="Edit file")})
    node._last_state = MiniState(
        tool_calls=[ToolCallSchema(tool="edit_file"), ToolCallSchema(tool="other")]
    )
    node._last_tool_results = [
        CallToolResult(
            content=[],
            structured_content={
                "path": "a.txt",
                "diff": "+hello",
                "additions": 1,
                "deletions": 0,
            },
            meta=None,
        ),
        CallToolResult(
            content=[], structured_content={"display": "42 files"}, meta=None
        ),
    ]

    entries = node._tool_display_entries()

    assert entries[0]["mime"] == "text/x-diff"
    assert entries[0]["header"] == "Edit file: a.txt (+1/-0)"
    assert entries[0]["data"] == "+hello"
    assert entries[0]["meta"] == {"path": "a.txt", "additions": 1, "deletions": 0}
    assert entries[0]["display"] == "```diff\n+hello\n```"
    assert entries[1]["mime"] == "text/markdown"
    assert entries[1]["data"] == "42 files"


def test_tool_display_entries_for_code_and_self_describing():
    """``code`` maps to ``text/x-<lang>``; a display dict is passed through."""
    node = _make_node()
    node._last_state = MiniState(
        tool_calls=[ToolCallSchema(tool="code_tool"), ToolCallSchema(tool="rich")]
    )
    node._last_tool_results = [
        CallToolResult(
            content=[],
            structured_content={"code": "print(1)", "language": "python"},
            meta=None,
        ),
        CallToolResult(
            content=[],
            structured_content={
                "display": {
                    "mime": "image/png",
                    "data": "AAAA",
                    "meta": {"uri": "file:///tmp/x.png"},
                }
            },
            meta=None,
        ),
    ]

    entries = node._tool_display_entries()

    assert entries[0]["mime"] == "text/x-python"
    assert entries[0]["data"] == "print(1)"
    assert entries[0]["meta"] == {"language": "python"}
    assert entries[1]["mime"] == "image/png"
    assert entries[1]["data"] == "AAAA"
    assert entries[1]["meta"] == {"uri": "file:///tmp/x.png"}
    assert entries[1]["display"] == "[image/png data, 4 bytes]"


def test_tool_display_entries_from_image_content_block():
    """A typed MCP ImageContent is surfaced using its mimeType."""
    node = _make_node()
    node._last_state = MiniState(tool_calls=[ToolCallSchema(tool="plot")])
    node._last_tool_results = [
        CallToolResult(
            content=[ImageContent(type="image", data="BBBB", mimeType="image/png")],
            structured_content=None,
            meta=None,
        )
    ]

    entries = node._tool_display_entries()

    assert entries[0]["mime"] == "image/png"
    assert entries[0]["data"] == "BBBB"
    assert entries[0]["meta"] == {"binary": True}


def test_tool_display_entries_skip_errors_and_empty():
    node = _make_node()
    node._last_state = MiniState(
        tool_calls=[ToolCallSchema(tool="a"), ToolCallSchema(tool="b")]
    )
    node._last_tool_results = [
        CallToolResult(
            content=[], structured_content={"diff": "+x"}, meta=None, is_error=True
        ),
        CallToolResult(content=[], structured_content=None, meta=None),
    ]

    assert node._tool_display_entries() == []


def test_post_exec_stream_emits_tool_event():
    """A renderable result produces one ``tool`` event carrying the entries."""
    node = _make_node(tool_infos={"write_file": ToolInfo(title="Write file")})
    events: list[dict] = []
    _record_stream(node, events)
    node._last_state = MiniState(tool_calls=[ToolCallSchema(tool="write_file")])
    node._last_tool_results = [
        CallToolResult(
            content=[],
            structured_content={
                "path": "a.txt",
                "diff": "+x",
                "additions": 1,
                "deletions": 0,
            },
            meta=None,
        )
    ]

    node._post_exec_stream()

    tool_events = [e for e in events if e["type"] == "tool"]
    assert len(tool_events) == 1
    assert tool_events[0]["data"]["tools"][0]["mime"] == "text/x-diff"
