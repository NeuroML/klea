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
    * ``kind="input"`` asks the questions on the plan's blocked steps (all
      batched into one interrupt) and writes the answers to ``human_input`` so
      the Planner can re-author a runnable plan.

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
            # Flatten the blocked steps' questions; a step with questions is a
            # draft step that cannot run until they are answered (ADR-0046).
            questions = [
                {"step_number": step.step_number, "question": question}
                for step in state.plan.step_list
                for question in step.needs_input
            ]
            return {"kind": "input", "questions": questions}
        return {
            "kind": "review",
            "question": "Review the plan: approve it, request changes, or cancel.",
            # Carry the rendered plan so the form is self-contained (the same
            # payload is re-presented on reload via checkpoint hydration).
            "plan": state.plan.render(markdown=True),
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
        """Apply the review decision, or record the supplied facts."""
        if self.kind == "review":
            return self._apply_review(state, answers)
        answers_list = [str(answer) for answer in (answers.get("answers") or [])]
        human_input = self._map_answers(state, answers_list)
        return {
            "human_input": human_input,
            "replan_reason": "",
            "messages": [
                *state.messages,
                HumanMessage(content=self._render_answers(state, human_input)),
            ],
        }

    @staticmethod
    def _map_answers(state: KleaAgentState, answers: list[str]) -> dict[int, list[str]]:
        """Map the positional answers back onto the blocked steps."""
        mapping: dict[int, list[str]] = {}
        index = 0
        for step in state.plan.step_list:
            if not step.needs_input:
                continue
            count = len(step.needs_input)
            mapping[step.step_number] = answers[index : index + count]
            index += count
        return mapping

    @staticmethod
    def _render_answers(
        state: KleaAgentState, human_input: dict[int, list[str]]
    ) -> str:
        """Render the answers against their step's question for the transcript."""
        asked = {step.step_number: step.needs_input for step in state.plan.step_list}
        lines: list[str] = []
        for step_number, answers in human_input.items():
            questions = asked.get(step_number, [])
            for index, answer in enumerate(answers):
                question = questions[index] if index < len(questions) else "(question)"
                lines.append(f"- Step {step_number} Q: {question} A: {answer}")
        return "\n".join(lines)

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
        question = str(payload.get("question", "")) or "; ".join(
            str(entry.get("question", ""))
            for entry in (payload.get("questions") or [])
            if isinstance(entry, dict)
        )
        note = (
            "User cancelled the run while awaiting an answer to: "
            f"{question or '(the pending question)'}  "
            "No remaining steps were executed."
        )
        return {
            "replan_reason": "",
            "plan": state.plan.model_copy(update={"status": "user_cancelled"}),
            "messages": [*state.messages, AIMessage(content=note)],
        }
