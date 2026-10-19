#!/usr/bin/env python3
"""
Route decision node

See ``devdocs/system/agent-general-path-control-flow.md``; ADR-0035.

File: klea_agent/nodes/route_decision.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.nodes.context import LLMNodeContext

from klea_agent.schemas import KleaAgentState, RouteSchema


# NOTE: ``chat`` deliberately also covers direct conversational edits (for
# example "make that shorter" or "rephrase that"), so the router answers them
# inline without the planning/evaluation stages.  The boundary is derivation:
# a request that builds on, compiles, compares or synthesises earlier work or
# turns is ``task``.  If we observe failure modes where such a derivation is
# answered inline from the (lossy) conversation instead of routed to ``task``,
# revisit this line.
class RouteDecision(BaseLLMNode[KleaAgentState, RouteSchema]):
    """Narrow entry router: ``chat`` or ``task`` (ADR-0035).

    ``chat`` is a self-contained conversational/general-knowledge reply that
    the router answers inline (``answer``); the graph then goes straight to
    ``AnswerUser``.  ``task`` is anything that needs the current
    environment/workspace/session, **or** that derives a result from earlier
    work or turns (summarising, compiling, comparing, synthesising); the Planner
    plans and the tool loop runs (a synthesis needing no environment work is
    planned as a ``reasoning`` step).

    The decision is **fail-closed**: the default is ``task``, so the router
    answers inline only when it is clearly general conversation/knowledge.
    This is the one place where a model could answer a world-fact from
    assumption, so the prompt is deliberately narrow and tool-free.  It reads
    conversation history (memory) for follow-ups and continuity.

    The node is re-entered when the Planner returns ``plan.status == "chat"``:
    the Planner recognised that the first ``task`` call was a misrouted
    conversational turn.  In that forced pass the router must answer as chat
    (the prompt carries an override) and the router always returns the
    ``chat`` edge, so the run cannot bounce back to the Planner.
    """

    model_role = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.0,
    }
    #: The router answers chat inline, so it reports the model producing the
    #: reply when asked "which model are you running".
    include_model_identity = True
    #: On a forced chat re-entry, reject a result that is not a chat route with
    #: a non-empty answer and re-invoke with the reason exposed as
    #: ``validation_feedback``, so the override cannot be silently ignored.
    max_validation_retries: ClassVar[int] = 3

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
    def _get_prompt_variables(self, state: KleaAgentState, ctx: Any) -> dict:
        """Format prompt with the user query and any forced-chat override.

        On a Planner ``chat`` re-entry, *forced_chat_block* instructs the router
        that the request has already been classified as conversational, so it
        answers instead of deciding ``task`` again.
        """
        override = ""
        if state.plan.status == "chat":
            parts = [
                (
                    "The Planner has already examined this request and classified "
                    "it as `chat`: a conversational reply that needs the "
                    "conversation history, not the task path. Answer it now. Set "
                    "`route` to `chat` and put the reply in `answer`. Do not "
                    "choose `task`."
                )
            ]
            feedback = str(getattr(ctx, "validation_feedback", "")).strip()
            if feedback:
                parts.append(f"Previous attempt was rejected: {feedback}")
            override = "\n\n".join(parts)
        variables = {
            "query": state.query,
            "forced_chat_block": self._optional_section("Planner override", override),
        }
        self.logger.debug(f"{variables = }")
        return variables

    @override
    def _validate_result(
        self, result: Any, state: KleaAgentState, ctx: Any
    ) -> str | None:
        """Require a chat answer on a forced re-entry (deterministic check).

        The Planner has already decided the request is conversational, so the
        router must answer it.  If the model ignored the override (returned
        ``task``) or produced no reply, return an error to trigger a bounded
        re-invoke.  The normal entry pass is not validated: there the router may
        legitimately choose ``task``.
        """
        if state.plan.status != "chat":
            return None
        if not isinstance(result, RouteSchema):
            return "expected a route decision"
        if result.route != "chat":
            return (
                "the Planner classified this request as chat; set route to "
                "'chat' and put your conversational reply in 'answer'"
            )
        if not result.answer.strip():
            return "a chat reply is required; put the conversational answer in 'answer'"
        return None

    @override
    def _update_state(
        self, result: RouteSchema, state: KleaAgentState, ctx: Any
    ) -> dict[str, Any]:
        """Store the route; write the inline answer for ``chat``.

        For ``chat`` the reply is written to ``message_for_user`` and the graph
        routes to ``AnswerUser``.  For ``task`` nothing else is written and the
        Planner takes over.

        On a forced re-entry (``state.plan.status == "chat"``) the Planner has
        already decided the request is conversational, so the route is forced to
        ``chat`` even if the model ignored the override and emitted ``task``.
        An empty answer is left for ``AnswerUser``'s fallback.
        """
        if state.plan.status == "chat":
            answer = result.answer.strip()
            state_update: dict[str, Any] = {
                "route": RouteSchema(route="chat", answer=answer),
            }
            if answer:
                state_update["message_for_user"] = answer
            self.logger.debug(f"forced chat: {state_update = }")
            return state_update

        state_update = {"route": result}
        if result.route == "chat" and result.answer.strip():
            state_update["message_for_user"] = result.answer.strip()
        self.logger.debug(f"{state_update = }")
        return state_update

    @override
    def _get_inspect(
        self, state: KleaAgentState, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData:
        """Return the routing decision plus prompt and raw/processed output."""
        assert ctx.prompt is not None
        assert ctx.output is not None
        assert ctx.result is not None
        result = ctx.result
        details: dict[str, Any]
        if isinstance(result, RouteSchema):
            forced = state.plan.status == "chat"
            summary = f"Route: {result.route}"
            if forced:
                summary = "Route: chat (forced by Planner)"
            details = {"route": result.route}
            if forced:
                details["forced_chat"] = True
            if result.route == "chat":
                details["answer_chars"] = len(result.answer)
        else:
            summary = "Route decision"
            details = {}
        details.update(
            {
                "input_prompt": prompt_value_to_messages(ctx.prompt),
                "unprocessed_output": extract_llm_output_content(ctx.output),
                "processed_output": str(ctx.result),
            }
        )
        return NodeStreamData(heading="Route", summary=summary, details=details)

    @override
    def _get_default_error_result(self, ctx: Any) -> RouteSchema:
        """Default to ``task`` when routing fails (fail-closed)."""
        return RouteSchema(route="task")
