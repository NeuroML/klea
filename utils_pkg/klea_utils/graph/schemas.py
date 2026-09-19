#!/usr/bin/env python3
"""
Schemas shared by LangGraph orchestrators.

File: klea_utils/graph/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pydantic import BaseModel


class TokenUsage(BaseModel):
    """Token usage for one node, or accumulated across a graph run.

    ``role``, ``reasoning_tokens`` and ``cached_tokens`` are populated on the
    per-node ``usage`` stream event so a run can be benchmarked per node and
    per role.  When this schema is accumulated into the graph state's
    ``usage_metrics`` (see :func:`~klea_utils.graph.reducers.add_token_usage`)
    only the numeric fields are summed; ``role`` is identity, not a quantity,
    so it is dropped from the aggregate.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    #: Hidden reasoning/thinking tokens reported by the provider (subset of
    #: ``output_tokens`` when the provider exposes them).
    reasoning_tokens: int = 0
    #: Prompt tokens served from the provider's prompt cache.
    cached_tokens: int = 0
    #: Model role that produced this usage (e.g. ``chat``, ``plan``, ``guard``).
    role: str = ""
