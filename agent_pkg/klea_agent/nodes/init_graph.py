#!/usr/bin/env python3
"""
Initialise graph state node

File: klea_agent/nodes/init_graph.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, override

from klea_utils.nodes.abstract import AbstractLangGraphNode
from langchain_core.messages import HumanMessage

from klea_agent.schemas import (
    CodeSchema,
    Discovery,
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    RouteSchema,
)


class InitGraphState(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Initialise/reset graph state before each iteration.

    Mirrors ``rag_pkg/klea_rag/nodes/init_rag.py``: resets per-turn
    ephemeral fields while preserving ``messages``,
    ``context_summary``/``summarised_till``, ``discovery_persistent``
    (project-wide discovery that only changes when files change), and
    ``mode`` (session-scoped, ADR-0030).  ``usage_metrics`` is intentionally
    not reset -- it uses the ``add_token_usage`` reducer and accumulates
    across turns.
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger."""
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Reset state fields to their initial values.

        Also appends the current query to ``messages`` (run history), which is
        preserved across turns so the Planner's memory and summarisation see
        the conversation.  A HITL resume does not re-run this node, so the
        query is recorded once per turn.
        """
        self.write_custom_stream({"type": "progress", "node": self.label})
        return {
            "guard_decision": "safe",
            "message_for_user": "",
            "plan": PlanSchema(),
            "goal": GoalSchema(),
            "route": RouteSchema(),
            "evaluation": EvaluationSchema(),
            "tool_retry_counts": {},
            "step_attempt_counts": {},
            "plan_revisions": 0,
            "picker_attempts": 0,
            "picker_step": -1,
            "tool_rounds": 0,
            "failure_reason": "",
            "human_feedback": "",
            "replan_reason": "",
            "pending_question": "",
            "tool_calls": [],
            "tool_results": [],
            "step_outputs": {},
            "artefacts": {},
            "discovery_per_step": Discovery(),
            "code": CodeSchema(),
            "messages": [*state.messages, HumanMessage(content=state.query)],
        }
