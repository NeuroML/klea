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
from klea_utils.nodes.base import EMPTY_RESULT_FALLBACK
from klea_utils.nodes.context import NodeContext
from langchain_core.messages import AIMessage

from klea_agent.schemas import KleaAgentState

logger = logging.getLogger(__name__)

#: Maximum number of characters of the plan query echoed in the history
#: marker.  It keeps the marker small against the conversation-history budget
#: while leaving a one-sentence plan query intact.
_HISTORY_MARKER_QUERY_CHARS = 200


class AnswerUser(AbstractLangGraphNode[KleaAgentState, dict[str, Any], NodeContext]):
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
        self._emit_progress()
        self.logger.debug(
            "AnswerUser.execute: entry query_chars=%d answer_chars=%d "
            "messages=%d assurance=%s",
            len(state.query),
            len(state.message_for_user),
            len(state.messages),
            state.mode.assurance,
        )

        answer = state.message_for_user
        # Last-resort guard: never deliver a blank reply.  A producer may
        # legitimately fail to write a message (e.g. the router chose ``chat``
        # but emitted an empty inline answer), so substitute a retry message
        # at the single delivery point.
        if not answer.strip():
            self.logger.warning(
                "AnswerUser.execute: empty answer; substituting fallback"
            )
            answer = EMPTY_RESULT_FALLBACK
        self.logger.info(
            "AnswerUser.execute: returning final answer to user (%d chars)",
            len(answer),
        )

        info = NodeStreamData(
            heading="Response",
            summary=(
                f"Answer ready ({len(answer)} chars; assurance={state.mode.assurance})"
            ),
            details={
                "char_count": len(answer),
                "assurance": state.mode.assurance,
            },
        )
        self.logger.debug(
            "AnswerUser.execute: built inspect payload heading=%s",
            info.heading,
        )
        self.write_custom_stream(
            NodeStreamEvent(type="inspect", node=self.label, data=info).model_dump()
        )

        # The assistant message appended to the conversation history carries the
        # plan query as a marker so the memory summariser keeps the plan's final
        # answer instead of compressing it away.  Only ``message_for_user`` --
        # and hence the streamed and persisted user-visible reply -- stays bare.
        query_excerpt = state.query.strip()
        if len(query_excerpt) > _HISTORY_MARKER_QUERY_CHARS:
            query_excerpt = query_excerpt[:_HISTORY_MARKER_QUERY_CHARS] + "..."
        if query_excerpt:
            history_answer = f"Final answer for plan query ({query_excerpt}): {answer}"
        else:
            history_answer = f"Final answer for plan query: {answer}"
        self.logger.debug(
            "AnswerUser.execute: history marker assembled query_excerpt_chars=%d "
            "history_answer_chars=%d",
            len(query_excerpt),
            len(history_answer),
        )

        return {
            "message_for_user": answer,
            "messages": [*state.messages, AIMessage(content=history_answer)],
        }
