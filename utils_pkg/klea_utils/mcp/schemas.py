#!/usr/bin/env python3
"""
Schemas shared by MCP servers and clients.

File: klea_utils/mcp/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Any

from pydantic import BaseModel, Field


class ToolInfo(BaseModel):
    """Metadata used to describe an MCP tool to clients and models.

    The ``read_only`` / ``destructive`` / ``idempotent`` / ``open_world``
    fields map 1:1 to the standard MCP ``ToolAnnotations`` hints
    (``readOnlyHint`` / ``destructiveHint`` / ``idempotentHint`` /
    ``openWorldHint``); see
    https://fastmcp.wiki/en/servers/tools#mcp-annotations for what each
    hint means and how a client is expected to act on it.
    :func:`klea_utils.mcp.registry.register_tools` folds them onto the
    registered tool.
    """

    # Detailed tool documentation for the LLM; falls back to the function docstring.
    description: str | None = None
    # Docstring-only form (no parameter list), for planner-style nodes that
    # reason about available tools without argument detail.
    short_description: str | None = None
    # Short human-facing label for UI and MCP clients.
    title: str | None = None
    # Categories used to group and filter tools.
    tags: set[str] | None = None
    # Argument names that are filesystem paths and must pass
    # check_path_access before the tool is invoked.  Read client-side by the
    # tool caller node to gate tool calls before they reach the MCP server.
    checkpaths: list[str] | None = None
    # Additional application-specific metadata.
    meta: dict[str, Any] | None = None
    # Standard MCP ToolAnnotations hints (see the docstring link above).
    # Each is only set when declared (None = server does not express it).
    read_only: bool | None = None
    destructive: bool | None = None
    idempotent: bool | None = None
    open_world: bool | None = None


class ToolCallSchema(BaseModel):
    """A single tool call selected by a tools picker node."""

    tool: str = ""
    args: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""
    #: 1-based plan step this call belongs to (0 when there is no plan, e.g.
    #: RAG).  Set by the agent's picker so a batch of calls can be attributed
    #: back to their originating step (ADR-0041).
    step: int = 0


class ToolCallsSchema(BaseModel):
    """The structured output of a tools picker node: a list of tool calls."""

    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
