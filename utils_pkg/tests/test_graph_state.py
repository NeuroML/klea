#!/usr/bin/env python3
"""
Tests for the shared BaseGraphSchema state contract (ADR-0032).

File: tests/test_graph_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.graph.schemas import TokenUsage
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
    "usage_metrics",
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
    assert state.usage_metrics == TokenUsage()
    assert state.access_level == "full"


def test_base_schema_exposes_shared_channels():
    """LangGraph reads the annotations, including the reduced usage channel."""
    channels = StateGraph(BaseGraphSchema).channels
    assert SHARED_FIELDS <= set(channels)
    # The inherited ``Annotated[TokenUsage, add_token_usage]`` must still be
    # honoured (an aggregate, not a last-value channel).
    assert isinstance(channels["usage_metrics"], BinaryOperatorAggregate)


def test_subclass_inherits_shared_and_adds_channels():
    """A subclass keeps the shared channels and gains its own."""

    class AppState(BaseGraphSchema):
        extra: str = ""

    channels = StateGraph(AppState).channels
    assert SHARED_FIELDS <= set(channels)
    assert "extra" in channels
