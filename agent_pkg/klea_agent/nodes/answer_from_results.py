#!/usr/bin/env python3
"""
Answer-from-results node

See ``devdocs/system/agent-general-path-control-flow.md`` and
``devdocs/system/c4-component-agent.md``; ADR-0035.

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
from klea_utils.nodes.context import LLMNodeContext
from pydantic import BaseModel

from klea_agent.schemas import ArtefactSchema, KleaAgentState


class AnswerSchema(BaseModel):
    # The answer-synthesis node's structured output.  Formatting rules live in
    # AnswerFromResults_system.md.
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
    generates.  It runs once at the end of a run -- on success (``plan_done``)
    or on failure (``abort``/``unplannable``/``failure_reason``) -- and turns
    the goal, the plan, the session artefacts and the observations into a reply.
    On failure it explains concisely what was attempted and why it could not be
    completed.
    In scientific mode a grounded/cited variant replaces it; the Evaluator
    contract (judge only) stays the same.

    A draft plan with pending questions (``needs_input``) never reaches this
    node: it is paused at the HITL input node (ADR-0046).
    """

    model_role = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.2,
    }
    #: The answer-composer produces the user-facing reply, so it reports the
    #: model that produced it when asked "which model are you running".
    include_model_identity = True

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
        """Return the run outcome: ``success`` | ``failure``."""
        return "failure" if AnswerFromResults._is_failure(state) else "success"

    @override
    def _get_prompt_variables(self, state: KleaAgentState, ctx: Any) -> dict:
        """Format prompt with the outcome, goal, plan, artefacts and query.

        The failure reason is folded into an ``outcome_details`` block that is
        empty on success, so the prompt never carries an empty label (prompt
        conventions).  The
        ``plan_history_block`` is a context-free signal (counts only) that the
        final plan followed iterations/human review; the raw feedback is
        deliberately not included (see :meth:`PlanSchema.revision_summary`).

        ``artefacts`` is the session-scoped durable results store (ADR-0035), so
        the synthesis can build on earlier tasks' deliverables; it is the
        curated, addressable record, not the conversation transcript (memory
        stays off for this node).
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
            "plan_history_block": self._optional_section(
                "Plan history", state.plan.revision_summary()
            ),
            "discovery": state.discovery_persistent.render(),
            "artefacts": state.artefacts_text(),
            "observations": self._observations_text(state),
        }
        self.logger.debug(f"{variables = }")
        return variables

    def _outcome_details(self, state: KleaAgentState) -> str:
        """Return the failure detail block, or ``""`` on success.

        Only the failure reason renders, with its own label, so a successful
        run's prompt contains no empty ``Failure reason:`` line.
        """
        if self._outcome(state) == "failure":
            return f"Failure reason: {state.failure_reason}"
        return ""

    @override
    def _update_state(
        self, result: AnswerSchema, state: KleaAgentState, ctx: Any
    ) -> dict[str, Any]:
        """Write the final message and persist the task deliverable.

        On success the task's deliverable (a concise artefact: the goal plus the
        answer) is written to the session-scoped ``artefacts`` so a later task
        in the same session can build on it.  The full user-facing reply stays
        in ``message_for_user`` and ``messages`` (lossy continuity); the
        artefact is the concise, addressable record.  On failure nothing is
        persisted -- there is no deliverable yet.

        In both outcomes a bounded ``continuation_evidence`` record is written
        from the goal, plan and StepOutput summaries, so a later ``continue``
        intent can re-plan without repeating the investigation.
        """
        answer = result.answer.strip() or self._fallback_answer(state)
        update: dict[str, Any] = {"message_for_user": answer}
        if self._outcome(state) == "success":
            artefact = self._deliverable_artefact(state, answer)
            artefacts = dict(state.artefacts or {})
            # Re-inserting moves the id to the end of the dict, so insertion
            # order is write order: ``artefacts_text`` keeps the most recent
            # entries when the prompt block is bounded.  A superseded id
            # (same goal) therefore counts as newly written.
            artefacts.pop(artefact.id_, None)
            artefacts[artefact.id_] = artefact
            update["artefacts"] = artefacts
        artefact_ids = list(update.get("artefacts") or state.artefacts)
        update["continuation_evidence"] = state.build_continuation_evidence(
            answer, artefact_ids=artefact_ids
        )
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

        Reports the plan outcome and renders the plan itself, so the user sees
        what was attempted instead of a raw tool dump.  ``_get_default_error_result``
        already recovers raw prose; this is the last resort when no usable
        synthesis exists at all.
        """
        status = state.plan.status
        if self._outcome(state) == "failure":
            reason = state.failure_reason or "the task could not be completed"
            lead = (
                f"I could not complete this task: {reason} "
                f"The plan ended with status: {status.replace('_', ' ')}."
            )
        elif status == "completed":
            lead = "The task plan completed successfully."
        else:
            lead = f"The task plan ended with status: {status.replace('_', ' ')}."
        plan = state.plan.render()
        if plan and plan != "(no plan)":
            return f"{lead}\n\n{plan}"
        return lead

    @override
    def _get_inspect(
        self, state: KleaAgentState, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData:
        """Return the answer summary plus input prompt and raw/processed output."""
        assert ctx.prompt is not None
        assert ctx.output is not None
        assert ctx.result is not None
        result = ctx.result
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
                "input_prompt": prompt_value_to_messages(ctx.prompt),
                "unprocessed_output": extract_llm_output_content(ctx.output),
                "processed_output": str(ctx.result),
            },
        )

    @override
    def _get_default_error_result(self, ctx: Any) -> AnswerSchema:
        """Recover raw prose, or return an empty result for the plan fallback.

        The parser can reject a valid prose reply when the model ignores the
        requested JSON envelope.  This node's schema is a single free-text
        field, so the raw text is a usable answer; recover it rather than
        discarding it.  Blank output and broken JSON are not recovered --
        ``_update_state`` then falls back to the plan.
        """
        if ctx.output is not None:
            text = extract_llm_output_content(ctx.output).strip()
            if "</think>" in text:
                text = text.split("</think>", 1)[1].strip()
            if text and not text.lstrip().startswith(("{", "[")):
                self.logger.warning(
                    "Structured output could not be parsed; recovering raw prose"
                )
                return AnswerSchema(answer=text)
        return AnswerSchema()
