#!/usr/bin/env python3
"""
Tests for the GenerateRetrievalQuery node wiring.

File: rag_pkg/tests/test_generate_retrieval_query.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

from klea_rag.nodes.generate_retrieval_query import GenerateRetrievalQuery
from klea_rag.schemas import RAGState
from klea_utils.nodes.context import LLMNodeContext
from klea_utils.stores.config import FilterFieldInfo


def _node(
    filter_fields_by_domain: dict[str, list[FilterFieldInfo]] | None = None,
) -> GenerateRetrievalQuery:
    node = object.__new__(GenerateRetrievalQuery)
    node.filter_fields_by_domain = filter_fields_by_domain or {}
    return node


def _repos_fields() -> list[FilterFieldInfo]:
    return [
        FilterFieldInfo(
            name="repository_type",
            description="hosting type (github, dandi, biomodels or figshare)",
            value_type="string",
        ),
        FilterFieldInfo(name="tags", description="repository tags", value_type="list"),
        FilterFieldInfo(name="year", description="publication year", value_type="int"),
    ]


def test_prompt_variables_list_configured_filter_fields():
    node = _node({"repos": _repos_fields()})
    variables = node._get_prompt_variables(
        RAGState(query_domains=["repos"]), LLMNodeContext()
    )
    allowed = variables["allowed_filter_fields"]
    assert "repository_type (string)" in allowed
    assert "github, dandi, biomodels or figshare" in allowed
    assert "tags (list)" in allowed


def test_no_filter_fields_gives_none_configured():
    node = _node({})
    variables = node._get_prompt_variables(
        RAGState(query_domains=["repos"]), LLMNodeContext()
    )
    assert variables["allowed_filter_fields"] == "(none configured)"


def test_unknown_domain_falls_back_to_all_configured_fields():
    node = _node({"repos": _repos_fields()})
    variables = node._get_prompt_variables(
        RAGState(query_domains=["unknown"]), LLMNodeContext()
    )
    assert "repository_type (string)" in variables["allowed_filter_fields"]


def test_braces_in_descriptions_do_not_break_prompt_formatting():
    """Brace-containing descriptions render cleanly through the template."""
    from langchain_core.prompts import ChatPromptTemplate

    fields = [
        FilterFieldInfo(
            name="odd", description="range like {'$gte': x}", value_type="int"
        )
    ]
    allowed = GenerateRetrievalQuery._format_allowed_filter_fields(fields)
    prompt = ChatPromptTemplate(
        [("system", "Allowed filter fields:\n{allowed_filter_fields}")]
    )
    text = prompt.invoke(
        {"allowed_filter_fields": allowed, "query": "q", "feedback": "", "previous": ""}
    ).to_string()
    assert "range like {'$gte': x}" in text


def _result(node: GenerateRetrievalQuery, state: RAGState, **fields):
    """Build a valid instance of the node's per-run output schema."""
    schema = node._get_output_schema(state, LLMNodeContext())
    return schema(**fields)


def test_update_state_normalizes_configured_filter():
    node = _node({"repos": _repos_fields()})
    state = RAGState(query_domains=["repos"])
    result = _result(node, state, search_query="repos", repository_type="github")

    updates = node._update_state(result, state, LLMNodeContext())

    stored = updates["retrieval_query"]
    assert stored.config_filters == [{"repository_type": {"$eq": "github"}}]
    assert stored.filters == {"repository_type": "github"}
    assert len(updates["messages"]) == 1


def test_update_state_handles_multi_value_contains():
    node = _node({"repos": _repos_fields()})
    state = RAGState(query_domains=["repos"])
    result = _result(node, state, search_query="repos", tags=["moose", "ca1"])

    stored = node._update_state(result, state, LLMNodeContext())["retrieval_query"]

    assert stored.config_filters == [
        {
            "$and": [
                {"tags": {"$contains": "moose"}},
                {"tags": {"$contains": "ca1"}},
            ]
        }
    ]


def test_update_state_without_filters_yields_no_clauses():
    node = _node({"repos": _repos_fields()})
    state = RAGState(query_domains=["repos"])
    result = _result(node, state, search_query="repos")

    stored = node._update_state(result, state, LLMNodeContext())["retrieval_query"]

    assert stored.config_filters == []
    assert stored.filters == {}


def test_update_state_maps_numeric_range():
    node = _node({"repos": _repos_fields()})
    state = RAGState(query_domains=["repos"])
    result = _result(node, state, search_query="repos", year={"gte": 2020, "lte": 2025})

    stored = node._update_state(result, state, LLMNodeContext())["retrieval_query"]

    assert stored.config_filters == [
        {"$and": [{"year": {"$gte": 2020}}, {"year": {"$lte": 2025}}]}
    ]
    assert stored.filters == {"year": {"$gte": 2020, "$lte": 2025}}


def test_output_schema_is_typed_and_strict_safe():
    node = _node({"repos": _repos_fields()})
    schema = node._get_output_schema(
        RAGState(query_domains=["repos"]), LLMNodeContext()
    )
    fields = schema.model_fields

    assert set(fields) == {"search_query", "repository_type", "tags", "year"}
    assert fields["search_query"].annotation is str
    # A dynamic-key ``filters`` object would emit ``additionalProperties`` and
    # be closed to ``{}`` by strict providers (ADR-0044).
    assert "additionalProperties" not in json.dumps(schema.model_json_schema())


def test_output_schema_without_configured_fields():
    node = _node({})
    schema = node._get_output_schema(RAGState(query_domains=["x"]), LLMNodeContext())
    assert set(schema.model_fields) == {"search_query"}
