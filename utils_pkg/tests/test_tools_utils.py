#!/usr/bin/env python3
"""
Tests for tool utility functions.

File: utils_pkg/tests/test_tools_utils.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_utils.mcp.schemas import ToolCallSchema
from klea_utils.tools import (
    last_tool_error_text,
    textualize_tool_results,
    tool_call_errors,
)
from mcp.types import EmbeddedResource, TextContent, TextResourceContents

logger = logging.getLogger(__name__)


def _result(text: str, *, is_error: bool = False) -> CallToolResult:
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=None,
        meta=None,
        data=None,
        is_error=is_error,
    )


def _call(tool: str, args: dict, *, step: int = 0, paths=None) -> ToolCallSchema:
    return ToolCallSchema(tool=tool, args=args, step=step, paths=paths or [])


def test_tool_call_errors_lists_every_failed_call_with_paths():
    """All failed calls in the batch are aligned and rendered together."""
    calls = [
        _call("web_fetch", {"url": "https://example.com/194021.json"}, step=6),
        _call(
            "edit_file",
            {"path": "/tmp/a.txt", "old_string": "x" * 500},
            step=7,
        ),
    ]
    results = [
        _result("HTTP 404", is_error=True),
        _result("permission denied", is_error=True),
    ]

    out = tool_call_errors(calls, results)

    assert "step 6 web_fetch" in out
    assert "194021.json" in out
    assert "step 7 edit_file" in out
    assert "/tmp/a.txt" in out
    assert "HTTP 404" in out
    assert "permission denied" in out
    assert "old_string" not in out


def test_tool_call_errors_skips_free_text_arguments():
    """Only explicitly path/URI-named arguments are included."""
    calls = [
        _call(
            "write_file",
            {
                "path": "/tmp/out.txt",
                "content": "def free_text_function(): pass",
                "command": "curl https://example.com/secret",
            },
        )
    ]

    out = tool_call_errors(calls, [_result("failed", is_error=True)])

    assert "path=/tmp/out.txt" in out
    assert "def free_text_function" not in out
    assert "curl https://example.com/secret" not in out


def test_tool_call_errors_matches_compound_path_and_uri_names():
    """Path-word matching covers working_directory/output_dir etc."""
    calls = [
        _call(
            "run_command",
            {"working_directory": "/tmp/work", "command": "rm -rf /tmp/work"},
        ),
        _call(
            "web_fetch",
            {"url": "https://example.com/a", "output_dir": "/tmp/out"},
        ),
    ]

    out = tool_call_errors(
        calls,
        [_result("failed", is_error=True), _result("failed", is_error=True)],
    )

    assert "working_directory=/tmp/work" in out
    assert "output_dir=/tmp/out" in out
    assert "url=https://example.com/a" in out
    assert "command=" not in out


def test_tool_call_errors_includes_picker_paths():
    calls = [_call("custom_tool", {"content": "x"}, paths=["/tmp/picker.txt"])]

    out = tool_call_errors(calls, [_result("failed", is_error=True)])

    assert "paths=/tmp/picker.txt" in out
    assert "content=" not in out


def test_tool_call_errors_redacts_and_truncates():
    calls = [
        _call(
            "web_fetch",
            {"url": "https://user:pass@example.com/a?token=secret&x=1"},
        )
    ]

    out = tool_call_errors(
        calls,
        [_result("e" * 1000, is_error=True)],
        max_value_chars=200,
        max_error_chars=20,
        max_total_chars=500,
    )

    assert "user:pass" not in out
    assert "secret" not in out
    assert "***@" in out
    assert "token=***" in out
    assert "..." in out


def test_tool_call_errors_empty_without_errors():
    assert (
        tool_call_errors([_call("read_file", {"path": "/tmp/a"})], [_result("ok")])
        == ""
    )
    assert tool_call_errors(None, None) == ""


def test_tool_call_errors_handles_a_missing_call():
    out = tool_call_errors([], [_result("boom", is_error=True)])
    assert "(unknown tool)" in out
    assert "boom" in out


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
    assert "**Tool call failed:**" in output
    assert "Connection timeout" in output


def test_textualize_tool_results_multiple_blocks():
    """A single result with both text content and an embedded resource."""
    embedded = EmbeddedResource(
        type="resource",
        resource=TextResourceContents(
            uri="file:///cache/42.xml",
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
    assert "**Tool call failed:**" in output
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
