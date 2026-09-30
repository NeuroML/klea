#!/usr/bin/env python3
"""
Answer user node

File: rag_pkg/klea_rag/nodes/answer_user.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, override

from klea_utils.llm import content_to_str, format_alert
from klea_utils.nodes.abstract import AbstractLangGraphNode

from klea_rag.schemas import RAGState


class AnswerUser(AbstractLangGraphNode[RAGState, dict[str, Any]]):
    """Node that returns the final message to the user."""

    #: Hardcoded note appended when the answer is a best-effort delivery:
    #: the evaluator was not satisfied but every retrieval/rewrite budget is
    #: exhausted, so the (grounded) answer is served with this caveat.
    BEST_EFFORT_WARNING = (
        "Note: the retrieved sources only partially covered your query; "
        "this answer may be incomplete."
    )

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        """
        super().__init__(logger, label)

    @override
    async def execute(self, state: RAGState) -> dict[str, Any]:
        """Return the message for the user.

        A ``best_effort`` delivery (evaluation was not "continue") appends
        the hardcoded :attr:`BEST_EFFORT_WARNING`; a clean "continue"
        verdict returns the answer untouched.

        :param state: Current graph state
        :returns: State update with message_for_user
        """
        self._emit_progress()
        self.logger.debug(f"{state =}")

        messages = state.messages
        answer = messages[-1]
        message = content_to_str(answer.content)

        if (
            state.text_response_eval.next_step != "continue"
            and self.BEST_EFFORT_WARNING not in message
        ):
            message = f"{format_alert(self.BEST_EFFORT_WARNING)}\n\n" + message

        self.logger.info(f"Returning final answer to user: {message}")

        return {"message_for_user": message}
