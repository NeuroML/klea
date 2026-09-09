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

from klea_agent.schemas import (
    CodeSchema,
    Discovery,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
)


class InitGraphState(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Initialise/reset graph state before each iteration.

    Mirrors ``rag_pkg/klea_rag/nodes/init_rag.py``: resets per-turn
    ephemeral fields while preserving ``messages``,
    ``context_summary``/``summarised_till``, and ``discovery_persistent``
    (project-wide discovery that only changes when files change).
    ``usage_metrics`` is intentionally not reset — it uses the
    ``add_token_usage`` reducer and accumulates across turns.
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger."""
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Reset state fields to their initial values."""
        self.write_custom_stream({"type": "progress", "node": self.label})
        return {
            "guard_decision": "safe",
            "message_for_user": "",
            "plan": PlanSchema(),
            "goal": GoalSchema(),
            "tool_calls": [],
            "tool_results": [],
            "step_outputs": {},
            "artefacts": {},
            "discovery_per_step": Discovery(),
            "code": CodeSchema(),
        }
