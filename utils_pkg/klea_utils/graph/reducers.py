#!/usr/bin/env python3
"""
Reducers shared by LangGraph orchestrators.

File: klea_utils/graph/reducers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Any

from klea_utils.graph.schemas import TokenUsage, TokenUsageLedger


def add_usage_ledger(
    left: TokenUsageLedger, right: TokenUsageLedger
) -> TokenUsageLedger:
    """Merge two token usage ledgers, accumulating usage per model.

    Args:
        left: Ledger merged into the result.
        right: Ledger whose per-model usage is accumulated onto ``left``.

    Returns:
        A new ledger holding the per-model accumulation of both inputs.
    """
    left = TokenUsageLedger.model_validate(left)
    right = TokenUsageLedger.model_validate(right)
    merged = dict(left.by_model)
    for key, usage in right.by_model.items():
        merged[key] = add_token_usage(merged.get(key, TokenUsage()), usage)
    return TokenUsageLedger(by_model=merged)


def add_token_usage(
    left: TokenUsage | dict[str, Any], right: TokenUsage | dict[str, Any]
) -> TokenUsage:
    """Add token usage updates from sequential or concurrent graph nodes.

    Only the numeric counters are summed.  ``role`` identifies the node that
    produced a single update, so it is not meaningful for the aggregate and is
    dropped (left empty) rather than concatenated.
    """
    left = TokenUsage.model_validate(left)
    right = TokenUsage.model_validate(right)
    return TokenUsage(
        input_tokens=left.input_tokens + right.input_tokens,
        output_tokens=left.output_tokens + right.output_tokens,
        total_tokens=left.total_tokens + right.total_tokens,
        reasoning_tokens=left.reasoning_tokens + right.reasoning_tokens,
        cached_tokens=left.cached_tokens + right.cached_tokens,
    )
