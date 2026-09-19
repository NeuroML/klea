#!/usr/bin/env python3
"""
Tool-related utilities for Klea

File: klea_utils/tools.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from fastmcp.client.client import CallToolResult
from mcp.types import EmbeddedResource, TextContent, Tool

logger = logging.getLogger(__name__)


def _textualize_content_block(block: Any) -> str:
    """Extract text from a single ContentBlock."""
    if isinstance(block, TextContent):
        return block.text
    elif isinstance(block, EmbeddedResource):
        resource = block.resource
        if hasattr(resource, "blob") and resource.blob:
            logger.warning("Blob resource not processed: %s", resource.uri)
        text = getattr(resource, "text", None) or getattr(resource, "blob", "")
        return f"[Resource: {resource.uri}]\n{text}"
    else:
        logger.warning("Unhandled content block type: %s", type(block).__name__)
        return str(block)


def textualize_tool_results(
    tool_results: list[CallToolResult],
    max_len_per_tool: int | None = None,
    include_header: bool = True,
) -> str:
    """Format tool call results as LLM-ready text for use in prompt context.

    Tool return values are typically JSON-structured data (dicts, lists) which
    LLMs handle naturally. We wrap them in markdown code blocks for clear
    separation from surrounding prompt text.

    :param tool_results: List of tool call results
    :param max_len_per_tool: If set, truncate each tool's textual content to
        this many characters (per-tool cap, not total). Ensures a large first
        result doesn't starve later tools' outputs.
    :param include_header: When ``True`` (default) emit the batch header
        (``## Tool Results``) and a ``### Result i/n`` label per result.  Set
        ``False`` to render the bare content of a single result, e.g. when a
        caller supplies its own per-result heading.
    :returns: Formatted string suitable for inclusion in an LLM prompt
    """
    if not tool_results:
        return ""

    text = "## Tool Results\n" if include_header else ""
    for i, result in enumerate(tool_results, 1):
        if include_header:
            text += f"\n### Result {i}/{len(tool_results)}\n"

        parts = [_textualize_content_block(c) for c in result.content]
        content = "\n".join(parts)
        if max_len_per_tool is not None and len(content) > max_len_per_tool:
            content = content[:max_len_per_tool] + f"\n[tool result {i} truncated]"
        if result.is_error:
            text += "**Tool call failed:** " + content + "\n"
        else:
            text += "**Tool call succeeded:** " + content + "\n"
            text += "```\n" + content + "\n```\n"

    return text


def last_tool_error_text(
    tool_results: list[CallToolResult] | None,
    max_len: int = 500,
) -> str:
    """Return the text of the last error result in a batch, or ``""``.

    Used to give the tool picker concrete feedback when a round failed (for
    example an ``edit_file`` ambiguity), so it can adjust the call instead of
    repeating it.  Only the last error is returned: the picker retries one
    round at a time, so the most recent failure is the one to correct.

    :param tool_results: The batch's tool results (``None`` treated as empty).
    :param max_len: Truncate the extracted text to this many characters.
    :returns: The final error result's textual content, or ``""`` if none.
    """
    if not tool_results:
        return ""
    for result in reversed(tool_results):
        if not getattr(result, "is_error", False):
            continue
        parts = [_textualize_content_block(c) for c in result.content]
        text = "\n".join(parts).strip()
        if max_len is not None and len(text) > max_len:
            text = text[:max_len] + " [truncated]"
        return text
    return ""


def _collapse_schema_type(schema: dict[str, Any]) -> str:
    """Collapse a JSON Schema type spec into a single compact type name.

    Handles ``anyOf`` unions (e.g. ``[{"type": "integer"}, {"type": "null"}]``
    collapses to ``integer``) and plain ``type`` strings.
    """
    if "anyOf" in schema:
        types = [
            t.get("type")
            for t in schema["anyOf"]
            if isinstance(t, dict) and t.get("type") != "null"
        ]
        if types:
            return "|".join(str(t) for t in types)
    return schema.get("type") or "any"


def _format_tool_parameters(input_schema: dict[str, Any] | None) -> str:
    """Return a compact one-line-per-parameter summary of an MCP tool schema.

    The raw JSON Schema ``properties`` dict is verbose (defaults, length and
    range validators, ``anyOf`` unions); only the parameter name, type,
    required flag, and description are useful for tool selection, so the
    rest is dropped and whitespace is normalised.
    """
    if not input_schema:
        return ""
    properties = input_schema.get("properties") or {}
    if not properties:
        return ""
    required = set(input_schema.get("required") or [])
    lines = []
    for name, schema in properties.items():
        if not isinstance(schema, dict):
            continue
        ptype = _collapse_schema_type(schema)
        desc = " ".join((schema.get("description") or "").split())
        flag = ", required" if name in required else ""
        lines.append(f"- {name} ({ptype}{flag}): {desc}".rstrip())
    if not lines:
        return ""
    return "Parameters:\n" + "\n".join(lines)


def build_tool_description(t: Tool) -> tuple[str, str]:
    """Build the full and compact LLM-facing descriptions for an MCP tool.

    Returns a ``(full, short)`` tuple.  :class:`klea_utils.mcp.schemas.ToolInfo`
    stores both: the **full** form (heading + docstring + compact parameter
    list) is what the tool picker needs to emit calls, while the **short** form
    (heading + docstring only) is for planner-style nodes that reason about
    which tools exist without the argument detail (tiered disclosure).

    Klea expects MCP tool descriptions to follow the *docstring-first*
    convention (see ``docs/concepts/mcp.rst``, "Tool description length and
    style"): the LLM-facing description is the opening text block of the
    function docstring, written as a one-sentence summary followed by
    "Use when:" / "Do not use for:" bullet sections and a single example
    line, bounded to roughly 100-250 tokens.  Anthropic recommends at
    least 3-4 sentences covering what a tool does and when it should (and
    should not) be used; opencode keeps its tool descriptions to roughly
    100-600 tokens with a one-line summary first.

    Parameter descriptions are given in a Google-style ``Args:`` section
    that fastmcp parses into the schema, so they must not be repeated in
    the description.  MCP servers must not set the tool description to the
    raw full docstring (see ``neuroml_mcp.utils.register_tools``),
    otherwise the prompt carries duplicated ``Args:``/``Returns:`` prose
    in both the description and the compact parameter list built here.
    """
    parts = [f"## {t.name}"]
    if t.description:
        parts.append(t.description)
    short = "\n\n".join(parts)

    full_parts = list(parts)
    params = _format_tool_parameters(t.inputSchema)
    if params:
        full_parts.append(params)
    full = "\n\n".join(full_parts)
    return full, short


def clean_tool_meta(meta: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop developer tags from tool metadata before storing it.

    FastMCP populates ``meta`` with ``{"fastmcp": {"tags": [...]}}``; the
    tags are mostly developer labels (e.g. ``testing``) that are not used
    by the tool picker, so they are stripped to keep stored metadata lean.
    """
    if not meta:
        return None
    cleaned = {k: (dict(v) if isinstance(v, dict) else v) for k, v in meta.items()}
    fastmcp_meta = cleaned.get("fastmcp")
    if isinstance(fastmcp_meta, dict):
        fastmcp_meta.pop("tags", None)
        if not fastmcp_meta:
            cleaned.pop("fastmcp", None)
    return cleaned or None
