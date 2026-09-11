#!/usr/bin/env python3
"""
Await-review node

File: klea_agent/nodes/await_review.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, override

from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)
from langchain_core.messages import HumanMessage

from klea_agent.schemas import KleaAgentState


class AwaitReview(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Capture the human's review of a plan (ADR-0035).

    Human evaluation, symmetric to the operational ``Evaluator``: it does not
    judge and carries no LLM call; it only captures the user's free-text input
    into ``human_feedback`` and hands it back to the Planner, which interprets
    it and owns the ``in_review`` <-> ``in_progress`` transition.

    Real input uses LangGraph ``interrupt``/resume (ADR-0037).  Until that
    lands this node is a stub that supplies a canned approval so the
    Planner <-> AwaitReview loop can be exercised end to end.
    """

    #: Canned approval supplied by the stub (replaced by real user input).
    STUB_REVIEW = "Looks good, proceed."

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        """
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Return the (stubbed) human review input.

        :param state: Current graph state
        :returns: State update carrying ``human_feedback``
        """
        self.write_custom_stream({"type": "progress", "node": self.label})
        self.logger.debug(f"{state = }")

        feedback = self.STUB_REVIEW
        info = NodeStreamData(
            heading="Review",
            summary="Auto-approved (stub: interrupt not implemented yet)",
            details={"human_feedback": feedback},
        )
        self.write_custom_stream(
            NodeStreamEvent(type="info", node=self.label, data=info).model_dump()
        )
        return {
            "human_feedback": feedback,
            "messages": [*state.messages, HumanMessage(content=feedback)],
        }
