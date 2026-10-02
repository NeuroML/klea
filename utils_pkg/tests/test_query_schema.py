#!/usr/bin/env python3
"""
Tests for the strict-safe retrieval-query schema builder.

File: tests/test_query_schema.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

from klea_utils.stores.config import FilterFieldInfo
from klea_utils.stores.query_schema import build_retrieval_query_schema


def _fields() -> list[FilterFieldInfo]:
    return [
        FilterFieldInfo(name="repository_type", description="", value_type="string"),
        FilterFieldInfo(name="year", description="", value_type="int"),
        FilterFieldInfo(name="score", description="", value_type="float"),
        FilterFieldInfo(name="tags", description="", value_type="list"),
    ]


def test_builds_typed_optional_fields_and_is_strict_safe():
    schema = build_retrieval_query_schema(_fields())

    assert set(schema.model_fields) == {
        "search_query",
        "repository_type",
        "year",
        "score",
        "tags",
    }
    # No dynamic-key (open) object: strict providers cannot close it to ``{}``.
    assert "additionalProperties" not in json.dumps(schema.model_json_schema())
    # Every filter field is optional (omitted when unstated).
    instance = schema.model_validate({"search_query": "q"}).model_dump()
    assert instance["repository_type"] is None
    assert instance["tags"] is None


def test_schema_is_cached_by_signature():
    fields = _fields()
    assert build_retrieval_query_schema(fields) is build_retrieval_query_schema(fields)


def test_shadowing_and_invalid_field_names_are_skipped():
    fields = [
        FilterFieldInfo(name="search_query", description="", value_type="string"),
        FilterFieldInfo(name="has space", description="", value_type="string"),
        FilterFieldInfo(name="ok", description="", value_type="string"),
    ]
    schema = build_retrieval_query_schema(fields)
    assert set(schema.model_fields) == {"search_query", "ok"}


def test_empty_field_set_still_builds_query_only_schema():
    schema = build_retrieval_query_schema([])
    assert set(schema.model_fields) == {"search_query"}
