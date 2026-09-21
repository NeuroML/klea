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
from langchain_core.messages import AIMessage

from klea_agent.nodes.triage_router import current_step_key
from klea_agent.schemas import EvaluationSchema, KleaAgentState


class OperationalEvaluator(BaseLLMNode[KleaAgentState, EvaluationSchema]):
    """General-mode evaluator: operational, not epistemic (ADR-0035).

    Runs after every Act batch and judges the goal and the current step (or,
    with no plan, the request directly) against its success criteria.  It emits
    an explicit ``evaluation`` verdict and advances the plan -- but it is
    **judge-only**: it never generates the user-facing answer, which is a
    separate synthesis stage (``AnswerFromResults``).  Keeping judgement and
    generation apart lets the same judge contract serve as the independent
    scientific verifier (ADR-0029 invariant 7).

    It advances the plan on ``step_done`` / ``plan_done`` and marks the current
    step ``failed`` on ``need_replan``.  Scientific mode uses a separate,
    independent, epistemic verifier instead of this node.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.0,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
        max_step_attempts: int = 3,
        max_tool_rounds: int = 8,
    ):
        """Initialise the operational evaluator.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history
        :param max_step_attempts: Non-advancing (``step_incomplete``)
            evaluations allowed for one step before escalating to a replan
        :param max_tool_rounds: ToolsPicker -> ToolsCaller dispatch rounds
            allowed in one run before the evaluator aborts (global backstop; a
            round may contain several parallel tool calls)
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=EvaluationSchema,
            memory=memory,
        )
        self.max_step_attempts = max_step_attempts
        self.max_tool_rounds = max_tool_rounds

    def _observations_text(self, state: KleaAgentState) -> str:
        """Return the rendered per-step tool outputs (tool + displayed flag).

        Delegates to :meth:`KleaAgentState.observations_text` so the Evaluator,
        Planner and AnswerFromResults all see identical observations.
        """
        return state.observations_text()

    @override
    def _get_prompt_variables(self, state: KleaAgentState) -> dict:
        """Format prompt with goal, plan, current step and observations."""
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        plan = state.plan
        executed = ", ".join(call.tool for call in state.tool_calls) or "(none)"
        variables = {
            "query": state.query,
            "goal": goal_text,
            "plan": plan.render(),
            "executed_tools": executed,
            "observations": self._observations_text(state),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(
        self, result: EvaluationSchema, state: KleaAgentState
    ) -> dict[str, Any]:
        """Store the verdict and advance the plan (judge only).

        The Evaluator never generates the user-facing answer -- that is a
        separate synthesis stage (``AnswerFromResults``).  ``step_done``
        advances to the next step; ``plan_done`` completes the plan;
        ``need_replan`` marks the current step failed; ``step_incomplete``
        leaves the plan unchanged.
        """
        update: dict[str, Any] = {"evaluation": result}
        plan = state.plan
        evaluation = result.evaluation
        current = plan.current_step()

        # Robustness: a model may report ``step_done`` on the last remaining
        # pending step ("criterion met, more steps remain") even though none
        # remain.  Coerce to completion so the graph does not route back to the
        # picker past the end of the plan.
        if evaluation == "step_done" and current is not None:
            other_pending = any(
                s.status == "pending" and s.step_number != current.step_number
                for s in plan.step_list
            )
            if not other_pending:
                self.logger.debug(
                    "step_done on the last pending step; coercing to plan_done"
                )
                result.evaluation = "plan_done"
                evaluation = "plan_done"

        # --- Per-step semantic budget (deterministic) --------------------
        # Count non-advancing evaluations; repeated ``step_incomplete`` on the
        # same step escalates to a replan rather than looping.  The key is the
        # plan step's 1-based number (see ``current_step_key``), matching the
        # step identity used for outputs and tool-retry counters.
        step = current_step_key(state)
        attempts = dict(state.step_attempt_counts or {})
        if evaluation == "step_incomplete":
            attempts[step] = attempts.get(step, 0) + 1
            if attempts[step] >= self.max_step_attempts:
                self.logger.warning(
                    "Step %d not progressing after %d attempts; replanning",
                    step,
                    attempts[step],
                )
                evaluation = "need_replan"
                result.evaluation = "need_replan"
        else:
            attempts.pop(step, None)
        update["step_attempt_counts"] = attempts

        # --- Global run budget (deterministic backstop) ------------------
        budget_abort = False
        if evaluation != "plan_done" and state.tool_rounds >= self.max_tool_rounds:
            self.logger.warning(
                "Tool-round budget (%d) exhausted; aborting",
                self.max_tool_rounds,
            )
            evaluation = "abort"
            result.evaluation = "abort"
            budget_abort = True

        if plan.step_list:
            if evaluation == "step_done":
                if current is not None:
                    current.status = "done"
                if not any(s.status == "pending" for s in plan.step_list):
                    plan.status = "completed"
                    evaluation = "plan_done"
                    result.evaluation = "plan_done"
                elif not plan.frontier():
                    # Pending steps remain but none is runnable (for example a
                    # dependency failed): escalate instead of stalling.
                    plan.status = "in_progress"
                    evaluation = "need_replan"
                    result.evaluation = "need_replan"
                    result.reason = "no runnable step remains; the plan needs revision"
                else:
                    plan.status = "in_progress"
                update["plan"] = plan
            elif evaluation == "plan_done":
                # The run is complete: mark every remaining step done so the
                # completed plan is coherent.
                for step_item in plan.step_list:
                    if step_item.status == "pending":
                        step_item.status = "done"
                plan.status = "completed"
                update["plan"] = plan
            elif evaluation == "need_replan":
                if current is not None:
                    current.status = "failed"
                update["plan"] = plan

        if evaluation == "abort":
            if current is not None:
                current.status = "failed"
            plan.status = "aborted"
            update["plan"] = plan
            if budget_abort:
                update["failure_reason"] = (
                    f"tool-round budget exhausted: {result.reason}"
                    if result.reason
                    else "tool-round budget exhausted"
                )
            else:
                # The model judged the goal unreachable; keep its reason so the
                # failure answer explains the real cause, not a budget.
                update["failure_reason"] = (
                    result.reason or "the goal cannot be achieved"
                )

        if evaluation == "need_replan":
            # Carry the reason to the Planner through the unified replan-reason
            # field (ADR-0035 update 2026-09-19); the Planner clears it.
            update["replan_reason"] = result.reason or "the plan needs revision"
        else:
            # Any non-replan verdict clears the reason: the Evaluator is the
            # last writer before the next action, so a stale failure cannot
            # leak into a later replan (and a reasoning step, which never runs
            # the tool-round recorder, is covered too).
            update["replan_reason"] = ""

        # Record the verdict in run history so a replan (and summarisation)
        # can see why the plan was sent back.
        update["messages"] = [
            *state.messages,
            AIMessage(content=f"Evaluation: {evaluation} -- {result.reason}"),
        ]
        self.logger.debug(f"{update = }")
        return update

    @override
    def _get_inspect(self) -> NodeStreamData:
        """Return the verdict summary plus prompt and raw/processed output."""
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        result = self._last_result
        details: dict[str, Any]
        if isinstance(result, EvaluationSchema):
            summary = f"Verdict: {result.evaluation}"
            details = {
                "evaluation": result.evaluation,
                "reason": result.reason,
            }
        else:
            summary = "Evaluation"
            details = {}
        details.update(
            {
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            }
        )
        return NodeStreamData(
            heading="Evaluation",
            summary=summary,
            details=details,
        )

    @override
    def _get_status(self) -> NodeStreamData | None:
        """Refresh the live plan section after evaluating a step.

        The plan section is shared (``key="plan"``) between the Planner and the
        Evaluator: the Planner creates it, and the Evaluator -- which sees the
        plan advanced after each tool round -- updates the same pane entry in
        place.  This keeps exactly one live plan section instead of a stale
        Planner copy plus a duplicate Evaluator copy.  The verdict itself is in
        the evaluator's ``inspect`` payload and the final answer.
        """
        state = self._last_state
        if state is None:
            return None
        plan = state.plan
        return NodeStreamData(
            heading="Plan",
            summary=f"{len(plan.step_list)} step(s); status={plan.status}",
            display=plan.render(markdown=True),
            key="plan",
            preformatted=True,
        )

    @override
    def _get_default_error_result(self) -> EvaluationSchema:
        """Escalate to the Planner when evaluation fails (never claim done)."""
        return EvaluationSchema(
            evaluation="need_replan",
            reason="evaluation failed; escalating to replan",
        )
