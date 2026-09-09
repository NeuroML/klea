#!/usr/bin/env python3
"""
Evaluator node for KleaAgent

File: klea_agent/nodes/evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, override

from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)

from klea_agent.schemas import KleaAgentState

# TODO: complete — evaluator is WIP; scope may split into plan + result evaluators


class Evaluator(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Node that evaluates whether all plan steps are completed.

    Scope is intentionally narrow for now — it only checks
    ``current_step_index >= len(step_list)`` to mark ``completed``.
    A future split into plan + result evaluators (separate verification)
    may replace this.
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        """
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Check if all steps are completed and update plan status.

        :param state: Current graph state
        :returns: State update with plan status
        """
        self.write_custom_stream({"type": "progress", "node": self.label})
        self.logger.debug(f"{state =}")
        plan = state.plan
        result: dict[str, Any] = {}

        if plan.current_step_index >= len(plan.step_list):
            plan.status = "completed"
            result["plan"] = plan

        # Inspector streams — keep evaluators inspectable per ADR-0013
        info = NodeStreamData(
            heading="Plan Evaluation",
            summary=f"Plan status: {plan.status} ({plan.current_step_index}/{len(plan.step_list)})",
            details={
                "status": plan.status,
                "current_step_index": plan.current_step_index,
                "total_steps": len(plan.step_list),
            },
        )
        self.write_custom_stream(
            NodeStreamEvent(type="info", node=self.label, data=info).model_dump()
        )

        return result
