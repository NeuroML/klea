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

from klea_agent.schemas import KleaAgentState, RouteSchema


class RouteDecision(BaseLLMNode[KleaAgentState, RouteSchema]):
    """Narrow entry router: ``chat`` or ``task`` (ADR-0035).

    ``chat`` is a self-contained general-conversation/knowledge request that
    the router answers inline (``answer``); the graph then goes straight to
    ``AnswerUser``.  ``task`` is anything that needs the current
    environment/workspace/session; the Planner plans and the tool loop runs.

    The decision is **fail-closed**: the default is ``task``, so the router
    answers inline only when it is clearly general conversation/knowledge.
    This is the one place where a model could answer a world-fact from
    assumption, so the prompt is deliberately narrow and tool-free.  It reads
    conversation history (memory) for follow-ups and continuity.
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
        memory: bool = False,
    ):
        """Initialise the route decision node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include recent conversation history; routing
            of follow-ups and chat continuity depend on context
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
        """Store the route; write the inline answer for ``chat``.

        For ``chat`` the reply is written to ``message_for_user`` and the graph
        routes to ``AnswerUser``.  For ``task`` nothing else is written and the
        Planner takes over.
        """
        state_update: dict[str, Any] = {"route": result}
        if result.route == "chat" and result.answer.strip():
            state_update["message_for_user"] = result.answer.strip()
        self.logger.debug(f"{state_update = }")
        return state_update

    @override
    def _get_inspect(self) -> NodeStreamData:
        """Return the routing decision plus prompt and raw/processed output."""
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        result = self._last_result
        details: dict[str, Any]
        if isinstance(result, RouteSchema):
            summary = f"Route: {result.route}"
            details = {"route": result.route}
            if result.route == "chat":
                details["answer_chars"] = len(result.answer)
        else:
            summary = "Route decision"
            details = {}
        details.update(
            {
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            }
        )
        return NodeStreamData(heading="Route", summary=summary, details=details)

    @override
    def _get_default_error_result(self) -> RouteSchema:
        """Default to ``task`` when routing fails (fail-closed)."""
        return RouteSchema(route="task")
