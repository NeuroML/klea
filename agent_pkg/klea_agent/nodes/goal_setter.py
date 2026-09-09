#!/usr/bin/env python3
"""
Goal setter node

File: klea_agent/nodes/goal_setter.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from pydantic import BaseModel

from klea_agent.schemas import GoalSchema


class GoalSetter(BaseLLMNode[GoalSchema]):
    """Goal setter node — derives a static session goal from the user query.

    ``memory=False`` by design: the goal is set once per session and the
    plan may evolve, but the goal itself stays static.  This mirrors the
    discussion that each session has one goal.
    """

    model_type = "plan"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.01,
        "max_output_tokens": 2048,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        output_schema: type[GoalSchema],
        memory: bool = False,
    ):
        """Initialise the goal setter node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param output_schema: Pydantic schema for structured output
        :param memory: Whether to append memory content to the system prompt
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=output_schema,
            memory=memory,
        )

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with state-specific parameters"""
        variables = {"query": getattr(state, "query", "")}
        self.logger.debug(f"{variables =}")
        return variables

    @override
    def _update_state(self, result: GoalSchema, state: BaseModel) -> dict[str, Any]:
        """Update and return state dictionary"""
        state_update = {"goal": result, "message_for_user": result.goal}
        self.logger.debug(f"{state_update =}")
        return state_update

    @override
    def _get_info(self) -> NodeStreamData:
        """Return goal summary for the inspector info pane."""
        assert self._last_state_updates is not None
        assert self._last_result is not None
        result = self._last_result
        if isinstance(result, GoalSchema):
            summary = f"Goal: {result.goal[:80]}" if result.goal else "Goal set"
            details = {
                "goal": result.goal,
                "success_criteria": result.success_criteria,
            }
        else:
            summary = "Goal set"
            details = {}
        return NodeStreamData(
            heading="Goal Definition",
            summary=summary,
            details=details,
        )

    @override
    def _get_debug(self) -> NodeStreamData:
        """Return info + input prompt and raw/processed output."""
        assert self._last_state is not None
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
    def _get_default_error_result(self) -> GoalSchema:
        """Return default result when processing fails"""
        assert self.output_schema is not None
        return self.output_schema(goal="Invalid", success_criteria="Invalid")
