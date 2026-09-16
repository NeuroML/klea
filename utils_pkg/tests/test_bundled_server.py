#!/usr/bin/env python3
"""
Tests for the shared bundled tools server.

File: utils_pkg/tests/test_bundled_server.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import inspect

from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.server import bundled_tools
from klea_utils.mcp.server.bundled import app, bundle_server

BUNDLED = "bundled"


def _tool_info(fn) -> ToolInfo:
    assert hasattr(fn, "_tool_meta"), f"{fn.__name__} is not a registered tool"
    return fn._tool_meta


def _tags(fn) -> set[str]:
    return _tool_info(fn).tags or set()


def test_all_bundled_wrappers_carry_bundled_tag():
    names = {
        name
        for name, fn in inspect.getmembers(bundled_tools, inspect.isfunction)
        if fn.__module__ == bundled_tools.__name__ and hasattr(fn, "_tool_meta")
    }
    assert names == {
        "web_fetch",
        "list_files",
        "find_files",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "download_file",
        "run_command",
    }
    for name in names:
        assert BUNDLED in _tags(getattr(bundled_tools, name))


def test_web_fetch_tags():
    assert _tags(bundled_tools.web_fetch) == {BUNDLED, "web"}
    assert _tool_info(bundled_tools.web_fetch).checkpaths is None


def test_list_files_tags_and_checkpaths():
    assert _tags(bundled_tools.list_files) == {BUNDLED, "local", "files"}
    assert _tool_info(bundled_tools.list_files).checkpaths == ["path"]


def test_read_file_tags_and_checkpaths():
    assert _tags(bundled_tools.read_file) == {BUNDLED, "local", "files"}
    assert _tool_info(bundled_tools.read_file).checkpaths == ["path"]


def test_find_files_tags_and_checkpaths():
    assert _tags(bundled_tools.find_files) == {BUNDLED, "local", "files"}
    assert _tool_info(bundled_tools.find_files).checkpaths == ["path"]


def test_write_file_tags_and_checkpaths():
    assert _tags(bundled_tools.write_file) == {BUNDLED, "local", "files"}
    assert _tool_info(bundled_tools.write_file).checkpaths == ["path"]
    assert _tool_info(bundled_tools.write_file).destructive is True


def test_edit_file_tags_and_checkpaths():
    assert _tags(bundled_tools.edit_file) == {BUNDLED, "local", "files"}
    assert _tool_info(bundled_tools.edit_file).checkpaths == ["path"]
    assert _tool_info(bundled_tools.edit_file).destructive is True


def test_grep_tags_and_checkpaths():
    assert _tags(bundled_tools.grep) == {BUNDLED, "local", "files"}
    assert _tool_info(bundled_tools.grep).checkpaths == ["path"]


def test_download_file_tags_and_checkpaths():
    assert _tags(bundled_tools.download_file) == {BUNDLED, "web", "download"}
    assert _tool_info(bundled_tools.download_file).checkpaths == ["file_path"]


def test_run_command_tags_and_checkpaths():
    """run_command is destructive/open-world and checks its working dir."""
    assert _tags(bundled_tools.run_command) == {BUNDLED, "local", "code"}
    assert _tool_info(bundled_tools.run_command).checkpaths == ["working_directory"]


def test_run_command_not_permitted_read_only():
    """The destructive annotation excludes run_command from read_only (ADR-0037)."""
    from klea_utils.mcp.access import tool_permits

    info = _tool_info(bundled_tools.run_command)
    assert tool_permits(info.read_only, info.destructive, "read_only") is False
    assert tool_permits(info.read_only, info.destructive, "full") is True


def test_write_edit_not_permitted_read_only():
    """Destructive write tools are excluded from read_only (ADR-0037)."""
    from klea_utils.mcp.access import tool_permits

    for name in ("write_file", "edit_file"):
        info = _tool_info(getattr(bundled_tools, name))
        assert tool_permits(info.read_only, info.destructive, "read_only") is False, (
            name
        )
        assert tool_permits(info.read_only, info.destructive, "full") is True, name


def test_context_wrapper_contract():
    """Web-fetching wrappers must declare the fastmcp Context to reach the
    lifespan-provided httpx session; file tools must not need one.  Note that
    fastmcp's tool registration rewrites each signature (moving Context after
    the data params), so membership is checked, not position."""

    for name in ("web_fetch", "download_file"):
        sig = inspect.signature(getattr(bundled_tools, name))
        assert "ctx" in sig.parameters, name
    for name in (
        "list_files",
        "find_files",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "run_command",
    ):
        sig = inspect.signature(getattr(bundled_tools, name))
        assert "ctx" not in sig.parameters, name


async def test_bundle_server_registers_expected_tools():
    tools = await bundle_server.list_tools()
    by_name = {t.name: t for t in tools}
    assert set(by_name) == {
        "web_fetch",
        "list_files",
        "find_files",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "download_file",
        "run_command",
    }
    for t in by_name.values():
        assert BUNDLED in t.tags
    assert (by_name["list_files"].meta or {}).get("checkpaths") == ["path"]
    assert (by_name["find_files"].meta or {}).get("checkpaths") == ["path"]
    assert (by_name["read_file"].meta or {}).get("checkpaths") == ["path"]
    assert (by_name["write_file"].meta or {}).get("checkpaths") == ["path"]
    assert (by_name["edit_file"].meta or {}).get("checkpaths") == ["path"]
    assert (by_name["grep"].meta or {}).get("checkpaths") == ["path"]
    assert (by_name["download_file"].meta or {}).get("checkpaths") == ["file_path"]
    assert (by_name["run_command"].meta or {}).get("checkpaths") == [
        "working_directory"
    ]
    assert by_name["web_fetch"].meta is None


async def test_bundle_server_annotation_hints():
    """Read-only bundled tools carry readOnlyHint; the download tool is
    marked destructive + open world."""
    tools = {t.name: t for t in await bundle_server.list_tools()}

    for name in ("web_fetch", "list_files", "find_files", "read_file", "grep"):
        ann = tools[name].annotations
        assert ann is not None, name
        assert ann.readOnlyHint is True, name
        assert ann.destructiveHint is None, name

    download = tools["download_file"]
    assert download.annotations is not None
    assert download.annotations.readOnlyHint is None
    assert download.annotations.destructiveHint is True
    assert download.annotations.openWorldHint is True

    command = tools["run_command"]
    assert command.annotations is not None
    assert command.annotations.readOnlyHint is None
    assert command.annotations.destructiveHint is True
    assert command.annotations.openWorldHint is True

    write = tools["write_file"]
    assert write.annotations is not None
    assert write.annotations.readOnlyHint is None
    assert write.annotations.destructiveHint is True
    assert write.annotations.openWorldHint is None

    edit = tools["edit_file"]
    assert edit.annotations is not None
    assert edit.annotations.readOnlyHint is None
    assert edit.annotations.destructiveHint is True
    assert edit.annotations.openWorldHint is None


async def test_bundle_server_serves_via_inprocess_client():
    from fastmcp import Client

    async with Client(transport=bundle_server) as client:
        tools = await client.list_tools()
        names = [t.name for t in tools]
    assert {
        "web_fetch",
        "list_files",
        "find_files",
        "read_file",
        "write_file",
        "edit_file",
        "grep",
        "download_file",
        "run_command",
    } <= set(names)


def test_cli_help():
    from typer.testing import CliRunner

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "stdio" in result.output
    assert "http" in result.output


def test_module_entry_point_help_smoke():
    """The module must run as ``python -m klea_utils.mcp.server.bundled``,
    the invocation the agent uses to auto-launch the bundled server."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-m", "klea_utils.mcp.server.bundled", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert "stdio" in result.stdout
