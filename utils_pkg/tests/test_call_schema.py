"""Tests for the strict-safe tool-call schema builder.

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
from typing import Any

import pytest
from klea_utils.mcp.call_schema import NO_TOOL_TAG, build_tool_call_schema
from klea_utils.mcp.schemas import ToolCallSchema, ToolCallsSchema, ToolInfo
from langchain_core.utils.function_calling import (
    convert_to_json_schema,
    convert_to_openai_tool,
)
from pydantic import BaseModel, ValidationError

READ_FILE = {
    "type": "object",
    "properties": {
        "path": {"type": "string", "description": "File path"},
        "offset": {"type": "integer", "default": 1},
        "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "line_numbers": {"type": "boolean", "default": True},
    },
    "required": ["path"],
}
FIND_FILES = {
    "type": "object",
    "properties": {
        "pattern": {"type": "string", "default": "*"},
        "tags": {"type": "array", "items": {"type": "string"}},
    },
    "required": [],
}
OBJECT_ARG = {
    "type": "object",
    "properties": {"filter": {"type": "object"}},
    "required": ["filter"],
}


def _tools(**schemas) -> dict[str, ToolInfo]:
    return {
        name: ToolInfo(title=name, input_schema=schema)
        for name, schema in schemas.items()
    }


def _call(tool: str, **args):
    return {"step": 1, "reason": "why", "call": {"tool": tool, **args}}


def test_required_and_defaults_are_enforced():
    out = build_tool_call_schema(_tools(read_file=READ_FILE))

    parsed: Any = out.model_validate(
        {"tool_calls": [_call("read_file", path="README.md")]}
    )
    call = parsed.tool_calls[0].call
    assert call.path == "README.md"
    assert call.offset == 1  # schema default
    assert call.limit is None  # anyOf [int, null], not required

    with pytest.raises(ValidationError):
        # missing required path
        out.model_validate({"tool_calls": [_call("read_file")]})


def test_array_field_maps_to_list():
    out = build_tool_call_schema(_tools(find_files=FIND_FILES))
    parsed: Any = out.model_validate(
        {"tool_calls": [_call("find_files", tags=["a", "b"])]}
    )
    assert parsed.tool_calls[0].call.tags == ["a", "b"]


def test_object_param_falls_back_to_json_string():
    out = build_tool_call_schema(_tools(query=OBJECT_ARG))
    schema = convert_to_json_schema(out)
    call_union = schema["properties"]["tool_calls"]["items"]["properties"]["call"]
    filter_schema = call_union["oneOf"][0]["properties"]["filter"]
    assert filter_schema["type"] == "string"
    assert "JSON-encoded" in filter_schema.get("description", "")


def test_no_tool_branch():
    out = build_tool_call_schema(_tools(read_file=READ_FILE))
    parsed: Any = out.model_validate(
        {"tool_calls": [_call(NO_TOOL_TAG, reason="no suitable tool")]}
    )
    assert parsed.tool_calls[0].call.tool == NO_TOOL_TAG
    assert parsed.tool_calls[0].call.reason == "no suitable tool"


def test_schema_is_deterministic():
    a = build_tool_call_schema(_tools(read_file=READ_FILE, find_files=FIND_FILES))
    b = build_tool_call_schema(_tools(find_files=FIND_FILES, read_file=READ_FILE))
    assert json.dumps(a.model_json_schema(), sort_keys=True) == json.dumps(
        b.model_json_schema(), sort_keys=True
    )


def _open_objects(node) -> list:
    found = []
    if isinstance(node, dict):
        if node.get("additionalProperties") is True:
            found.append(node)
        for value in node.values():
            found.extend(_open_objects(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_open_objects(value))
    return found


def test_schema_has_no_open_objects():
    out = build_tool_call_schema(
        _tools(read_file=READ_FILE, find_files=FIND_FILES, query=OBJECT_ARG)
    )
    assert _open_objects(out.model_json_schema()) == []


def test_openai_strict_accepts_schema():
    out = build_tool_call_schema(_tools(read_file=READ_FILE))
    tool = convert_to_openai_tool(out, strict=True)
    assert tool["function"]["name"] == "ToolPickerOutput"


def test_anthropic_transform_accepts_schema():
    anthropic = pytest.importorskip("anthropic")
    out = build_tool_call_schema(_tools(read_file=READ_FILE, find_files=FIND_FILES))
    transformed = anthropic.transform_schema(out)
    assert transformed["type"] == "object"


def test_examples_are_attached():
    out = build_tool_call_schema(_tools(read_file=READ_FILE))
    assert "examples" in json.dumps(out.model_json_schema())


def _schema_descriptions(schema: type[BaseModel]) -> list[str]:
    """Every ``description`` pydantic emits for *schema*, root and nested."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("description"), str):
                found.append(node["description"])
            for key, value in node.items():
                if key in ("properties", "$defs") and isinstance(value, dict):
                    for sub in value.values():
                        walk(sub)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema.model_json_schema())
    return found


def test_static_picker_output_schemas_carry_no_descriptions():
    """The static picker output models are structure-only (see prompt-conventions)."""
    for schema in (ToolCallSchema, ToolCallsSchema):
        assert _schema_descriptions(schema) == []
