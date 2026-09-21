#!/usr/bin/env python3
"""
Tests for client-side MCP tool-call dispatch with permission gating.

File: utils_pkg/tests/test_mcp_dispatch.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_utils.mcp.dispatch import (
    DEFAULT_TOOL_CALL_TIMEOUT_SECONDS,
    TOOL_CALL_TIMEOUT_ENV_VAR,
    dispatch_tool_calls,
    resource_key,
    tool_call_timeout_seconds,
)
from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo
from mcp.types import TextContent


class FakeMCPClient:
    """Minimal MCP client fake recording calls made to it."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []
        self.timeouts: list[float | None] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        return False

    async def call_tool(self, name, arguments, raise_on_error=False, timeout=None):
        self.calls.append((name, arguments))
        self.timeouts.append(timeout)
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
    tools = {
        "list_files": ToolInfo(meta={"checkpaths": ["path"]}),
        "other": ToolInfo(),
    }
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


async def test_dispatch_rejects_unknown_and_empty_names():
    """A hallucinated/empty name is rejected without a server call."""
    client = FakeMCPClient()
    tools = {"read": ToolInfo()}
    results = await dispatch_tool_calls(
        client,
        [("read", {}), ("", {}), ("made_up", {})],
        tools,
    )
    assert [r.is_error for r in results] == [False, True, True]
    assert "Unknown tool" in str(results[1].content)
    assert "Unknown tool" in str(results[2].content)
    assert client.calls == [("read", {})]


async def test_dispatch_empty_name_rejected_without_catalogue():
    """Without a catalogue an empty name is still rejected."""
    client = FakeMCPClient()
    results = await dispatch_tool_calls(client, [("", {}), ("any", {})])
    assert [r.is_error for r in results] == [True, False]
    assert client.calls == [("any", {})]


async def test_dispatch_leading_denied_keeps_order(tmp_path):
    """Leading denied must not shift later results (old insert bug)."""
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.touch()

    client = FakeMCPClient()
    tools = {
        "list_files": ToolInfo(meta={"checkpaths": ["path"]}),
        "other": ToolInfo(),
    }
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
        async def call_tool(self, name, arguments, raise_on_error=False, timeout=None):
            self.calls.append((name, arguments))
            self.timeouts.append(timeout)
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


def test_tool_call_timeout_env_override(monkeypatch):
    monkeypatch.setenv(TOOL_CALL_TIMEOUT_ENV_VAR, "42")
    assert tool_call_timeout_seconds() == 42.0


def test_tool_call_timeout_invalid_env_falls_back(monkeypatch, caplog):
    monkeypatch.setenv(TOOL_CALL_TIMEOUT_ENV_VAR, "nope")
    with caplog.at_level(logging.WARNING):
        assert tool_call_timeout_seconds() == DEFAULT_TOOL_CALL_TIMEOUT_SECONDS
    assert TOOL_CALL_TIMEOUT_ENV_VAR in caplog.text


def test_tool_call_timeout_non_finite_falls_back(monkeypatch):
    for raw in ("inf", "nan"):
        monkeypatch.setenv(TOOL_CALL_TIMEOUT_ENV_VAR, raw)
        assert tool_call_timeout_seconds() == DEFAULT_TOOL_CALL_TIMEOUT_SECONDS


def test_tool_call_timeout_can_be_disabled(monkeypatch):
    for raw in ("0", "-1"):
        monkeypatch.setenv(TOOL_CALL_TIMEOUT_ENV_VAR, raw)
        assert tool_call_timeout_seconds() is None


def test_tool_call_timeout_default(monkeypatch):
    monkeypatch.delenv(TOOL_CALL_TIMEOUT_ENV_VAR, raising=False)
    assert tool_call_timeout_seconds() == DEFAULT_TOOL_CALL_TIMEOUT_SECONDS


async def test_dispatch_passes_resolved_timeout(monkeypatch):
    monkeypatch.setenv(TOOL_CALL_TIMEOUT_ENV_VAR, "77")
    client = FakeMCPClient()
    await dispatch_tool_calls(client, [("a", {})])
    assert client.timeouts == [77.0]


async def test_dispatch_call_timeout_override():
    client = FakeMCPClient()
    await dispatch_tool_calls(client, [("a", {})], call_timeout=5.0)
    assert client.timeouts == [5.0]


async def test_dispatch_timeout_disabled_passes_none():
    client = FakeMCPClient()
    await dispatch_tool_calls(client, [("a", {})], call_timeout=0)
    assert client.timeouts == [None]


async def test_dispatch_timeout_becomes_error():
    class TimeoutClient(FakeMCPClient):
        async def call_tool(self, name, arguments, raise_on_error=False, timeout=None):
            self.calls.append((name, arguments))
            self.timeouts.append(timeout)
            raise TimeoutError("timed out")

    client = TimeoutClient()
    results = await dispatch_tool_calls(client, [("slow", {})], call_timeout=3.0)

    assert results[0].is_error is True
    assert "timed out" in str(results[0].content)


def test_resource_key_normalises_checkpath_values():
    infos = {"edit_file": ToolInfo(checkpaths=["path"], destructive=True)}
    call = ToolCallSchema(tool="edit_file", args={"path": "./a/b.txt"})
    assert resource_key(call, infos) == frozenset({"a/b.txt"})


def test_resource_key_empty_without_identifiable_resource():
    infos = {
        "plain": ToolInfo(),
        "grep": ToolInfo(checkpaths=["path"]),
    }
    assert (
        resource_key(ToolCallSchema(tool="nope", args={"path": "x"}), infos)
        == frozenset()
    )
    assert (
        resource_key(ToolCallSchema(tool="plain", args={"path": "x"}), infos)
        == frozenset()
    )
    # Declared checkpath absent from the arguments.
    assert resource_key(ToolCallSchema(tool="grep", args={}), infos) == frozenset()


def test_resource_key_reads_folded_meta_checkpaths():
    infos = {"edit_file": ToolInfo(meta={"checkpaths": ["path"]})}
    call = ToolCallSchema(tool="edit_file", args={"path": "a.txt"})
    assert resource_key(call, infos) == frozenset({"a.txt"})


def test_resource_key_without_tool_infos():
    assert (
        resource_key(ToolCallSchema(tool="edit_file", args={"path": "a"}), None)
        == frozenset()
    )


def test_resource_key_flags_same_file_across_tools():
    infos = {
        "edit_file": ToolInfo(checkpaths=["path"]),
        "write_file": ToolInfo(checkpaths=["path"]),
    }
    a = resource_key(ToolCallSchema(tool="edit_file", args={"path": "X"}), infos)
    b = resource_key(ToolCallSchema(tool="write_file", args={"path": "./X"}), infos)
    assert a & b == frozenset({"X"})
