#!/usr/bin/env python3
"""
Task-state boundary for the agent graph.

See devdocs/adr/0054-cross-run-continuity.md.

File: klea_agent/nodes/reset_state.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, override

from klea_utils.nodes.abstract import AbstractLangGraphNode
from klea_utils.nodes.context import NodeContext

from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
)


class ResetState(AbstractLangGraphNode[KleaAgentState, dict[str, Any], NodeContext]):
    """Apply the task boundary selected by ``RouteDecision``.

    ``task`` starts a fresh goal and plan.  ``continue`` also starts a fresh
    plan for a new goal, but preserves the bounded ``continuation_evidence``
    record so the Planner can use prior work.  Transient execution fields are
    cleared in both cases.

    ``InitGraphState`` deliberately leaves the prior task state intact so
    ``RouteDecision`` can inspect it and choose between ``task`` and
    ``continue``.
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger."""
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Reset the plan-scoped state for the selected route."""
        self._emit_progress()
        route = state.route.route
        update = self._task_reset()
        if route == "continue" and state.continuation_evidence is not None:
            update.pop("continuation_evidence", None)
        self.logger.debug(
            f"{route = }\n"
            f"has_continuation_evidence = {state.continuation_evidence is not None}\n"
            f"{update.keys() = }"
        )
        return update

    @staticmethod
    def _task_reset() -> dict[str, Any]:
        """Return the full plan-scoped reset shared by task boundaries."""
        return {
            "plan": PlanSchema(),
            "plan_history": [],
            "goal": GoalSchema(),
            "evaluation": EvaluationSchema(),
            "step_outputs": {},
            "tool_retry_counts": {},
            "step_attempt_counts": {},
            "picker_attempts": 0,
            "picker_step": -1,
            "binding_calls": [],
            "failure_reason": "",
            "human_feedback": "",
            "human_input": {},
            "replan_reason": "",
            "review_progress": "",
            "tool_calls": [],
            "tool_results": [],
            "continuation_evidence": None,
        }
