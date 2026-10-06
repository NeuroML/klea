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
from pydantic import BaseModel

from klea_agent.schemas import KleaAgentState


class ReviewResponse(BaseModel):
    """Resume payload for a plan review (the review ``hitl_response_schema``).

    ``action="cancel"`` cancels the run.  ``action="answer"`` carries the
    decision: ``approve`` runs the plan, ``revise`` sends ``feedback`` back to
    the Planner.
    """

    action: Literal["answer", "cancel"] = "answer"
    decision: Literal["approve", "revise"] = "approve"
    feedback: str = ""


class AwaitHuman(AwaitHumanNode[KleaAgentState]):
    """Agent policy for the generic HITL node (ADR-0046).

    One class, two instances:

    * ``kind="review"`` presents the plan for an explicit approve/revise
      decision (or cancel).  An approval sets ``plan.status = in_progress``
      and the router dispatches straight into the execution loop -- no Planner
      call; a revision carries ``feedback`` back to the Planner.
    * ``kind="input"`` asks the Planner's ``pending_question`` and writes the
      answer to ``human_input`` so the Planner can finalise a ``needs_input``
      plan.

    A cancel marks the plan ``user_cancelled``, records a note in
    ``messages``, and lets the router send the run to a terminal reply; the
    remaining plan never executes.  The interrupt mechanism itself is the
    shared :class:`~klea_utils.nodes.await_human.AwaitHumanNode` template.

    Reference: https://docs.langchain.com/oss/python/langgraph/interrupts
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        *,
        kind: Literal["review", "input"],
    ):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param kind: Which ask this instance presents (``review`` | ``input``)
        """
        super().__init__(logger, label, kind=kind)

    @override
    def _ask(self, state: KleaAgentState) -> dict[str, Any]:
        """Return the interrupt payload shown to the user."""
        if self.kind == "input":
            question = (
                state.pending_question or "More information is needed to continue."
            )
            return {"kind": "input", "question": question}
        return {
            "kind": "review",
            "question": "Review the plan: approve it, request changes, or cancel.",
        }

    @override
    def _hitl_response_schema(
        self, state: KleaAgentState, payload: dict[str, Any]
    ) -> type[BaseModel] | None:
        """Return the typed review response model (inputs stay free text)."""
        if self.kind == "review":
            return ReviewResponse
        return None

    @override
    def _on_answer(
        self, state: KleaAgentState, answers: dict[str, Any]
    ) -> dict[str, Any]:
        """Apply the review decision, or record the supplied fact."""
        if self.kind == "review":
            return self._apply_review(state, answers)
        text = str(answers.get("text", ""))
        return {
            "human_input": text,
            "replan_reason": "",
            "messages": [*state.messages, HumanMessage(content=text)],
        }

    def _apply_review(
        self, state: KleaAgentState, answers: dict[str, Any]
    ) -> dict[str, Any]:
        """Approve (run the plan) or revise (back to the Planner)."""
        decision = str(answers.get("decision", "approve"))
        feedback = str(answers.get("feedback", ""))
        if decision == "approve":
            # A human approval is an explicit decision: set the plan runnable
            # so the router dispatches straight into the execution loop, and
            # account for the review round (the Planner is not re-entered).
            plan = state.plan.model_copy(
                update={
                    "status": "in_progress",
                    "human_feedback_rounds": state.plan.human_feedback_rounds + 1,
                    "automated_plan_revisions": 0,
                }
            )
            return {
                "plan": plan,
                "human_feedback": "",
                "replan_reason": "",
                "messages": [
                    *state.messages,
                    AIMessage(content="User approved the plan."),
                ],
            }
        note = (
            f"User requested plan changes: {feedback}"
            if feedback
            else ("User requested plan changes.")
        )
        return {
            "human_feedback": feedback,
            "replan_reason": "",
            "messages": [*state.messages, HumanMessage(content=note)],
        }

    @override
    def _on_cancel(
        self, state: KleaAgentState, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Mark the plan cancelled and record a note.

        The note is written into ``messages`` because ``AnswerFromResults``
        does not append the reply to them, so the next turn's Planner would
        otherwise not see the cancellation.  A note-only message (no plan
        content) is deliberate: a cancelled plan is not carried forward.
        """
        question = str(payload.get("question", ""))
        note = (
            "User cancelled the run while awaiting an answer to: "
            f"{question}  No remaining steps were executed."
        )
        return {
            "replan_reason": "",
            "plan": state.plan.model_copy(update={"status": "user_cancelled"}),
            "messages": [*state.messages, AIMessage(content=note)],
        }
