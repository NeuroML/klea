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
from klea_utils.nodes.context import LLMNodeContext
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

    It resolves each judged step (``step_done`` / ``step_skipped`` /
    ``step_incomplete`` / ``need_replan``) and completes the plan only on
    ``plan_done`` with no pending step left (goal-authoritative, ADR-0035).
    Scientific mode uses a separate, independent, epistemic verifier instead
    of this node.
    """

    model_role = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.0,
    }
    #: One retry to correct an evaluation that conflicts with the plan's step
    #: statuses (see :meth:`_validate_result`) before failing closed.
    max_validation_retries: ClassVar[int] = 1

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
        max_step_attempts: int = 3,
    ):
        """Initialise the operational evaluator.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history
        :param max_step_attempts: Non-advancing (``step_incomplete``)
            evaluations allowed for one step before escalating to a replan
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=EvaluationSchema,
            memory=memory,
        )
        self.max_step_attempts = max_step_attempts

    def _observations_text(self, state: KleaAgentState) -> str:
        """Return the rendered per-step tool outputs (tool + displayed flag).

        Delegates to :meth:`KleaAgentState.observations_text` so the Evaluator,
        Planner and AnswerFromResults all see identical observations.
        """
        return state.observations_text()

    @override
    def _get_prompt_variables(self, state: KleaAgentState, ctx: Any) -> dict:
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
            "validation_feedback_block": self._optional_section(
                "Validation feedback", ctx.validation_feedback
            ),
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
    def _validate_result(
        self, result: EvaluationSchema, state: KleaAgentState, ctx: Any
    ) -> str | None:
        """Reject an evaluation that conflicts with the plan's step statuses.

        Completion is goal-authoritative (ADR-0035): ``plan_done`` asserts the
        goal is met and therefore requires every step to be resolved (done or
        skipped); an all-resolved plan with no ``overall`` means the goal was
        never judged.  Either inconsistency is retried once with the reason
        exposed to the prompt as ``validation_feedback``; if the model insists,
        :meth:`_update_state` fails closed.

        :returns: An error description, or ``None`` when consistent.
        """
        if not isinstance(result, EvaluationSchema):
            return None
        plan = state.plan
        by_number = {s.step_number: s for s in plan.step_list}

        errors: list[str] = []
        seen: set[int] = set()
        for verdict in result.evaluations:
            if verdict.step_number not in by_number:
                errors.append(f"step {verdict.step_number} is not in the plan")
            elif verdict.step_number in seen:
                errors.append(f"step {verdict.step_number} was judged twice")
            seen.add(verdict.step_number)

        verdicts = {v.step_number: v.verdict for v in result.evaluations}
        pending_after = [
            s.step_number
            for s in plan.step_list
            if s.status == "pending"
            and verdicts.get(s.step_number) not in ("step_done", "step_skipped")
        ]

        if result.overall == "plan_done":
            if pending_after:
                errors.append(
                    "plan_done requires every step to be done or skipped; "
                    f"still pending: {pending_after} - mark unneeded steps "
                    "step_skipped, or drop plan_done"
                )
            if any(v == "need_replan" for v in verdicts.values()):
                errors.append("plan_done contradicts a need_replan verdict")
        elif result.overall == "" and plan.step_list and not pending_after:
            errors.append(
                "all steps are resolved but no overall was set; judge the goal "
                "and set plan_done or abort"
            )
        return "; ".join(errors) if errors else None

    @override
    def _update_state(
        self, result: EvaluationSchema, state: KleaAgentState, ctx: Any
    ) -> dict[str, Any]:
        """Store the verdict and advance the plan (judge only).

        The Evaluator never generates the user-facing answer -- that is a
        separate synthesis stage (``AnswerFromResults``).  Verdicts resolve
        steps: ``step_done`` (met), ``step_skipped`` (not needed),
        ``need_replan`` (failed) and ``step_incomplete`` (still pending).
        ``plan_done`` completes the plan only when no pending step remains;
        otherwise the plan is sent back to the Planner (post-retry fallback).
        """
        update: dict[str, Any] = {"evaluation": result}
        plan = state.plan

        # An empty evaluation (for example the node's LLM call failed) must not
        # silently continue the loop: escalate deterministically to a replan for
        # the current step, or abort when there is none.
        if not result.evaluations and result.overall == "":
            current = plan.current_step()
            if current is not None:
                result.evaluations = [
                    StepEvaluation(
                        step_number=current.step_number,
                        verdict="need_replan",
                        reason="evaluation produced no verdict; replanning",
                    )
                ]
            else:
                result.overall = "abort"
                result.reason = "evaluation produced no verdict"

        # --- Per-step semantic budget (deterministic) --------------------
        # Repeated ``step_incomplete`` on the same step escalates that step to a
        # replan rather than looping.
        attempts = dict(state.step_attempt_counts or {})
        by_number = {s.step_number: s for s in plan.step_list}
        replan_reasons: list[str] = []
        for verdict in result.evaluations:
            number = verdict.step_number
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
            elif verdict.verdict == "step_skipped":
                step.status = "skipped"
            elif verdict.verdict == "need_replan":
                step.status = "failed"
                replan_reasons.append(verdict.reason or f"step {number} needs revision")
        update["step_attempt_counts"] = attempts
        self.logger.debug(
            f"evaluator verdicts\n"
            f"{ {v.step_number: v.verdict for v in result.evaluations} = }\n"
            f"{replan_reasons = }"
        )

        # --- Determine the plan's routing state (goal-authoritative) -----
        # Completion requires ``plan_done`` (the goal is met) with no pending
        # step left; an unneeded pending step must be judged ``step_skipped``.
        pending = [s for s in plan.step_list if s.status == "pending"]
        if result.overall == "abort":
            current = plan.current_step()
            if current is not None:
                current.status = "failed"
            plan.status = "aborted"
            # The model judged the goal unreachable; keep its reason so the
            # failure answer explains the real cause.
            update["failure_reason"] = result.reason or "the goal cannot be achieved"
            update["replan_reason"] = ""
        elif replan_reasons:
            plan.status = "in_progress"
            update["replan_reason"] = "; ".join(replan_reasons)
        elif result.overall == "plan_done":
            if not pending:
                plan.status = "completed"
                update["replan_reason"] = ""
            else:
                # Post-retry fallback: the model insists the goal is met but
                # left steps unresolved.  Do not force-close them; send the
                # plan back for revision instead.
                plan.status = "in_progress"
                numbers = ", ".join(str(s.step_number) for s in pending)
                update["replan_reason"] = (
                    f"plan_done with pending step(s) {numbers}; "
                    "resolve them or drop plan_done"
                )
        elif not pending:
            # Post-retry fallback: every step is resolved but the goal was
            # never judged.  The work is done, so complete to the answer.
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
            f"step {verdict.step_number} {verdict.verdict} -- {verdict.reason}"
            for verdict in result.evaluations
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
    def _get_inspect(
        self, state: KleaAgentState, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData:
        """Return the verdict summary plus prompt and raw/processed output."""
        assert ctx.prompt is not None
        assert ctx.output is not None
        assert ctx.result is not None
        result = ctx.result
        details: dict[str, Any]
        if isinstance(result, EvaluationSchema):
            parts = [f"step {v.step_number}: {v.verdict}" for v in result.evaluations]
            if result.overall:
                parts.append(f"overall: {result.overall}")
            summary = "Verdict: " + (", ".join(parts) or "(none)")
            details = {
                "evaluations": {
                    str(v.step_number): v.model_dump() for v in result.evaluations
                },
                "overall": result.overall,
                "reason": result.reason,
            }
        else:
            summary = "Evaluation"
            details = {}
        details.update(
            {
                "input_prompt": prompt_value_to_messages(ctx.prompt),
                "unprocessed_output": extract_llm_output_content(ctx.output),
                "processed_output": str(ctx.result),
            }
        )
        return NodeStreamData(
            heading="Evaluation",
            summary=summary,
            details=details,
        )

    @override
    def _get_status(
        self, state: KleaAgentState, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData | None:
        """Refresh the live plan section after evaluating a step.

        The plan section is shared (``key="plan"``) between the Planner and the
        Evaluator: the Planner creates it, and the Evaluator -- which sees the
        plan advanced after each tool round -- updates the same pane entry in
        place.  This keeps exactly one live plan section instead of a stale
        Planner copy plus a duplicate Evaluator copy.  The verdict itself is in
        the evaluator's ``inspect`` payload and the final answer.
        """
        plan = state.plan
        return NodeStreamData(
            heading="Plan",
            summary=f"{len(plan.step_list)} step(s); status={plan.status}",
            display=plan.render(markdown=True),
            key="plan",
            preformatted=True,
        )

    @override
    def _get_default_error_result(self, ctx: Any) -> EvaluationSchema:
        """Return an empty evaluation; ``_update_state`` escalates to replan."""
        return EvaluationSchema()
