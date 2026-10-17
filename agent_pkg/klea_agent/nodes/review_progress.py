#!/usr/bin/env python3
"""
Review progress node

Synthesises a human-facing digest of the work completed since the last review,
for presentation in the review interrupt.  See ADR-0051.

File: klea_agent/nodes/review_progress.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.nodes.context import LLMNodeContext
from pydantic import BaseModel

from klea_agent.schemas import KleaAgentState, StepSchema


class ReviewProgressSchema(BaseModel):
    # The review-progress node's structured output.  Prompt rules live in
    # ReviewProgress_system.md.
    summary: str = ""


class ReviewProgress(BaseLLMNode[KleaAgentState, ReviewProgressSchema]):
    """Synthesise the review ask's progress digest (ADR-0051).

    Generation is kept separate from judgement: the Evaluator judges steps
    against their criteria, while this node turns the work completed since the
    last review into a concise, human-facing digest for the approval gate.

    It calls no tools.  The evidence is whatever the plan's tool steps produced
    and recorded in ``step_outputs``, so the digest covers arbitrary tools
    (including third-party MCP servers), not just file edits.  A whole-plan
    approval (no work yet) short-circuits without an LLM call.
    """

    model_role = "chat"
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
        """Initialise the review-progress node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from
            ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history.  The
            digest is about this plan's recorded work, so memory is off by
            default.
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=ReviewProgressSchema,
            memory=memory,
        )

    @override
    def _pre_exec(self, state: KleaAgentState, ctx: Any) -> bool:
        """Run only when there is work since the last review.

        A whole-plan approval runs before any step, so there is nothing to
        summarize; skipping keeps that path free and leaves the ask carrying
        only the plan.  The transient slot is cleared when a review is resolved
        (approve or revise), so a skipped run never shows a stale digest.
        """
        has_work = bool(state.work_since_review())
        self.logger.debug(f"{has_work = }")
        return has_work

    @staticmethod
    def _review_step(state: KleaAgentState) -> StepSchema | None:
        """Return the review gate the digest is for, if any."""
        gates = state.plan.review_frontier()
        return gates[0] if gates else None

    @override
    def _get_prompt_variables(self, state: KleaAgentState, ctx: Any) -> dict:
        """Format the prompt with the goal, gate and recorded work.

        ``review_step`` names what the user is deciding on; the observations are
        scoped to work since the last review by
        :meth:`KleaAgentState.work_since_review_text`.
        """
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        gate = self._review_step(state)
        review_step = (
            f"Step {gate.step_number}: {gate.description}"
            if gate is not None
            else "(none)"
        )
        variables = {
            "query": state.query,
            "goal": goal_text,
            "review_step": review_step,
            "progress_observations": state.work_since_review_text(),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(
        self, result: ReviewProgressSchema, state: KleaAgentState, ctx: Any
    ) -> dict[str, Any]:
        """Write the digest into the transient ``review_progress`` slot."""
        summary = result.summary.strip()
        self.logger.debug(f"{summary = }")
        return {"review_progress": summary}

    @override
    def _get_inspect(
        self, state: KleaAgentState, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData:
        """Return the digest summary plus prompt and raw/processed output."""
        assert ctx.prompt is not None
        assert ctx.output is not None
        assert ctx.result is not None
        result = ctx.result
        if isinstance(result, ReviewProgressSchema):
            summary = f"Progress digest ready ({len(result.summary)} chars)"
            char_count = len(result.summary)
        else:
            summary = "Progress digest"
            char_count = 0
        return NodeStreamData(
            heading="Review progress",
            summary=summary,
            details={
                "char_count": char_count,
                "input_prompt": prompt_value_to_messages(ctx.prompt),
                "unprocessed_output": extract_llm_output_content(ctx.output),
                "processed_output": str(ctx.result),
            },
        )

    @override
    def _get_default_error_result(self, ctx: Any) -> ReviewProgressSchema:
        """Return an empty digest; the gate still fires with the plan alone."""
        return ReviewProgressSchema()
