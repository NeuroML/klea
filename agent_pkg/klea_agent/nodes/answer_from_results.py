#!/usr/bin/env python3
"""
Answer-from-results node

File: klea_agent/nodes/answer_from_results.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import re
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.tools import textualize_tool_results
from pydantic import BaseModel

from klea_agent.schemas import ArtefactSchema, KleaAgentState


class AnswerSchema(BaseModel):
    """Structured output of the answer-synthesis node."""

    answer: str = ""


def _slug(text: str, max_len: int = 60) -> str:
    """Return a stable, filesystem-safe id slug from *text*.

    Lowercases, collapses non-alphanumeric runs to single hyphens and trims to
    *max_len* characters, so a goal maps to a short id (used as the artefact
    key; re-running the same goal supersedes the previous artefact).
    """
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:max_len].strip("-")


class AnswerFromResults(BaseLLMNode[KleaAgentState, AnswerSchema]):
    """Synthesise the final user-facing reply (ADR-0035).

    Separate from the Evaluator by design: evaluation judges, this node
    generates.  It runs once at the end of a run -- on success (``plan_done``),
    on failure (``abort``/``unplannable``/``failure_reason``), or on
    ``needs_input`` (present the pending question) -- and turns the goal, the
    plan and the observations into a reply.  On failure it explains concisely
    what was attempted and why it could not be completed.  In scientific mode a
    grounded/cited variant replaces it; the Evaluator contract (judge only)
    stays the same.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.2,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
    ):
        """Initialise the answer node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=AnswerSchema,
            memory=memory,
        )

    def _observations_text(self, state: KleaAgentState) -> str:
        """Return the rendered per-step tool outputs (tool + displayed flag).

        Delegates to :meth:`KleaAgentState.observations_text` so the answer
        node, Planner and Evaluator see identical observations.
        """
        return state.observations_text()

    @staticmethod
    def _is_failure(state: KleaAgentState) -> bool:
        """Return True when the run ended in failure rather than success."""
        return bool(state.failure_reason) or state.plan.status in (
            "aborted",
            "unplannable",
        )

    @staticmethod
    def _outcome(state: KleaAgentState) -> str:
        """Return the run outcome: ``success`` | ``failure`` | ``needs_input``.

        ``needs_input`` is the failure answer's sibling: the task is not
        complete, but it can proceed once the user answers the pending
        question (``plan.status == needs_input``).
        """
        if state.plan.status == "needs_input" or state.pending_question:
            return "needs_input"
        return "failure" if AnswerFromResults._is_failure(state) else "success"

    @override
    def _get_prompt_variables(self, state: KleaAgentState) -> dict:
        """Format prompt with the outcome, goal, plan, observations and query.

        The failure reason and pending question are mutually exclusive and are
        folded into one ``outcome_details`` block that is empty on success, so
        the prompt never carries an empty label (prompt conventions).
        """
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        variables = {
            "query": state.query,
            "outcome": self._outcome(state),
            "outcome_details": self._outcome_details(state),
            "goal": goal_text,
            "plan": state.plan.render(),
            "observations": self._observations_text(state),
        }
        self.logger.debug(f"{variables = }")
        return variables

    def _outcome_details(self, state: KleaAgentState) -> str:
        """Return the outcome-specific detail block, or ``""`` on success.

        Only the relevant detail renders, with its own label, so a successful
        run's prompt contains no empty ``Failure reason:`` or
        ``Pending question:`` line.
        """
        outcome = self._outcome(state)
        if outcome == "needs_input":
            question = (
                state.pending_question or "More information is needed to continue."
            )
            return f"Pending question: {question}"
        if outcome == "failure":
            return f"Failure reason: {state.failure_reason}"
        return ""

    @override
    def _update_state(
        self, result: AnswerSchema, state: KleaAgentState
    ) -> dict[str, Any]:
        """Write the final message and persist the task deliverable.

        On success the task's deliverable (a concise artefact: the goal plus the
        answer) is written to the session-scoped ``artefacts`` so a later task
        in the same session can build on it.  The full user-facing reply stays
        in ``message_for_user`` and ``messages`` (lossy continuity); the
        artefact is the concise, addressable record.  On failure or
        ``needs_input`` nothing is persisted -- there is no deliverable yet.
        """
        answer = result.answer.strip() or self._fallback_answer(state)
        update: dict[str, Any] = {"message_for_user": answer}
        if self._outcome(state) == "success":
            artefact = self._deliverable_artefact(state, answer)
            artefacts = dict(state.artefacts or {})
            artefacts[artefact.id_] = artefact
            update["artefacts"] = artefacts
        self.logger.debug(f"{answer = }\n{update.get('artefacts') = }")
        return update

    @staticmethod
    def _deliverable_artefact(state: KleaAgentState, answer: str) -> ArtefactSchema:
        """Build the concise, session-scoped artefact for this task.

        The id is derived from the goal so a re-run of the same task supersedes
        rather than accumulates.  ``content`` is the concise result (the goal
        and the answer); ``metadata`` records provenance (the goal and the plan
        status) for later reference.
        """
        goal = state.goal.goal.strip()
        artefact_id = _slug(goal) or "task-result"
        content = f"Goal: {goal}\nResult: {answer}" if goal else answer
        return ArtefactSchema(
            id_=artefact_id,
            type_="result",
            content=content,
            metadata={"goal": goal, "plan_status": state.plan.status},
        )

    def _fallback_answer(self, state: KleaAgentState) -> str:
        """Return a non-empty answer when synthesis produced nothing.

        On ``needs_input``, asks the pending question.  On failure, reports
        that the task could not be completed and why.  On success, prefers the
        latest tool outputs (which are often the answer, e.g. a command's
        output), then the completed step description.
        """
        outcome = self._outcome(state)
        if outcome == "needs_input":
            question = (
                state.pending_question or "More information is needed to continue."
            )
            return f"I need more information to continue: {question}"
        if outcome == "failure":
            reason = state.failure_reason or "the task could not be completed"
            return f"I could not complete this task: {reason}."
        if state.tool_results:
            return textualize_tool_results(state.tool_results)
        step = state.plan.current_step()
        # A completed plan has ``current_step_index == len(step_list)``, so
        # fall back to the final step's description.
        if step is None and state.plan.step_list:
            step = state.plan.step_list[-1]
        if step and step.description:
            return f"Completed: {step.description}"
        return "Done."

    @override
    def _get_inspect(self) -> NodeStreamData:
        """Return the answer summary plus input prompt and raw/processed output."""
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        result = self._last_result
        if isinstance(result, AnswerSchema):
            summary = f"Answer ready ({len(result.answer)} chars)"
            char_count = len(result.answer)
        else:
            summary = "Answer ready"
            char_count = 0
        return NodeStreamData(
            heading="Answer",
            summary=summary,
            details={
                "char_count": char_count,
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            },
        )

    @override
    def _get_default_error_result(self) -> AnswerSchema:
        """Return an empty result; ``_update_state`` falls back deterministically."""
        return AnswerSchema()
