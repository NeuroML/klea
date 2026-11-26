#!/usr/bin/env python3
"""
Tests for the shared inspect-details formatter.

File: tests/test_inspect_format.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

import pytest
from klea_utils.ui.inspect_format import (
    NOT_DISPLAYED_DETAIL_KEYS,
    drop_not_displayed,
    format_details,
)


def test_format_details_drops_not_displayed_keys() -> None:
    """Drop not-displayed keys everywhere while keeping the rest of the payload.

    The retry-budget keys are harness bookkeeping: they must never reach
    user-facing text, at the top level or nested inside a mapping or a list,
    while their sibling keys survive untouched.
    """
    details = {
        "retry_count": 1,
        "max_retries": 3,
        "route": "task",
        "nested": {"retry_count": 2, "step": 1},
        "items": [{"max_retries": 1, "tool_names": ["a"]}],
    }

    formatted = format_details(details)

    assert "retry_count" not in formatted
    assert "max_retries" not in formatted
    assert '"route": "task"' in formatted
    assert '"step": 1' in formatted
    assert "tool_names" in formatted
    # A key added to the not-displayed set later must fail this test, so the
    # frontends' filtering stays in step with the harness bookkeeping.
    assert NOT_DISPLAYED_DETAIL_KEYS == frozenset({"retry_count", "max_retries"})


def test_rendering_hides_not_displayed_detail_keys() -> None:
    """The details render path never shows the retry-budget bookkeeping.

    Mirrors what the status panes do: filter a section's ``details`` through
    :func:`drop_not_displayed`, then render it as indented JSON.  The
    eval-only keys must be absent at the top level and nested inside a
    mapping and a list, while ordinary keys survive, so a producer that adds
    ``retry_count``/``max_retries`` to an ``inspect``/``state``/``tool``
    payload cannot leak them into user-facing output.
    """
    details = {
        "retry_count": 1,
        "max_retries": 3,
        "route": "task",
        "nested": {"retry_count": 2, "step": 1},
        "items": [{"max_retries": 1, "tool_names": ["a"]}],
    }

    rendered = json.dumps(drop_not_displayed(details), indent=2)

    assert '"route": "task"' in rendered
    assert '"step": 1' in rendered
    assert "tool_names" in rendered
    assert "retry_count" not in rendered
    assert "max_retries" not in rendered


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
