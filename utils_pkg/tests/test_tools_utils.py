#!/usr/bin/env python3
"""
Tests for tool utility functions.

File: utils_pkg/tests/test_tools_utils.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_utils.tools import last_tool_error_text, textualize_tool_results
from mcp.types import EmbeddedResource, TextContent, TextResourceContents
from pydantic.networks import AnyUrl

logger = logging.getLogger(__name__)


def _result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=None,
        meta=None,
        data=None,
        is_error=is_error,
    )


def test_last_tool_error_text_returns_last_error():
    """The most recent error result's text is returned."""
    results = [
        _result("bad", is_error=True),
        _result("ok"),
        _result("ambiguous match", is_error=True),
    ]
    assert last_tool_error_text(results) == "ambiguous match"


def test_last_tool_error_text_none_when_no_error():
    """A batch without errors yields no feedback."""
    assert last_tool_error_text([_result("ok")]) == ""
    assert last_tool_error_text(None) == ""


def test_last_tool_error_text_truncates():
    """Long error text is capped."""
    out = last_tool_error_text([_result("x" * 1000, is_error=True)], max_len=10)
    assert out == "x" * 10 + " [truncated]"


def test_textualize_tool_results_success():
    """A tool returning a dict with model info and a downloaded resource."""
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text=(
                    '{"Model_42": {"Model_ID": "42", '
                    '"Name": "Purkinje cell", '
                    '"resource": "/home/user/.cache/nml_mcp/42.xml"}}'
                ),
            )
        ],
        structured_content={
            "Model_42": {
                "Model_ID": "42",
                "Name": "Purkinje cell",
                "resource": "/home/user/.cache/nml_mcp/42.xml",
            }
        },
        meta=None,
        data={
            "Model_42": {
                "Model_ID": "42",
                "Name": "Purkinje cell",
                "resource": "/home/user/.cache/nml_mcp/42.xml",
            }
        },
        is_error=False,
    )
    output = textualize_tool_results([result])
    logger.debug(f"{output = }")

    assert "## Tool Results" in output
    assert "Purkinje cell" in output
    assert "/home/user/.cache/nml_mcp/42.xml" in output
    assert "Error" not in output


def test_textualize_tool_results_error():
    """A tool that errored, returning an error message."""
    result = CallToolResult(
        content=[
            TextContent(
                type="text",
                text="Connection timeout: NeuroML-DB not reachable",
            )
        ],
        structured_content=None,
        meta=None,
        data=None,
        is_error=True,
    )
    output = textualize_tool_results([result])
    logger.debug(f"{output = }")

    assert "## Tool Results" in output
    assert "**Error:**" in output
    assert "Connection timeout" in output


def test_textualize_tool_results_multiple_blocks():
    """A single result with both text content and an embedded resource."""
    embedded = EmbeddedResource(
        type="resource",
        resource=TextResourceContents(
            uri=AnyUrl("file:///cache/42.xml"),
            mimeType="application/xml",
            text="<cell>Purkinje</cell>",
        ),
    )
    result = CallToolResult(
        content=[
            TextContent(type="text", text='{"status": "ok"}'),
            embedded,
        ],
        structured_content=None,
        meta=None,
        data=None,
        is_error=False,
    )
    output = textualize_tool_results([result])
    logger.debug(f"{output = }")

    assert "## Tool Results" in output
    assert '{"status": "ok"}' in output
    assert "file:///cache/42.xml" in output
    assert "<cell>Purkinje</cell>" in output


def test_textualize_tool_results_multiple_results():
    """Multiple results: one success, one error."""
    success = CallToolResult(
        content=[
            TextContent(type="text", text='{"model": "cerebellum"}'),
        ],
        structured_content=None,
        meta=None,
        data=None,
        is_error=False,
    )
    error = CallToolResult(
        content=[
            TextContent(type="text", text="Timeout fetching data"),
        ],
        structured_content=None,
        meta=None,
        data=None,
        is_error=True,
    )
    output = textualize_tool_results([success, error])
    logger.debug(f"{output = }")

    assert "## Tool Results" in output
    assert "### Result 1/2" in output
    assert "### Result 2/2" in output
    assert '{"model": "cerebellum"}' in output
    assert "**Error:**" in output
    assert "Timeout fetching data" in output


def test_textualize_tool_results_without_header():
    """``include_header=False`` renders just the result body."""
    result = CallToolResult(
        content=[TextContent(type="text", text='{"a": 1}')],
        structured_content=None,
        meta=None,
        data=None,
        is_error=False,
    )
    output = textualize_tool_results([result], include_header=False)
    assert "## Tool Results" not in output
    assert "### Result" not in output
    assert '{"a": 1}' in output
