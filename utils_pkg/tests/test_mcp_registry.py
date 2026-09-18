#!/usr/bin/env python3
"""
Tests for the shared MCP tool registry.

File: utils_pkg/tests/test_mcp_registry.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import inspect
import logging
import sys
import types

import pytest
from fastmcp import Client, FastMCP
from klea_utils.mcp import registry
from klea_utils.mcp.registry import (
    _strip_null_optionals,
    _wrap_with_root_guard,
    register_tools,
    tool_meta,
)
from klea_utils.mcp.schemas import ToolInfo

logger = logging.getLogger(__name__)


@tool_meta(ToolInfo(tags={"testing"}))
def sample_tool(param: str) -> str:
    """A tool function."""
    return param


@tool_meta(ToolInfo(tags={"testing"}))
def optional_tool(count: int = 5, limit: int | None = None) -> dict:
    """A tool mixing a non-nullable and a nullable optional argument."""
    return {"count": count, "limit": limit}


@tool_meta(ToolInfo(tags={"testing"}, checkpaths=["path"]))
def checkpath_tool(path: str) -> str:
    """A tool whose path argument must be permission-checked."""
    return path


@tool_meta(ToolInfo(tags={"testing"}))
def nopath_tool(x: str) -> str:
    """A tool with no path arguments."""
    return x


@tool_meta(ToolInfo(tags={"testing"}, read_only=True, open_world=True))
def annotated_tool(param: str) -> str:
    """A tool carrying standard MCP annotation hints."""
    return param


@tool_meta(ToolInfo(tags={"testing"}, destructive=True))
def destructive_tool(param: str) -> str:
    """A tool marked destructive."""
    return param


def plain_helper() -> str:
    """A helper that is not a tool."""
    return "helper"


def _private_helper() -> str:
    """A private helper that is not a tool."""
    return "private"


@pytest.mark.asyncio
async def test_register_tools_only_registers_decorated():
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    tools = await server.list_tools()
    names = [t.name for t in tools]
    logger.debug(f"{names = }")

    assert "sample_tool" in names
    assert "plain_helper" not in names
    assert "_private_helper" not in names


@pytest.mark.asyncio
async def test_register_tools_ignores_imported_decorated_functions():
    """A decorated function attached to a module but defined elsewhere is
    not registered (the __module__ filter)."""
    mod = types.ModuleType("fake_tool_module")

    @tool_meta(ToolInfo(tags={"testing"}))
    def imported_tool(a: str) -> str:
        return a

    mod.__dict__["imported_tool"] = imported_tool

    server = FastMCP("test-server")
    register_tools(server, [mod])

    tools = await server.list_tools()
    names = [t.name for t in tools]
    logger.debug(f"{names = }")

    assert "imported_tool" not in names


@pytest.mark.asyncio
async def test_register_tools_puts_checkpaths_in_meta():
    """checkpaths declared on ToolInfo must reach clients on the Tool meta."""
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    tools = await server.list_tools()
    checkpath_tools = [t for t in tools if t.name == "checkpath_tool"]
    assert len(checkpath_tools) == 1
    assert checkpath_tools[0].meta is not None
    assert checkpath_tools[0].meta["checkpaths"] == ["path"]


@pytest.mark.asyncio
async def test_register_tools_without_checkpaths_omits_key():
    """Tools that declare no checkpaths must not carry the key in meta."""
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    tools = await server.list_tools()
    nopath_tools = [t for t in tools if t.name == "nopath_tool"]
    assert len(nopath_tools) == 1
    assert "checkpaths" not in (nopath_tools[0].meta or {})


@pytest.mark.asyncio
async def test_register_tools_folds_annotation_hints():
    """ToolInfo hint fields must reach clients as standard ToolAnnotations."""
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    tools = {t.name: t for t in await server.list_tools()}

    annotated = tools["annotated_tool"]
    assert annotated.annotations is not None
    assert annotated.annotations.readOnlyHint is True
    assert annotated.annotations.openWorldHint is True
    assert annotated.annotations.destructiveHint is None

    destructive = tools["destructive_tool"]
    assert destructive.annotations is not None
    assert destructive.annotations.destructiveHint is True
    assert destructive.annotations.readOnlyHint is None
    assert destructive.annotations.openWorldHint is None


@pytest.mark.asyncio
async def test_register_tools_no_annotations_when_unset():
    """A tool declaring no hints must not carry an annotations object."""
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    tools = {t.name: t for t in await server.list_tools()}

    plain = tools["sample_tool"]
    assert plain.annotations is None


async def _echo(value: str) -> str:
    """Echo the value back."""
    return value


def test_wrap_with_root_guard_preserves_metadata():
    """The guard must not hide the name/docstring/signature from fastmcp."""
    wrapped = _wrap_with_root_guard(_echo)
    assert wrapped.__name__ == "_echo"
    assert wrapped.__doc__ == _echo.__doc__
    assert inspect.signature(wrapped) == inspect.signature(_echo)


@pytest.mark.asyncio
async def test_wrap_with_root_guard_refuses_as_root(monkeypatch):
    monkeypatch.setattr(registry, "running_as_root", lambda: True)
    monkeypatch.setattr(registry, "allow_root_tools", lambda: False)
    called = False

    async def tool(x):
        nonlocal called
        called = True
        return x

    result = await _wrap_with_root_guard(tool)("x")
    assert result.is_error is True
    assert called is False


@pytest.mark.asyncio
async def test_wrap_with_root_guard_allows_with_override(monkeypatch):
    monkeypatch.setattr(registry, "running_as_root", lambda: True)
    monkeypatch.setattr(registry, "allow_root_tools", lambda: True)
    assert await _wrap_with_root_guard(_echo)("x") == "x"


@pytest.mark.asyncio
async def test_registered_tool_keeps_parameters():
    """The wrapper must not hide the signature from fastmcp's schema."""
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    tools = {t.name: t for t in await server.list_tools()}

    properties = tools["sample_tool"].parameters.get("properties", {})
    assert "param" in properties


@pytest.mark.asyncio
async def test_registered_tool_refused_as_root(monkeypatch):
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])
    monkeypatch.setattr(registry, "running_as_root", lambda: True)
    monkeypatch.setattr(registry, "allow_root_tools", lambda: False)

    async with Client(transport=server) as client:
        result = await client.call_tool(
            "sample_tool", {"param": "hi"}, raise_on_error=False
        )

    assert result.is_error is True


def test_register_tools_warns_if_root(monkeypatch):
    """Registration logs the root warning once (via warn_if_root)."""
    called: list = []
    monkeypatch.setattr(registry, "warn_if_root", called.append)

    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    assert called


def test_strip_null_optionals_unit():
    """Nulls are dropped only for non-nullable, non-required properties."""
    schema = {
        "properties": {
            "a": {"type": "integer"},
            "b": {"type": "integer"},
            "c": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            "d": {"type": "integer"},
        },
        "required": ["d"],
    }
    args = {"a": None, "b": 3, "c": None, "d": None}
    _strip_null_optionals(schema, args)
    # a: non-nullable optional -> dropped; b: present; c: nullable -> kept;
    # d: required -> kept (validation reports it).
    assert args == {"b": 3, "c": None, "d": None}


@pytest.mark.asyncio
async def test_registered_tool_tolerates_null_optional():
    """A weak model's explicit null uses the default for non-nullable args."""
    server = FastMCP("test-server")
    register_tools(server, [sys.modules[__name__]])

    async with Client(transport=server) as client:
        result = await client.call_tool("optional_tool", {"count": None, "limit": None})

    assert result.structured_content == {"count": 5, "limit": None}
