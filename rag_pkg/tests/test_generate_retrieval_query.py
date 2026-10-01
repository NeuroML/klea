#!/usr/bin/env python3
"""
Tests for the GenerateRetrievalQuery node wiring.

File: rag_pkg/tests/test_generate_retrieval_query.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_rag.nodes.generate_retrieval_query import GenerateRetrievalQuery
from klea_rag.schemas import RAGState, RetrievalQueryOutput
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


def test_update_state_normalizes_filters_and_drops_undeclared():
    node = _node({"repos": _repos_fields()})
    result = RetrievalQueryOutput(
        search_query="repos",
        filters={"repository_type": "github", "bogus": "x"},
    )
    updates = node._update_state(result, RAGState(query_domains=["repos"]))

    stored = updates["retrieval_query"]
    assert stored.config_filters == [{"repository_type": {"$eq": "github"}}]
    # The raw operands are preserved as the LLM produced them.
    assert stored.filters == {"repository_type": "github", "bogus": "x"}
    assert len(updates["messages"]) == 1


def test_update_state_handles_multi_value_contains():
    node = _node({"repos": _repos_fields()})
    result = RetrievalQueryOutput(
        search_query="repos",
        filters={"tags": ["moose", "ca1"]},
    )
    stored = node._update_state(result, RAGState(query_domains=["repos"]))[
        "retrieval_query"
    ]
    assert stored.config_filters == [
        {
            "$and": [
                {"tags": {"$contains": "moose"}},
                {"tags": {"$contains": "ca1"}},
            ]
        }
    ]


def test_update_state_no_allowed_fields_ignores_all_filters():
    node = _node({})
    result = RetrievalQueryOutput(
        search_query="repos", filters={"repository_type": "github"}
    )
    stored = node._update_state(result, RAGState(query_domains=["repos"]))[
        "retrieval_query"
    ]
    assert stored.config_filters == []
    assert stored.filters == {"repository_type": "github"}
