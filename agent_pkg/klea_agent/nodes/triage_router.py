#!/usr/bin/env python3
"""
Triage router node

File: klea_agent/nodes/triage_router.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import override

from fastmcp.client.client import CallToolResult
from klea_utils.nodes.abstract import (
    AbstractRouterNode,
    NodeStreamData,
    NodeStreamEvent,
)

from klea_agent.schemas import KleaAgentState


def current_step_key(state: KleaAgentState) -> int:
    """Return the state key used for the current step's retry counter.

    The key is the 0-based ``plan.current_step_index``; on the ``act``
    (planless) path there is no plan, so the key is ``0``.
    """
    plan = getattr(state, "plan", None)
    return int(getattr(plan, "current_step_index", 0) or 0)


def update_tool_retry_counts(
    state: KleaAgentState, results: list[CallToolResult] | None = None
) -> dict[int, int]:
    """Return the updated per-step consecutive-failure counter (ADaPT).

    A conditional-edge router cannot update state, so the tool caller's
    post-dispatch callback calls this and merges the result.  The counter for
    the current step is incremented when the batch contained any ``is_error``
    result, and cleared when the batch had no errors (progress resets the
    budget).  ``TriageRouter.decide`` then compares the count against
    ``max_retries``.

    :param state: Current graph state.
    :param results: The batch's tool results; defaults to ``state.tool_results``
        when not supplied (e.g. in tests).
    """
    counts = dict(getattr(state, "tool_retry_counts", None) or {})
    step = current_step_key(state)
    batch = results if results is not None else (state.tool_results or [])
    if any(getattr(r, "is_error", False) for r in batch):
        counts[step] = counts.get(step, 0) + 1
    else:
        counts.pop(step, None)
    return counts


class TriageRouter(AbstractRouterNode[KleaAgentState]):
    """Deterministic triage of tool results before semantic evaluation.

    Routes on mechanical failure only (ADR-0035):

    * no error -> ``evaluate`` (hand off to the Evaluator);
    * call-level error with retries left -> ``retry`` (re-pick with the error);
    * retries exhausted -> ``replan`` (escalate to Planner).

    Semantic step/plan judgements belong to the Evaluator.  The retry budget
    is the ADaPT policy: re-pick a step N times, then escalate.  Failure is
    attributed per call, not per batch.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        max_retries: int = 2,
    ):
        """Initialise the triage router.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param max_retries: Re-pick attempts allowed per step before replanning
        """
        super().__init__(logger, label)
        self.max_retries = max_retries

    def decide(self, state: KleaAgentState) -> str:
        """Return the routing label (pure, no streaming).

        Kept separate from :meth:`execute` so the policy is unit-testable
        without a graph run.

        :param state: Current graph state.
        :returns: ``retry`` | ``evaluate`` | ``replan``.
        """
        results = getattr(state, "tool_results", None) or []
        has_error = any(getattr(r, "is_error", False) for r in results)
        if not has_error:
            return "evaluate"
        step = current_step_key(state)
        count = int((getattr(state, "tool_retry_counts", None) or {}).get(step, 0))
        return "retry" if count <= self.max_retries else "replan"

    @override
    async def execute(self, state: KleaAgentState) -> str:
        """Emit progress/info and return the routing label."""
        self.write_custom_stream({"type": "progress", "node": self.label})
        route = self.decide(state)

        results = getattr(state, "tool_results", None) or []
        info = NodeStreamData(
            heading="Triage",
            summary=f"Routing: {route}",
            details={
                "route": route,
                "has_error": any(getattr(r, "is_error", False) for r in results),
                "tool_count": len(results),
                "step": current_step_key(state),
                "retry_count": int(
                    (getattr(state, "tool_retry_counts", None) or {}).get(
                        current_step_key(state), 0
                    )
                ),
                "max_retries": self.max_retries,
            },
        )
        self.write_custom_stream(
            NodeStreamEvent(type="inspect", node=self.label, data=info).model_dump()
        )
        return route
