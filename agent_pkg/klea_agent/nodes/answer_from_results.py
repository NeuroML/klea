#!/usr/bin/env python3
"""
Answer-from-results node

File: klea_agent/nodes/answer_from_results.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.tools import textualize_tool_results
from pydantic import BaseModel

from klea_agent.schemas import KleaAgentState


class AnswerSchema(BaseModel):
    """Structured output of the answer-synthesis node."""

    answer: str = ""


class AnswerFromResults(BaseLLMNode[KleaAgentState, AnswerSchema]):
    """Synthesise the final user-facing reply (ADR-0035).

    Separate from the Evaluator by design: evaluation judges, this node
    generates.  It runs once at the end of a run -- on success (``plan_done``)
    or on failure (``abort``/``unplannable``/``failure_reason``) -- and turns
    the goal, the plan and the observations into a reply.  On failure it
    explains concisely what was attempted and why it could not be completed.
    In scientific mode a grounded/cited variant replaces it; the Evaluator
    contract (judge only) stays the same.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.2,
        "max_output_tokens": 4096,
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
        """Return per-step tool outputs as readable text.

        ``step_outputs`` accumulates every batch for each step, so the latest
        batch is already included; there is no separate latest-batch block.
        """
        parts = []
        for step_index, results in state.step_outputs.items():
            if results:
                parts.append(f"Step {step_index}:\n{textualize_tool_results(results)}")
        return "\n\n".join(parts) if parts else "(no observations)"

    @staticmethod
    def _is_failure(state: KleaAgentState) -> bool:
        """Return True when the run ended in failure rather than success."""
        return bool(state.failure_reason) or state.plan.status in (
            "aborted",
            "unplannable",
        )

    @override
    def _get_prompt_variables(self, state: KleaAgentState) -> dict:
        """Format prompt with the outcome, goal, plan, observations and query."""
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        failure = self._is_failure(state)
        variables = {
            "query": state.query,
            "outcome": "failure" if failure else "success",
            "failure_reason": state.failure_reason or "(none)",
            "goal": goal_text,
            "plan": state.plan.render(),
            "observations": self._observations_text(state),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(
        self, result: AnswerSchema, state: KleaAgentState
    ) -> dict[str, Any]:
        """Write the final message, with a deterministic fallback if empty."""
        answer = result.answer.strip() or self._fallback_answer(state)
        self.logger.debug(f"{answer = }")
        return {"message_for_user": answer}

    def _fallback_answer(self, state: KleaAgentState) -> str:
        """Return a non-empty answer when synthesis produced nothing.

        On failure, reports that the task could not be completed and why.
        On success, prefers the latest tool outputs (which are often the
        answer, e.g. a command's output), then the completed step description.
        """
        if self._is_failure(state):
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
    def _get_info(self) -> NodeStreamData:
        """Return a short summary of the produced answer."""
        assert self._last_result is not None
        result = self._last_result
        if isinstance(result, AnswerSchema):
            summary = f"Answer ready ({len(result.answer)} chars)"
            details = {"char_count": len(result.answer)}
        else:
            summary = "Answer ready"
            details = {}
        return NodeStreamData(heading="Answer", summary=summary, details=details)

    @override
    def _get_debug(self) -> NodeStreamData:
        """Return info + input prompt and raw/processed output."""
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        info = self._get_info()
        details = info.details.copy()
        details.update(
            {
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            }
        )
        return NodeStreamData(
            heading=info.heading, summary=info.summary, details=details
        )

    @override
    def _get_default_error_result(self) -> AnswerSchema:
        """Return an empty result; ``_update_state`` falls back deterministically."""
        return AnswerSchema()
