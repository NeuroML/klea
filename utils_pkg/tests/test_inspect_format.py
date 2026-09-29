#!/usr/bin/env python3
"""
Tests for the shared inspect-details formatter.

File: tests/test_inspect_format.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

import pytest
from klea_utils.ui.inspect_format import format_details


def test_empty_containers():
    """Empty dict/list render as JSON literals, at top level and nested."""
    assert format_details({}) == "{}"
    assert format_details([]) == "[]"
    assert format_details({"d": {}, "l": []}) == '{\n  "d": {},\n  "l": []\n}'


def test_multiline_string_expands_newlines():
    """A multi-line value keeps real newlines at their original column."""
    out = format_details({"content": "line1\nline2"})
    assert out == '{\n  "content": "line1\nline2"\n}'
    assert "\\n" not in out


def test_multiline_string_in_list_and_nested():
    """Multi-line expansion works inside lists and nested structures."""
    out = format_details({"items": [{"content": "a\nb"}]})
    assert out == '{\n  "items": [\n    {\n      "content": "a\nb"\n    }\n  ]\n}'


def test_json_object_string_inlined():
    """A JSON object string is parsed and inlined as nested JSON."""
    out = format_details({"unprocessed_output": '{"route": "task", "answer": ""}'})
    assert out == (
        '{\n  "unprocessed_output": {\n    "route": "task",\n    "answer": ""\n  }\n}'
    )
    assert '"unprocessed_output": "' not in out


def test_json_array_string_inlined():
    """A JSON array string is parsed and inlined."""
    assert format_details({"a": "[1, 2]"}) == ('{\n  "a": [\n    1,\n    2\n  ]\n}')


def test_non_json_container_string_left_as_text():
    """A string that opens like JSON but does not parse stays text."""
    assert format_details({"note": "{not json"}) == '{\n  "note": "{not json"\n}'


def test_pydantic_repr_left_as_quoted_string():
    """A single-line repr (e.g. processed_output) is emitted as a JSON string."""
    out = format_details({"processed_output": "RouteSchema(route='task')"})
    assert '"processed_output": "RouteSchema(route=\'task\')"' in out


def test_sentinel_never_leaks():
    """The newline sentinel must not survive into the output."""
    out = format_details({"content": "a\nb\nc"})
    assert "\ue000" not in out


@pytest.mark.parametrize(
    "details",
    [
        {"n": 3, "b": True, "z": None, "f": 1.5},
        {"s": 'he said "hi"\\done', "t": "tab\there"},
        {"nested": {"a": [1, 2, {"b": "c"}]}},
        {"d": {}, "l": [], "s": ""},
    ],
)
def test_matches_stdlib_json_for_plain_values(details):
    """Values needing no special treatment render exactly as json.dumps."""
    assert format_details(details) == json.dumps(details, indent=2, ensure_ascii=False)


def test_route_decision_example():
    """The route-decision inspect payload renders readably end to end."""
    details = {
        "route": "task",
        "input_prompt": [
            {"role": "system", "content": "## Role\n\n* You are the router."},
            {"role": "human", "content": "create a plan to list the files?"},
        ],
        "unprocessed_output": '{"route": "task", "answer": ""}',
        "processed_output": "route='task' answer=''",
    }
    out = format_details(details)
    assert "## Role\n\n* You are the router." in out
    assert "\\n" not in out
    assert '"unprocessed_output": {' in out
    assert "\"processed_output\": \"route='task' answer=''\"" in out


def test_duplicate_query_example_renders_both():
    """The duplicated user query (memory + human prompt) both render unescaped."""
    details = {
        "input_prompt": [
            {"role": "human", "content": "the query"},
            {
                "role": "human",
                "content": "## User request\n\nthe query\n\n---\n\nClassify",
            },
        ]
    }
    out = format_details(details)
    assert out.count("the query") == 2
    assert "\\n" not in out
