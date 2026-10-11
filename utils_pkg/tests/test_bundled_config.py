#!/usr/bin/env python3
"""
Tests for BaseLangGraph._bundled_server_config and the tag-filtered stdio
entry it produces.

File: utils_pkg/tests/test_bundled_config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import sys
from typing import override

from fastmcp.mcp_config import StdioMCPServer, TransformingStdioMCPServer
from klea_utils.graph.base import BaseLangGraph
from klea_utils.mcp.server.config import BundledToolsConfig
from pydantic import BaseModel, Field


class _General(BaseModel):
    bundled_tools: BundledToolsConfig = Field(default_factory=BundledToolsConfig)


class _Config(BaseModel):
    general: _General = Field(default_factory=_General)


class _BundledGraph(BaseLangGraph):
    """Minimal BaseLangGraph subclass for exercising the helper."""

    env_class: type[BaseModel] = BaseModel
    config_class: type[BaseModel] = BaseModel
    env_var: str = "TOY_ENV_FILE"
    env_file_default: str = "toy.env"
    graph_name: str = "ToyBundledGraph"

    def __init__(self, bundled: BundledToolsConfig | None = None):
        super().__init__(logging_level=logging.INFO, checkpoint="none", log_file=False)
        from platformdirs import PlatformDirs

        self.paths = PlatformDirs(self.graph_name.lower())
        self.logger = logging.getLogger(self.graph_name)
        self.app_config = (
            _Config()
            if bundled is None
            else _Config(general=_General(bundled_tools=bundled))
        )

    @override
    def _load_env(self) -> None:
        pass

    @override
    def _configure_resources(self) -> None:
        pass

    @override
    def _setup_models(self) -> None:
        pass

    @override
    async def _create_graph(self) -> None:
        pass


def test_disabled_returns_none():
    graph = _BundledGraph(BundledToolsConfig(enabled=False))
    assert graph._bundled_server_config() is None


def test_enabled_plain_entry_has_command_and_args():
    graph = _BundledGraph()
    entry = graph._bundled_server_config()
    assert entry == {
        "command": sys.executable,
        "args": ["-m", "klea_utils.mcp.server.bundled"],
    }


def test_enabled_with_filters_carries_tag_lists():
    graph = _BundledGraph(
        BundledToolsConfig(include_tags={"local", "files"}, exclude_tags={"code"})
    )
    entry = graph._bundled_server_config()
    assert entry is not None
    assert set(entry["include_tags"]) == {"local", "files"}
    assert set(entry["exclude_tags"]) == {"code"}


def test_no_tags_yields_plain_stdio_server():
    graph = _BundledGraph()
    from fastmcp.mcp_config import MCPConfig

    mcp = MCPConfig(mcpServers={"bundled": graph._bundled_server_config()})
    assert isinstance(mcp.mcpServers["bundled"], StdioMCPServer)
    assert not isinstance(mcp.mcpServers["bundled"], TransformingStdioMCPServer)


def test_filters_yield_transforming_stdio_server():
    graph = _BundledGraph(BundledToolsConfig(include_tags={"local"}))
    from fastmcp.mcp_config import MCPConfig

    mcp = MCPConfig(mcpServers={"bundled": graph._bundled_server_config()})
    server = mcp.mcpServers["bundled"]
    assert isinstance(server, TransformingStdioMCPServer)
    assert server.include_tags == {"local"}


async def test_filtered_stdio_client_lists_only_matching_tags():
    """End to end: an app config ``include_tags=["download"]`` exposes only
    the download_file tool (scoped as web + download) to a client connecting
    over stdio."""
    from fastmcp import Client
    from fastmcp.mcp_config import MCPConfig

    graph = _BundledGraph(BundledToolsConfig(include_tags={"download"}))
    entry = graph._bundled_server_config()
    assert entry is not None

    client = Client(MCPConfig(mcpServers={"bundled": entry}))
    async with client:
        tools = await client.list_tools()
    assert [t.name for t in tools] == ["download_file"]


async def test_web_scope_exposes_web_tools():
    """include_tags=["web"] matches every web-scoped bundled tool
    (web_fetch, web_search and download_file) since the scope tag is ``web``."""
    from fastmcp import Client
    from fastmcp.mcp_config import MCPConfig

    graph = _BundledGraph(BundledToolsConfig(include_tags={"web"}))
    entry = graph._bundled_server_config()
    assert entry is not None

    client = Client(MCPConfig(mcpServers={"bundled": entry}))
    async with client:
        tools = await client.list_tools()
    assert {t.name for t in tools} == {"web_fetch", "web_search", "download_file"}


async def test_disabled_entry_is_never_registered():
    graph = _BundledGraph(BundledToolsConfig(enabled=False))
    assert graph._bundled_server_config() is None
