#!/usr/bin/env python3
"""
Tests for the retrieval-query output schema

File: rag_pkg/tests/test_schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from klea_rag.rag import RAG
from klea_rag.schemas import (
    EvaluateAnswerSchema,
    RAGState,
    RetrievalQueryOutput,
)
from klea_utils.graph.schemas import TokenUsage
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.mcp.schemas import ToolCallSchema
from klea_utils.stores.filters import translate_metadata_filter
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import StateGraph


def test_no_constraints_returns_none():
    assert RetrievalQueryOutput().to_metadata_filter() is None


def test_single_config_clause_returned_directly():
    rq = RetrievalQueryOutput(
        search_query="repos", config_filters=[{"repository_type": {"$eq": "github"}}]
    )
    assert rq.to_metadata_filter() == {"repository_type": {"$eq": "github"}}


def test_multiple_config_clauses_wrapped_in_and():
    rq = RetrievalQueryOutput(
        search_query="repos",
        config_filters=[
            {"repository_type": {"$eq": "github"}},
            {"content_types": {"$eq": "modeling"}},
            {"year": {"$gte": 2020}},
        ],
    )
    assert rq.to_metadata_filter() == {
        "$and": [
            {"repository_type": {"$eq": "github"}},
            {"content_types": {"$eq": "modeling"}},
            {"year": {"$gte": 2020}},
        ]
    }


def test_config_multi_contains_and_merges():
    """A nested ``$and`` clause from normalize_config_filters merges as-is."""
    rq = RetrievalQueryOutput(
        search_query="papers",
        config_filters=[
            {
                "$and": [
                    {"tags": {"$contains": "moose"}},
                    {"tags": {"$contains": "ca1"}},
                ]
            },
            {"journal": {"$eq": "nature"}},
        ],
    )
    assert rq.to_metadata_filter() == {
        "$and": [
            {
                "$and": [
                    {"tags": {"$contains": "moose"}},
                    {"tags": {"$contains": "ca1"}},
                ]
            },
            {"journal": {"$eq": "nature"}},
        ]
    }


def test_raw_filters_do_not_affect_metadata_filter():
    """to_metadata_filter() runs only on the normalized config_filters."""
    rq = RetrievalQueryOutput(
        search_query="repos",
        filters={"repository_type": "github"},
        config_filters=[{"repository_type": {"$eq": "github"}}],
    )
    assert rq.to_metadata_filter() == {"repository_type": {"$eq": "github"}}

    rq2 = RetrievalQueryOutput(search_query="repos", filters={"tags": "moose"})
    assert rq2.to_metadata_filter() is None


def test_raw_and_normalized_filters_survive_roundtrip():
    rq = RetrievalQueryOutput(
        search_query="repos",
        filters={"repository_type": ["github", "dandi"]},
        config_filters=[{"repository_type": {"$in": ["github", "dandi"]}}],
    )
    restored = RetrievalQueryOutput.model_validate(rq.model_dump())
    assert restored.filters == {"repository_type": ["github", "dandi"]}
    assert restored.config_filters == [
        {"repository_type": {"$in": ["github", "dandi"]}}
    ]


def test_filter_accepts_backend_translation():
    rq = RetrievalQueryOutput(
        search_query="repos",
        config_filters=[
            {"repository_type": {"$in": ["github", "dandi"]}},
            {"tags": {"$contains": "moose"}},
        ],
    )
    out = rq.to_metadata_filter()
    assert out is not None
    translated = translate_metadata_filter("chroma:/data/store", out)
    assert translated["$and"][0] == {"repository_type": {"$in": ["github", "dandi"]}}
    assert translated["$and"][1] == {"tags": {"$contains": "moose"}}


def test_malformed_config_clauses_rejected_on_translation():
    """Malformed operator expressions surface as ValidationError (ValueError)."""
    rq = RetrievalQueryOutput(
        search_query="repos", config_filters=[{"tags": {"$nonexistent": 1}}]
    )
    out = rq.to_metadata_filter()
    assert out is not None
    with pytest.raises(ValueError):
        translate_metadata_filter("chroma:/data/store", out)


def test_rag_state_inherits_base_graph_schema():
    """RAGState extends the shared BaseGraphSchema (ADR-0032)."""
    assert issubclass(RAGState, BaseGraphSchema)


def test_rag_state_shared_defaults():
    state = RAGState()
    assert state.query == ""
    assert state.messages == []
    assert state.guard_decision == "safe"
    assert state.summarised_till == 0
    assert state.message_for_user == ""
    assert state.tool_calls == []
    assert state.tool_results == []
    assert state.usage_metrics == TokenUsage()


def test_rag_state_is_read_only():
    """RAG is fixed at read_only (ADR-0037): it retrieves, never mutates."""
    assert RAGState().access_level == "read_only"


def test_rag_state_channels_present():
    channels = StateGraph(RAGState).channels
    assert {"messages", "tool_calls", "tool_results", "context_summary"} <= set(
        channels
    )
    assert {"query_domains", "retrieval_query"} <= set(channels)
    assert isinstance(channels["usage_metrics"], BinaryOperatorAggregate)


def test_rag_state_models_roundtrip():
    """Shared and RAG-specific checkpointed models round-trip through msgpack."""
    allowed = RAG.__new__(RAG).get_allowed_msgpack_modules()
    serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
    payload = {
        "text_response_eval": EvaluateAnswerSchema(coverage=0.5),
        "retrieval_query": RetrievalQueryOutput(search_query="neurons"),
        "tool_calls": [ToolCallSchema(tool="read_file", args={"path": "x.md"})],
        "usage_metrics": TokenUsage(input_tokens=4, output_tokens=5, total_tokens=9),
        "retrieval_attempts": 2,
        "rewrite_attempts": 1,
    }
    type_name, data = serde.dumps_typed(payload)
    assert serde.loads_typed((type_name, data)) == payload
