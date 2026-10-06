#!/usr/bin/env python3
"""
Await-human node (agent policy)

File: klea_agent/nodes/await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, Literal, override

from klea_utils.nodes.await_human import AwaitHumanNode
from langchain_core.messages import AIMessage, HumanMessage

from klea_agent.schemas import KleaAgentState


class AwaitHuman(AwaitHumanNode[KleaAgentState]):
    """Agent policy for the generic HITL node (ADR-0046).

    One class, two instances:

    * ``kind="review"`` asks the user to review the plan and writes the answer
      to ``human_feedback`` (the Planner owns the ``in_review`` transition);
    * ``kind="input"`` asks the Planner's ``pending_question`` and writes the
      answer to ``human_input`` so the Planner can finalise a ``needs_input``
      plan.

    On cancel it marks the plan ``user_cancelled``, records a note in
    ``messages``, and lets ``_await_human_router`` send the run to a terminal
    reply; the remaining plan never executes.  The interrupt mechanism itself
    (pause/resume, sentinel parsing, event emission) is the shared
    :class:`~klea_utils.nodes.await_human.AwaitHumanNode` template.

    Reference: https://docs.langchain.com/oss/python/langgraph/interrupts
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        *,
        kind: Literal["review", "input"],
        answer_field: str,
    ):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param kind: Which ask this instance presents (``review`` | ``input``)
        :param answer_field: State field the answer is written to
            (``human_feedback`` for review, ``human_input`` for input)
        """
        super().__init__(logger, label, kind=kind, answer_field=answer_field)

    @override
    def _question(self, state: KleaAgentState) -> str:
        """Return the ask to present to the user."""
        if self.kind == "input":
            return state.pending_question or "More information is needed to continue."
        return "Review the plan: reply to approve, or with your feedback."

    @override
    def _on_answer(self, state: KleaAgentState, text: str) -> dict[str, Any]:
        """Record the answer in ``messages`` and clear any stale replan reason."""
        return {
            "replan_reason": "",
            "messages": [*state.messages, HumanMessage(content=text)],
        }

    @override
    def _on_cancel(self, state: KleaAgentState, question: str) -> dict[str, Any]:
        """Mark the plan cancelled and record a note.

        The note is written into ``messages`` because ``AnswerFromResults``
        does not append the reply to them, so the next turn's Planner would
        otherwise not see the cancellation.  A note-only message (no plan
        content) is deliberate: a cancelled plan is not carried forward.
        """
        note = (
            "User cancelled the run while awaiting an answer to: "
            f"{question}  No remaining steps were executed."
        )
        return {
            "replan_reason": "",
            "plan": state.plan.model_copy(update={"status": "user_cancelled"}),
            "messages": [*state.messages, AIMessage(content=note)],
        }
