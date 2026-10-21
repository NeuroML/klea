#!/usr/bin/env python3
"""
Tool-call binder for the agent's planned steps.

The Planner chooses the tool identity; this node binds the arguments for one
contiguous group of plan steps that share the same suggested-tool set.  The
caller dispatches the accumulated calls in parallel.

See ``devdocs/adr/0035-general-operational-agent-path.md`` and
``devdocs/adr/0041-plan-step-granularity-and-parallelism.md``.

File: klea_agent/nodes/tool_call_binder.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from collections.abc import Callable
from typing import Any, ClassVar, override

from klea_utils.mcp.schemas import ToolInfo
from klea_utils.nodes.tools_picker import ToolsPicker
from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ToolCallBinder(ToolsPicker):
    """Bind arguments for the Planner's chosen tools.

    This is the agent's plan-aware ``ToolsPicker``: it processes one
    contiguous group of steps with an identical suggested-tool set per
    invocation, exposes only those tools, and never substitutes another tool.
    ``no_tool`` is returned when the suggested tools cannot carry out a step,
    so the Planner can revise the plan.

    RAG keeps the shared :class:`ToolsPicker` and its full domain tool set.
    """

    feedback_key: ClassVar[str] = "step_feedback"
    feedback_title: ClassVar[str] = "Feedback for the current step"

    @override
    def _feedback_body(self, state: BaseModel, ctx: Any) -> str:
        """Combine evaluator step feedback with the picker's call feedback.

        The evaluator's ``step_incomplete`` reason explains why the current
        step did not meet its criteria; the picker body adds mechanical
        failures (rejected selection, failed calls, empty picks).  Both are
        presented in one conditional block in the binder prompt.
        """
        parts: list[str] = []
        steps = self._binding_group(state) or self._batch_steps(state)
        step_numbers = {
            number
            for step in steps
            if (number := int(getattr(step, "step_number", 0) or 0)) > 0
        }
        evaluation = getattr(state, "evaluation", None)
        if evaluation is not None and step_numbers:
            evaluator_feedback = evaluation.step_incomplete_feedback(step_numbers)
            if evaluator_feedback:
                parts.append(
                    f"{evaluator_feedback}\n"
                    "Use this to bind a call that advances the step; do not "
                    "repeat the previous call unchanged."
                )
        picker_feedback = super()._feedback_body(state, ctx)
        if picker_feedback:
            parts.append(picker_feedback)
        return "\n\n".join(parts)

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        tools_info: dict[str, dict[str, ToolInfo]] | None = None,
        on_unusable: Callable[[Any, str], dict[str, Any]] | None = None,
    ):
        """Initialise the binder.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param tools_info: Per-domain tool metadata (``BaseLangGraph.tools_info``)
        :param on_unusable: Callback invoked with a deliberate ``no_tool``
            failure so the agent can record it and replan.
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            tools_info=tools_info,
            model_role="tool_picker",
            prompt_prefix="ToolCallBinder",
            on_unusable=on_unusable,
            bind_planned_steps=True,
        )
