#!/usr/bin/env python3
"""
Reasoning node

See ``devdocs/system/agent-general-path-control-flow.md``.

File: klea_agent/nodes/reasoning.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import MAX_OUTPUT_TOKENS_CEILING, BaseLLMNode
from klea_utils.nodes.context import LLMNodeContext
from langchain_core.messages import AIMessage

from klea_agent.nodes.triage_router import current_step_key
from klea_agent.schemas import KleaAgentState, ReasoningSchema, StepOutput

#: Conclusion recorded when the model produced no usable reasoning output.
#: Non-empty so the base node's empty-result guard sees a meaningful result
#: and the Evaluator recognises an explicit failure, instead of a silent empty
#: conclusion it might mistake for progress.
NO_CONCLUSION_FALLBACK = (
    "Reasoning failed: the model produced no conclusion for this step."
)


class ReasoningNode(BaseLLMNode[KleaAgentState, ReasoningSchema]):
    """Produce a reasoning step's conclusion (ADR-0035 update 2026-09-19).

    A reasoning step is a plan step whose output is a judgement rather than an
    external observation (interpretation, decision, hypothesis, design,
    synthesis).  This node reads the goal, the plan, the current step, the
    observations so far and (when memory is enabled) the conversation history,
    and emits a general conclusion; the picker/caller are skipped.  The
    conclusion is recorded as a :class:`StepOutput` (a plain string, not a
    ``CallToolResult``) so later steps and the Planner see it in
    ``observations`` alongside tool results.  This is how a goal whose facts are
    already known in an earlier turn reaches the answer-composer.

    It never answers the user: the final reply is written by
    ``AnswerFromResults``.  The Evaluator judges the step afterwards, like any
    other step.
    """

    model_role = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.2,
        # Reasoning models spend a large part of the completion budget on
        # hidden reasoning tokens before emitting the conclusion.  Start at the
        # truncation ceiling instead of burning a retry after a 32k cut-off;
        # the provider resolver still clamps to the model/context limit.
        "max_output_tokens": MAX_OUTPUT_TOKENS_CEILING,
    }
    include_model_identity = True

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
    ):
        """Initialise the reasoning node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=ReasoningSchema,
            memory=memory,
        )

    @override
    def _pre_exec(self, state: KleaAgentState, ctx: LLMNodeContext[Any]) -> bool:
        """Run only when there is a current plan step to reason about."""
        return state.plan.current_step() is not None

    @override
    def _get_prompt_variables(self, state: KleaAgentState, ctx: Any) -> dict:
        """Format prompt with the goal, plan, current step and observations."""
        goal_text = state.goal.goal or "(none)"
        if state.goal.success_criteria:
            goal_text += f"\nSuccess criteria: {state.goal.success_criteria}"
        current = state.plan.current_step()
        step_feedback = ""
        if current is not None:
            evaluator_feedback = state.evaluation.step_incomplete_feedback(
                {current.step_number}
            )
            if evaluator_feedback:
                step_feedback = self._optional_section(
                    "Evaluator feedback for this step",
                    f"{evaluator_feedback}\n"
                    "Revise the conclusion so it satisfies the step's success "
                    "criteria; do not repeat the same conclusion unchanged.",
                )
        variables = {
            "query": state.query,
            "goal": goal_text,
            "plan": state.plan.render(),
            "current_step": current.render(current=True) if current else "(no step)",
            "step_feedback": step_feedback,
            "observations": state.observations_text(),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(
        self, result: ReasoningSchema, state: KleaAgentState, ctx: Any
    ) -> dict[str, Any]:
        """Record the conclusion as a step output and a concise message.

        The conclusion is stored as a :class:`StepOutput` with a ``str`` result
        (the reasoning form), against the current step's 1-based number, so
        ``observations_text`` renders it for later steps, the Evaluator and the
        Planner.  A short ``AIMessage`` records it in the run history.

        ``tool_calls``/``tool_results`` are cleared so the Evaluator (which
        renders ``executed_tools`` from ``tool_calls``) does not report the
        previous tool step's calls for this reasoning step.
        """
        step = current_step_key(state)
        conclusion = result.conclusion.strip()
        entry = StepOutput(
            result=conclusion,
            tool="",
            displayed=False,
            rationale=result.rationale.strip(),
        )
        outputs = dict(state.step_outputs or {})
        # Reasoning retries are bounded by the evaluator's step-attempt budget,
        # so the per-step list cannot grow without bound.
        outputs[step] = [*outputs.get(step, []), entry]
        message = f"Reasoning (step {step}): {conclusion}"
        if result.rationale.strip():
            message += f"\nRationale: {result.rationale.strip()}"
        self._emit_notice(f"step {step}: {conclusion or NO_CONCLUSION_FALLBACK}")
        update: dict[str, Any] = {
            "step_outputs": outputs,
            "tool_calls": [],
            "tool_results": [],
            "messages_internal": [*state.messages_internal, AIMessage(content=message)],
        }
        self.logger.debug(f"{step = }\n{conclusion = }\n{len(outputs[step]) = }")
        return update

    @override
    def _get_inspect(
        self, state: KleaAgentState, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData:
        """Return the conclusion summary plus prompt and raw/processed output."""
        assert ctx.prompt is not None
        assert ctx.output is not None
        assert ctx.result is not None
        result = ctx.result
        if isinstance(result, ReasoningSchema):
            summary = f"Conclusion ready ({len(result.conclusion)} chars)"
            details: dict[str, Any] = {
                "conclusion": result.conclusion,
                "rationale": result.rationale,
            }
        else:
            summary = "Reasoning"
            details = {}
        details.update(
            {
                "input_prompt": prompt_value_to_messages(ctx.prompt),
                "unprocessed_output": extract_llm_output_content(ctx.output),
                "processed_output": str(ctx.result),
            }
        )
        return NodeStreamData(heading="Reasoning", summary=summary, details=details)

    @override
    def _get_default_error_result(self, ctx: Any) -> ReasoningSchema:
        """Return an explicit failure conclusion so the step cannot pass silently."""
        return ReasoningSchema(conclusion=NO_CONCLUSION_FALLBACK)
