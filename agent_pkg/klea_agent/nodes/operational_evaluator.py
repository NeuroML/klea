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
from klea_utils.nodes.tools_picker import MAX_BATCH_STEPS
from langchain_core.messages import AIMessage

from klea_agent.schemas import EvaluationSchema, KleaAgentState, StepEvaluation


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
        """Format prompt with goal, plan, per-step executed tools and observations."""
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        plan = state.plan
        batch_numbers = {step.step_number for step in plan.next_batch(MAX_BATCH_STEPS)}
        self.logger.debug(f"{batch_numbers = }")
        variables = {
            "query": state.query,
            "goal": goal_text,
            "plan": plan.render(current_numbers=batch_numbers),
            "executed_tools": self._executed_tools_text(state),
            "observations": self._observations_text(state),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @staticmethod
    def _executed_tools_text(state: KleaAgentState) -> str:
        """Render the latest batch's tools grouped by originating step.

        The calls carry their step (``ToolCallSchema.step``), so the flat
        ``state.tool_calls`` list is grouped back into per-step lines
        (ADR-0041).  Returns ``"(none)"`` when no tool ran (for example a
        reasoning-only batch).
        """
        by_step: dict[int, list[str]] = {}
        for call in state.tool_calls:
            by_step.setdefault(call.step, []).append(call.tool)
        return (
            "\n".join(
                f"Step {number}: {', '.join(tools)}"
                for number, tools in sorted(by_step.items())
            )
            or "(none)"
        )

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

        # An empty evaluation (for example the node's LLM call failed) must not
        # silently continue the loop: escalate deterministically to a replan for
        # the current step, or abort when there is none.
        if not result.evaluations and result.overall == "":
            current = plan.current_step()
            if current is not None:
                result.evaluations = {
                    current.step_number: StepEvaluation(
                        verdict="need_replan",
                        reason="evaluation produced no verdict; replanning",
                    )
                }
            else:
                result.overall = "abort"
                result.reason = "evaluation produced no verdict"

        # --- Per-step semantic budget (deterministic) --------------------
        # Repeated ``step_incomplete`` on the same step escalates that step to a
        # replan rather than looping.
        attempts = dict(state.step_attempt_counts or {})
        by_number = {s.step_number: s for s in plan.step_list}
        replan_reasons: list[str] = []
        for number, verdict in result.evaluations.items():
            if verdict.verdict == "step_incomplete":
                attempts[number] = attempts.get(number, 0) + 1
                if attempts[number] >= self.max_step_attempts:
                    self.logger.warning(
                        "Step %d not progressing after %d attempts; replanning",
                        number,
                        attempts[number],
                    )
                    verdict.verdict = "need_replan"
                    verdict.reason = verdict.reason or "step not progressing"
            else:
                attempts.pop(number, None)
            step = by_number.get(number)
            if step is None:
                continue
            if verdict.verdict == "step_done":
                step.status = "done"
            elif verdict.verdict == "need_replan":
                step.status = "failed"
                replan_reasons.append(verdict.reason or f"step {number} needs revision")
        update["step_attempt_counts"] = attempts
        self.logger.debug(
            f"evaluator verdicts\n"
            f"{ {n: v.verdict for n, v in result.evaluations.items()} = }\n"
            f"{replan_reasons = }"
        )

        # --- Global run budget (deterministic backstop) ------------------
        budget_abort = False
        if result.overall != "plan_done" and state.tool_rounds >= self.max_tool_rounds:
            self.logger.warning(
                "Tool-round budget (%d) exhausted; aborting",
                self.max_tool_rounds,
            )
            result.overall = "abort"
            budget_abort = True

        # --- Determine the plan's routing state --------------------------
        if result.overall == "abort":
            current = plan.current_step()
            if current is not None:
                current.status = "failed"
            plan.status = "aborted"
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
            update["replan_reason"] = ""
        elif result.overall == "plan_done":
            # The run is complete: mark every remaining step done so the
            # completed plan is coherent.
            for step in plan.step_list:
                if step.status == "pending":
                    step.status = "done"
            plan.status = "completed"
            update["replan_reason"] = ""
        elif replan_reasons:
            plan.status = "in_progress"
            update["replan_reason"] = "; ".join(replan_reasons)
        elif not any(step.status == "pending" for step in plan.step_list):
            plan.status = "completed"
            update["replan_reason"] = ""
        elif not plan.frontier():
            # Pending steps remain but none is runnable (for example a
            # dependency failed): escalate instead of stalling.
            plan.status = "in_progress"
            update["replan_reason"] = (
                "no runnable step remains; the plan needs revision"
            )
        else:
            plan.status = "in_progress"
            # Any non-replan outcome clears the reason: the Evaluator is the
            # last writer before the next action, so a stale failure cannot
            # leak into a later replan.
            update["replan_reason"] = ""

        update["plan"] = plan
        self.logger.debug(
            f"evaluator outcome\n{plan.status = }\n"
            f"{update.get('replan_reason') = }\n"
            f"{update.get('failure_reason') = }"
        )

        # Record the verdict in run history so a replan (and summarisation) can
        # see why the plan was sent back.
        summary = "; ".join(
            f"step {number} {verdict.verdict} -- {verdict.reason}"
            for number, verdict in result.evaluations.items()
        )
        if result.overall:
            summary = f"{summary}; overall {result.overall}".strip("; ")
        update["messages"] = [
            *state.messages,
            AIMessage(content=f"Evaluation: {summary or 'no verdict'}"),
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
            parts = [f"step {n}: {v.verdict}" for n, v in result.evaluations.items()]
            if result.overall:
                parts.append(f"overall: {result.overall}")
            summary = "Verdict: " + (", ".join(parts) or "(none)")
            details = {
                "evaluations": {
                    str(number): verdict.model_dump()
                    for number, verdict in result.evaluations.items()
                },
                "overall": result.overall,
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
        """Return an empty evaluation; ``_update_state`` escalates to replan."""
        return EvaluationSchema()
