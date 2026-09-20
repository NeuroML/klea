#!/usr/bin/env python3
"""
Tests for the compact MCP tool description formatter.

File: tests/test_tools_info.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import unittest

from klea_utils.tools import (
    _format_tool_parameters,
    build_tool_description,
    clean_tool_meta,
)
from mcp.types import Tool


def _make_tool(
    name="test_tool",
    title="Test tool",
    description="Does useful things.",
    input_schema=None,
):
    input_schema = input_schema or {}
    return Tool(
        name=name,
        title=title,
        description=description,
        inputSchema=input_schema,
        outputSchema=None,
        annotations=None,
        execution=None,
        icons=[],
    )


class TestFormatToolParameters(unittest.TestCase):
    """Tests for _format_tool_parameters."""

    def test_none_and_empty_schema(self):
        self.assertEqual(_format_tool_parameters(None), "")
        self.assertEqual(_format_tool_parameters({}), "")
        self.assertEqual(_format_tool_parameters({"properties": {}}), "")

    def test_required_flag_and_type(self):
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "A path."}},
            "required": ["path"],
        }
        result = _format_tool_parameters(schema)
        self.assertIn("- path (string, required): A path.", result)
        self.assertNotIn("default", result)

    def test_optional_param_labelled_optional(self):
        schema = {
            "type": "object",
            "properties": {"recursive": {"type": "boolean"}},
        }
        result = _format_tool_parameters(schema)
        self.assertIn("- recursive (boolean, optional):", result)

    def test_anyof_collapses_null_and_omits_null_default(self):
        schema = {
            "type": "object",
            "properties": {
                "max_depth": {
                    "anyOf": [{"type": "integer"}, {"type": "null"}],
                    "default": None,
                    "description": "Depth.",
                }
            },
        }
        result = _format_tool_parameters(schema)
        self.assertIn("- max_depth (integer, optional): Depth.", result)
        self.assertNotIn("anyOf", result)
        # A null default means "unset"; rendering "default null" would invite
        # the model to pass a literal null.
        self.assertNotIn("default", result)

    def test_validators_dropped_and_default_kept(self):
        schema = {
            "type": "object",
            "properties": {
                "k": {
                    "type": "integer",
                    "default": 5,
                    "minimum": 1,
                    "maximum": 100,
                    "description": "A number.",
                }
            },
        }
        result = _format_tool_parameters(schema)
        self.assertIn("- k (integer, optional, default 5): A number.", result)
        self.assertNotIn("minimum", result)

    def test_string_default_is_quoted(self):
        schema = {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "default": "*", "description": "Filter."}
            },
        }
        result = _format_tool_parameters(schema)
        self.assertIn('- pattern (string, optional, default "*"): Filter.', result)

    def test_boolean_default_is_bare(self):
        schema = {
            "type": "object",
            "properties": {
                "include_files": {
                    "type": "boolean",
                    "default": True,
                    "description": "Include files.",
                }
            },
        }
        result = _format_tool_parameters(schema)
        self.assertIn(
            "- include_files (boolean, optional, default true): Include files.",
            result,
        )

    def test_description_whitespace_normalised(self):
        schema = {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "\n  a\n    b  c\n"}
            },
        }
        result = _format_tool_parameters(schema)
        self.assertIn("- pattern (string, optional): a b c", result)


class TestBuildToolDescription(unittest.TestCase):
    """Tests for build_tool_description (full and short forms)."""

    def test_full_has_heading_description_and_parameters(self):
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        }
        full, _short = build_tool_description(_make_tool(input_schema=schema))
        self.assertIn("## test_tool", full)
        self.assertIn("Does useful things.", full)
        self.assertIn("Parameters:", full)
        self.assertIn("- path (string, optional):", full)

    def test_short_omits_parameters(self):
        schema = {
            "type": "object",
            "properties": {"path": {"type": "string"}},
        }
        _full, short = build_tool_description(_make_tool(input_schema=schema))
        self.assertIn("## test_tool", short)
        self.assertIn("Does useful things.", short)
        self.assertNotIn("Parameters:", short)
        self.assertNotIn("- path (string, optional):", short)

    def test_full_equals_short_when_no_parameters(self):
        full, short = build_tool_description(_make_tool(input_schema=None))
        self.assertEqual(full, short)
        self.assertIn("## test_tool", short)
        self.assertIn("Does useful things.", short)
        self.assertNotIn("Parameters:", short)

    def test_no_description(self):
        full, short = build_tool_description(_make_tool(description=""))
        self.assertEqual(full, "## test_tool")
        self.assertEqual(short, "## test_tool")


class TestCleanToolMeta(unittest.TestCase):
    """Tests for clean_tool_meta."""

    def test_strips_fastmcp_tags(self):
        self.assertEqual(clean_tool_meta({"fastmcp": {"tags": ["testing"]}}), None)
        self.assertEqual(
            clean_tool_meta({"fastmcp": {"tags": ["testing"]}, "other": 1}),
            {"other": 1},
        )

    def test_preserves_other_metadata(self):
        self.assertEqual(
            clean_tool_meta({"fastmcp": {"tags": ["testing"], "x": 2}}),
            {"fastmcp": {"x": 2}},
        )

    def test_none_and_empty(self):
        self.assertIsNone(clean_tool_meta(None))
        self.assertIsNone(clean_tool_meta({}))

    def test_input_not_mutated(self):
        meta = {"fastmcp": {"tags": ["testing"]}}
        clean_tool_meta(meta)
        self.assertEqual(meta, {"fastmcp": {"tags": ["testing"]}})


if __name__ == "__main__":
    unittest.main()
