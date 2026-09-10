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

from klea_agent.schemas import KleaAgentState, PlanSchema, RouteSchema, StepSchema


class RouteDecision(BaseLLMNode[KleaAgentState, RouteSchema]):
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
    def _get_prompt_variables(self, state: KleaAgentState) -> dict:
        """Format prompt with the user query."""
        variables = {"query": state.query}
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _update_state(
        self, result: RouteSchema, state: KleaAgentState
    ) -> dict[str, Any]:
        """Store the route; answer inline or seed a one-step plan.

        * ``answer``: write ``message_for_user`` directly.
        * ``act``: model the action as a **one-step plan** so the shared
          ``ToolsPicker`` always receives a concrete step to pick tools for
          (ADR-0035).  This is a clean separation of concerns -- the picker
          only ever picks tools for a step -- and involves no Planner call.
        * ``plan``: leave the plan to :class:`GoalSetter`/:class:`Planner`.
        """
        state_update: dict[str, Any] = {"route": result}
        if result.route == "answer":
            state_update["message_for_user"] = result.answer
        elif result.route == "act":
            state_update["plan"] = PlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        description=state.query,
                        success_criteria="the user's request is satisfied",
                    )
                ],
                status="in_progress",
                current_step_index=0,
            )
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
