#!/usr/bin/env python3
"""
Tests for shared LangGraph reducers.

File: tests/test_reducers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from klea_utils.graph.reducers import add_token_usage
from klea_utils.graph.schemas import TokenUsage


def test_add_token_usage_accepts_dictionary_updates() -> None:
    """Reducer accepts the dictionaries supplied by LangGraph channels."""
    left = {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    right = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60}

    assert add_token_usage(left, right) == TokenUsage(
        input_tokens=150,
        output_tokens=30,
        total_tokens=180,
    )


@pytest.mark.parametrize(
    ("left", "right"),
    [
        (
            TokenUsage(input_tokens=100, output_tokens=20, total_tokens=120),
            {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60},
        ),
        (
            {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
            TokenUsage(input_tokens=50, output_tokens=10, total_tokens=60),
        ),
    ],
)
def test_add_token_usage_accepts_mixed_operands(
    left: TokenUsage | dict[str, int], right: TokenUsage | dict[str, int]
) -> None:
    """Reducer accepts either model or dictionary operands in either position."""
    assert add_token_usage(left, right) == TokenUsage(
        input_tokens=150,
        output_tokens=30,
        total_tokens=180,
    )


def test_add_token_usage_combines_parallel_deltas() -> None:
    """Reducer combines independent node deltas into graph totals."""
    initial = TokenUsage()
    retrieval_delta = {
        "input_tokens": 300,
        "output_tokens": 40,
        "total_tokens": 340,
    }
    tool_delta = {
        "input_tokens": 200,
        "output_tokens": 30,
        "total_tokens": 230,
    }

    after_retrieval = add_token_usage(initial, retrieval_delta)
    final_usage = add_token_usage(after_retrieval, tool_delta)

    assert final_usage == TokenUsage(
        input_tokens=500,
        output_tokens=70,
        total_tokens=570,
    )


def test_add_token_usage_sums_reasoning_and_cached_and_drops_role() -> None:
    """Reasoning/cache counters sum; ``role`` is identity and is dropped."""
    left = TokenUsage(
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        reasoning_tokens=12,
        cached_tokens=64,
        role="chat",
    )
    right = {
        "input_tokens": 50,
        "output_tokens": 10,
        "total_tokens": 60,
        "reasoning_tokens": 5,
        "cached_tokens": 16,
        "role": "plan",
    }

    combined = add_token_usage(left, right)

    assert combined.reasoning_tokens == 17
    assert combined.cached_tokens == 80
    assert combined.role == ""
