#!/usr/bin/env python3
"""
Route decision node

File: klea_agent/nodes/route_decision.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from pydantic import BaseModel

from klea_agent.schemas import RouteSchema


class RouteDecision(BaseLLMNode[RouteSchema]):
    """Upfront routing node: ``answer | act | plan`` (ADR-0035).

    Cheap first hop.  The route is judged from the request and the recent
    conversation, not from tool schemas: ``answer`` needs no environment,
    ``act`` is one independent hop, and ``plan`` has dependent steps,
    discovery, or a verifiable end state.  For ``answer`` the node answers
    inline (``RouteSchema.answer``), so trivial chat costs Guard plus this one
    call.  Misrouting is recoverable: an ``act`` that turns out complex
    escalates to the Planner via the triage/evaluator.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.0,
        "max_output_tokens": 1024,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = True,
    ):
        """Initialise the route decision node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history; routing
            of follow-up requests ("now do the same for X") depends on context
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=RouteSchema,
            memory=memory,
        )

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with the user query."""
        variables = {"query": getattr(state, "query", "")}
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(self, result: RouteSchema, state: BaseModel) -> dict[str, Any]:
        """Store the route and answer inline when the route is ``answer``.

        For ``act``/``plan`` no ``message_for_user`` is written; the answer is
        left to the Evaluator/answer step.
        """
        state_update: dict[str, Any] = {"route": result}
        if result.route == "answer":
            state_update["message_for_user"] = result.answer
        self.logger.debug(f"{state_update = }")
        return state_update

    @override
    def _get_info(self) -> NodeStreamData:
        """Return the routing decision for the inspector."""
        assert self._last_result is not None
        result = self._last_result
        if isinstance(result, RouteSchema):
            summary = f"Route: {result.route}"
            details = {
                "route": result.route,
                "rationale": result.rationale,
            }
            if result.route == "answer":
                details["answer_chars"] = len(result.answer)
        else:
            summary = "Route decision"
            details: dict[str, Any] = {}
        return NodeStreamData(
            heading="Route",
            summary=summary,
            details=details,
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
    def _get_default_error_result(self) -> RouteSchema:
        """Route to ``plan`` when routing fails: the conservative choice.

        ``answer`` risks returning an empty reply and ``act`` risks acting on a
        misunderstanding; ``plan`` forces the goal/planner stage instead.
        """
        return RouteSchema(
            route="plan",
            rationale="routing failed; defaulting to plan",
        )
