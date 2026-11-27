#!/usr/bin/env python3
"""
Schemas shared by LangGraph orchestrators.

File: klea_utils/graph/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pydantic import BaseModel, Field


class TokenUsage(BaseModel):
    """Token usage for one LLM call, or for one model across a run.

    This is the unit of accounting: an instance describes the tokens billed
    for one call (``role`` names the node role that produced it) and, keyed by
    model string, the per-model entries of :class:`TokenUsageLedger`, the graph
    state's cumulative token channel.  ``reasoning_tokens`` and
    ``cached_tokens`` are populated on the per-node ``usage`` stream event so a
    run can be benchmarked per node and per role.  Summing instances is done
    by :func:`~klea_utils.graph.reducers.add_token_usage`, which drops
    ``role`` because identity is not a quantity.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    #: Hidden reasoning/thinking tokens reported by the provider (subset of
    #: ``output_tokens`` when the provider exposes them).
    reasoning_tokens: int = 0
    #: Prompt tokens served from the provider's prompt cache (cache reads
    #: only; cache-creation/write tokens remain part of ``input_tokens``).
    cached_tokens: int = 0
    #: Model role that produced this usage (e.g. ``chat``, ``plan``, ``guard``).
    role: str = ""


class TokenUsageLedger(BaseModel):
    """Cumulative token usage for a thread, broken down by model.

    ``by_model`` maps the effective model string a node reports (``"unknown"``
    when no model is configured) to the tokens billed for that model in this
    thread.  The channel is reduced by
    :func:`~klea_utils.graph.reducers.add_usage_ledger`, so nodes writing
    different models coexist and per-model totals stay additive.  A fresh
    thread, or one checkpointed before this field existed, yields an empty map
    and an all-zero :meth:`total`.
    """

    by_model: dict[str, TokenUsage] = Field(default_factory=dict)

    def total(self) -> TokenUsage:
        """Return the flat thread total across all models.

        Folds ``by_model`` with
        :func:`~klea_utils.graph.reducers.add_token_usage`; an empty ledger
        yields an all-zero :class:`TokenUsage`.

        :returns: The summed token usage for the whole thread.
        """
        # Imported here because ``reducers`` imports ``TokenUsage`` from this
        # module, so a module-level import would be circular.
        from klea_utils.graph.reducers import add_token_usage

        total = TokenUsage()
        for usage in self.by_model.values():
            total = add_token_usage(total, usage)
        return total

    def total_by_model(self) -> dict[str, TokenUsage]:
        """Return a copy of the per-model breakdown.

        The copy keeps callers from mutating the channel's dict in place.

        :returns: A new dict mapping model string to its token usage.
        """
        return dict(self.by_model)
