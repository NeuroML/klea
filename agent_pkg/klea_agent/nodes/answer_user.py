#!/usr/bin/env python3
"""
Answer user node

File: klea_agent/nodes/answer_user.py

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


class AnswerUser(AbstractLangGraphNode[KleaAgentState, dict[str, Any]]):
    """Node that returns the final message to the user."""

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        """
        super().__init__(logger, label)

    @override
    async def execute(self, state: KleaAgentState) -> dict[str, Any]:
        """Return the message for the user.

        :param state: Current graph state
        :returns: State update with message_for_user
        """
        self.write_custom_stream({"type": "progress", "node": self.label})
        self.logger.debug(f"{state =}")

        answer = state.message_for_user
        self.logger.info(f"Returning final answer to user: {answer}")

        info = NodeStreamData(
            heading="Response",
            summary=f"Answer ready ({len(answer)} chars)",
            details={"char_count": len(answer)},
        )
        self.write_custom_stream(
            NodeStreamEvent(type="info", node=self.label, data=info).model_dump()
        )

        return {"message_for_user": answer}
