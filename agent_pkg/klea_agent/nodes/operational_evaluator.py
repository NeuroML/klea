#!/usr/bin/env python3
"""
Operational evaluator node

File: klea_agent/nodes/operational_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.tools import textualize_tool_results

from klea_agent.schemas import EvaluationSchema, KleaAgentState


class OperationalEvaluator(BaseLLMNode[KleaAgentState, EvaluationSchema]):
    """General-mode evaluator: operational, not epistemic (ADR-0035).

    Runs after every Act batch and judges the goal and the current step (or,
    on the planless ``act`` path, the request directly) against its success
    criteria.  It emits an explicit ``next_step`` verdict and, when the task is
    done (``plan_done``), also writes the user-facing answer, so it doubles as
    answer synthesis and is not an extra call over a separate answer node.

    It advances the plan on ``step_done`` / ``plan_done`` and marks the current
    step ``failed`` on ``need_replan``.  Scientific mode uses a separate,
    independent, epistemic verifier instead of this node.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.0,
        "max_output_tokens": 2048,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
    ):
        """Initialise the operational evaluator.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=EvaluationSchema,
            memory=memory,
        )

    def _observations_text(self, state: KleaAgentState) -> str:
        """Return per-step and latest-batch tool outputs as readable text."""
        parts = []
        for step_index, results in state.step_outputs.items():
            if results:
                parts.append(f"Step {step_index}:\n{textualize_tool_results(results)}")
        if state.tool_results:
            parts.append(
                f"Latest batch:\n{textualize_tool_results(state.tool_results)}"
            )
        return "\n\n".join(parts) if parts else "(no observations yet)"

    @override
    def _get_prompt_variables(self, state: KleaAgentState) -> dict:
        """Format prompt with goal, plan, current step and observations."""
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        plan = state.plan
        current = plan.current_step()
        variables = {
            "query": state.query,
            "goal": goal_text,
            "plan": plan.render(),
            "current_step": current.render(current=True) if current else "(no plan)",
            "observations": self._observations_text(state),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(
        self, result: EvaluationSchema, state: KleaAgentState
    ) -> dict[str, Any]:
        """Store the verdict, advance the plan, and answer when done.

        ``step_done`` advances to the next step; ``plan_done`` completes the
        plan and writes ``message_for_user``; ``need_replan`` marks the current
        step failed; ``step_incomplete`` leaves the plan unchanged.
        """
        update: dict[str, Any] = {"evaluation": result}
        plan = state.plan
        next_step = result.next_step

        # Robustness: a model may report the *final* step as ``step_done``
        # ("criterion met, more steps remain") even though no steps remain.
        # Coerce to completion so the graph does not route back to the picker
        # past the end of the plan, and ensure a non-empty answer.
        if (
            next_step == "step_done"
            and plan.step_list
            and plan.current_step_index >= len(plan.step_list) - 1
        ):
            self.logger.debug("step_done on the final step; coercing to plan_done")
            result.next_step = "plan_done"
            next_step = "plan_done"
            if not result.answer:
                result.answer = self._fallback_answer(state)

        if plan.step_list:
            index = plan.current_step_index
            if next_step == "step_done" and 0 <= index < len(plan.step_list):
                plan.step_list[index].status = "done"
                plan.current_step_index = index + 1
                plan.status = "in_progress"
                update["plan"] = plan
            elif next_step == "plan_done":
                if 0 <= index < len(plan.step_list):
                    plan.step_list[index].status = "done"
                plan.current_step_index = len(plan.step_list)
                plan.status = "completed"
                update["plan"] = plan
            elif next_step == "need_replan" and 0 <= index < len(plan.step_list):
                plan.step_list[index].status = "failed"
                update["plan"] = plan

        if next_step == "plan_done":
            update["message_for_user"] = result.answer
        self.logger.debug(f"{update = }")
        return update

    def _fallback_answer(self, state: KleaAgentState) -> str:
        """Return a non-empty fallback answer when none was produced.

        Only used when the model reports the final step done without an answer;
        prefers the latest tool outputs (which often *are* the answer, e.g. a
        command's output), then the step description.
        """
        if state.tool_results:
            return textualize_tool_results(state.tool_results)
        current = state.plan.current_step()
        if current and current.description:
            return f"Completed: {current.description}"
        return "Done."

    @override
    def _get_info(self) -> NodeStreamData:
        """Return the evaluation verdict for the inspector."""
        assert self._last_result is not None
        result = self._last_result
        if isinstance(result, EvaluationSchema):
            summary = f"Verdict: {result.next_step}"
            details: dict[str, Any] = {
                "next_step": result.next_step,
                "reason": result.reason,
            }
            if result.next_step == "plan_done":
                details["answer_chars"] = len(result.answer)
        else:
            summary = "Evaluation"
            details = {}
        return NodeStreamData(
            heading="Evaluation",
            summary=summary,
            details=details,
        )

    @override
    def _get_status(self) -> NodeStreamData | None:
        """Expose the current plan to the status pane (markdown)."""
        state = self._last_state
        if state is None:
            return None
        plan = state.plan
        return NodeStreamData(
            heading="Plan",
            summary=f"{len(plan.step_list)} step(s); status={plan.status}",
            display=plan.render(markdown=True),
        )

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
    def _get_default_error_result(self) -> EvaluationSchema:
        """Escalate to the Planner when evaluation fails (never claim done)."""
        return EvaluationSchema(
            next_step="need_replan",
            reason="evaluation failed; escalating to replan",
        )
