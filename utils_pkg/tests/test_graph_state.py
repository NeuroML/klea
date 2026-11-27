#!/usr/bin/env python3
"""
Tests for the shared BaseGraphSchema state contract (ADR-0032).

File: tests/test_graph_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.graph.reducers import add_usage_ledger
from klea_utils.graph.schemas import TokenUsage, TokenUsageLedger
from klea_utils.graph.state import BaseGraphSchema
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.graph import StateGraph

SHARED_FIELDS = {
    "query",
    "messages",
    "guard_decision",
    "context_summary",
    "summarised_till",
    "message_for_user",
    "tool_calls",
    "tool_results",
    "token_usage_ledger",
    "access_level",
}


def test_base_schema_defaults():
    """The shared fields carry the documented defaults."""
    state = BaseGraphSchema()
    assert state.query == ""
    assert state.messages == []
    assert state.guard_decision == "safe"
    assert state.context_summary == ""
    assert state.summarised_till == 0
    assert state.message_for_user == ""
    assert state.tool_calls == []
    assert state.tool_results == []
    assert state.token_usage_ledger == TokenUsageLedger()
    assert state.access_level == "full"


def test_base_schema_exposes_shared_channels():
    """LangGraph reads the annotations, including the reduced usage channel."""
    channels = StateGraph(BaseGraphSchema).channels
    assert SHARED_FIELDS <= set(channels)
    # The inherited ``Annotated[TokenUsageLedger, add_usage_ledger]`` must still
    # be honoured (an aggregate, not a last-value channel).
    assert isinstance(channels["token_usage_ledger"], BinaryOperatorAggregate)


def test_subclass_inherits_shared_and_adds_channels():
    """A subclass keeps the shared channels and gains its own."""

    class AppState(BaseGraphSchema):
        extra: str = ""

    channels = StateGraph(AppState).channels
    assert SHARED_FIELDS <= set(channels)
    assert "extra" in channels


def test_add_usage_ledger_unions_keys_and_accumulates():
    """Two writes with different models coexist and per-key totals add up."""
    left = TokenUsageLedger(
        by_model={
            "model-a": TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)
        }
    )
    right = TokenUsageLedger(
        by_model={
            "model-a": TokenUsage(input_tokens=4, output_tokens=5, total_tokens=9),
            "model-b": TokenUsage(input_tokens=6, output_tokens=7, total_tokens=13),
        }
    )

    merged = add_usage_ledger(left, right)

    assert set(merged.by_model) == {"model-a", "model-b"}
    assert merged.by_model["model-a"] == TokenUsage(
        input_tokens=5, output_tokens=7, total_tokens=12
    )
    assert merged.by_model["model-b"] == TokenUsage(
        input_tokens=6, output_tokens=7, total_tokens=13
    )


def test_empty_ledger_total_is_zero():
    """A fresh ledger folds to an all-zero ``TokenUsage``."""
    assert TokenUsageLedger().total() == TokenUsage()


def test_total_by_model_returns_independent_copy():
    """``total_by_model()`` hands back a copy callers cannot mutate."""
    ledger = TokenUsageLedger(
        by_model={
            "model-a": TokenUsage(input_tokens=1, output_tokens=2, total_tokens=3)
        }
    )

    snapshot = ledger.total_by_model()
    snapshot["model-a"] = TokenUsage()
    snapshot["model-b"] = TokenUsage()

    assert ledger.by_model["model-a"] == TokenUsage(
        input_tokens=1, output_tokens=2, total_tokens=3
    )
    assert "model-b" not in ledger.by_model
