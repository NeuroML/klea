#!/usr/bin/env python3
"""
Tests for client-side MCP tool-call dispatch with permission gating.

File: utils_pkg/tests/test_mcp_dispatch.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from fastmcp.client.client import CallToolResult
from klea_utils.mcp.dispatch import dispatch_tool_calls
from klea_utils.mcp.schemas import ToolInfo
from mcp.types import TextContent


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


async def test_dispatch_empty_input():
    client = FakeMCPClient()
    results = await dispatch_tool_calls(client, [])
    assert results == []
    assert client.calls == []


async def test_dispatch_calls_all_tools_in_order():
    client = FakeMCPClient()
    results = await dispatch_tool_calls(
        client,
        [("a", {"x": 1}), ("b", {"y": 2})],
    )
    assert [r.is_error for r in results] == [False, False]
    assert client.calls == [("a", {"x": 1}), ("b", {"y": 2})]


async def test_dispatch_denies_outside_project_without_calling(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()

    client = FakeMCPClient()
    tools = {"list_files": ToolInfo(meta={"checkpaths": ["path"]})}
    results = await dispatch_tool_calls(
        client,
        [("list_files", {"path": str(outside)})],
        tools,
        str(root),
    )

    assert len(results) == 1
    assert results[0].is_error
    assert "denied" in str(results[0].content)
    assert client.calls == []


async def test_dispatch_mixed_keeps_order(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()

    client = FakeMCPClient()
    tools = {"list_files": ToolInfo(meta={"checkpaths": ["path"]})}
    results = await dispatch_tool_calls(
        client,
        [
            ("list_files", {"path": str(root)}),
            ("list_files", {"path": str(outside)}),
            ("other", {"n": 1}),
        ],
        tools,
        str(root),
    )

    assert [r.is_error for r in results] == [False, True, False]
    assert client.calls == [
        ("list_files", {"path": str(root)}),
        ("other", {"n": 1}),
    ]


async def test_dispatch_without_meta_skips_gate(tmp_path):
    client = FakeMCPClient()
    results = await dispatch_tool_calls(
        client,
        [("list_files", {"path": str(tmp_path)})],
    )
    assert [r.is_error for r in results] == [False]
    assert client.calls == [("list_files", {"path": str(tmp_path)})]


async def test_dispatch_leading_denied_keeps_order(tmp_path):
    """Leading denied must not shift later results (old insert bug)."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()

    client = FakeMCPClient()
    tools = {"list_files": ToolInfo(meta={"checkpaths": ["path"]})}
    results = await dispatch_tool_calls(
        client,
        [
            ("list_files", {"path": str(outside)}),
            ("list_files", {"path": str(root)}),
            ("other", {"n": 1}),
        ],
        tools,
        str(root),
    )

    assert [r.is_error for r in results] == [True, False, False]
    assert client.calls == [
        ("list_files", {"path": str(root)}),
        ("other", {"n": 1}),
    ]


async def test_dispatch_read_only_denies_destructive():
    client = FakeMCPClient()
    results = await dispatch_tool_calls(
        client,
        [("search", {}), ("delete", {})],
        {"search": ToolInfo(read_only=True), "delete": ToolInfo(destructive=True)},
        access_level="read_only",
    )
    assert [r.is_error for r in results] == [False, True]
    assert client.calls == [("search", {})]
    assert "delete" in str(results[1].content)


async def test_dispatch_full_allows_destructive():
    client = FakeMCPClient()
    results = await dispatch_tool_calls(
        client,
        [("delete", {})],
        {"delete": ToolInfo(destructive=True)},
        access_level="full",
    )
    assert [r.is_error for r in results] == [False]
    assert client.calls == [("delete", {})]


async def test_dispatch_read_only_denies_unannotated():
    client = FakeMCPClient()
    results = await dispatch_tool_calls(
        client,
        [("plain", {})],
        {"plain": ToolInfo()},
        access_level="read_only",
    )
    assert results[0].is_error
    assert client.calls == []


async def test_dispatch_without_tool_infos_skips_gates():
    """With no tool-info map neither gate runs, even under read_only (compat)."""
    client = FakeMCPClient()
    results = await dispatch_tool_calls(
        client, [("delete", {})], access_level="read_only"
    )
    assert [r.is_error for r in results] == [False]
    assert client.calls == [("delete", {})]


async def test_one_tool_fails_others_succeed():
    class FailingClient(FakeMCPClient):
        async def call_tool(self, name, arguments, raise_on_error=False):
            self.calls.append((name, arguments))
            if name == "bad_tool":
                raise RuntimeError("boom")
            return CallToolResult(content=[], structured_content=None, meta=None)

    client = FailingClient()
    results = await dispatch_tool_calls(
        client,
        [("good", {"x": 1}), ("bad_tool", {"y": 2}), ("good2", {"z": 3})],
    )

    assert len(results) == 3
    assert results[0].is_error is False
    assert results[1].is_error is True
    first = results[1].content[0]
    assert isinstance(first, TextContent)
    assert "RuntimeError" in first.text
    assert "boom" in first.text
    assert results[2].is_error is False
    # All three were attempted; order preserved despite middle failure
    assert client.calls == [
        ("good", {"x": 1}),
        ("bad_tool", {"y": 2}),
        ("good2", {"z": 3}),
    ]
