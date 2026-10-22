#!/usr/bin/env python3
"""
Tests for the agent RouteDecision node.

File: tests/test_route_decision.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_agent.nodes.route_decision import RouteDecision
from klea_agent.schemas import KleaAgentState, PlanSchema, RouteSchema
from klea_utils.llm import LLMModel
from klea_utils.nodes.context import LLMNodeContext


class TestRouteDecisionState:
    """Routing writes: inline chat answer, or task/continue only."""

    def _node(self) -> RouteDecision:
        return RouteDecision(
            logger=logging.getLogger("test"),
            label="Deciding route",
            llm_models={"chat": object()},
        )

    def test_chat_writes_message(self):
        update = self._node()._update_state(
            RouteSchema(route="chat", answer="hello"),
            KleaAgentState(query="hi"),
            LLMNodeContext(),
        )
        assert update["route"].route == "chat"
        assert update["message_for_user"] == "hello"

    def test_task_writes_no_message(self):
        update = self._node()._update_state(
            RouteSchema(route="task"),
            KleaAgentState(query="list files"),
            LLMNodeContext(),
        )
        assert update["route"].route == "task"
        assert "message_for_user" not in update

    def test_continue_writes_no_message(self):
        update = self._node()._update_state(
            RouteSchema(route="continue"),
            KleaAgentState(query="ok, implement"),
            LLMNodeContext(),
        )
        assert update["route"].route == "continue"
        assert "message_for_user" not in update

    def test_chat_without_answer_is_not_delivered(self):
        update = self._node()._update_state(
            RouteSchema(route="chat", answer="  "),
            KleaAgentState(query="hi"),
            LLMNodeContext(),
        )
        assert "message_for_user" not in update

    def test_default_error_result_is_continue(self):
        assert (
            self._node()._get_default_error_result(LLMNodeContext()).route == "continue"
        )

    def test_forced_chat_overrides_task_result(self):
        """A Planner chat re-entry forces the chat route and the answer."""
        state = KleaAgentState(query="what did you mean?")
        state.plan = PlanSchema(status="chat")
        update = self._node()._update_state(
            RouteSchema(route="task", answer="it means this"),
            state,
            LLMNodeContext(),
        )
        assert update["route"].route == "chat"
        assert update["message_for_user"] == "it means this"

    def test_forced_chat_prompt_carries_override(self):
        state = KleaAgentState(query="what did you mean?")
        state.plan = PlanSchema(status="chat")
        variables = self._node()._get_prompt_variables(state, LLMNodeContext())
        assert "Planner override" in variables["forced_chat_block"]
        assert "Do not choose `task`" in variables["forced_chat_block"]

    def test_normal_prompt_has_no_override(self):
        variables = self._node()._get_prompt_variables(
            KleaAgentState(query="hi"), LLMNodeContext()
        )
        assert variables["forced_chat_block"] == ""

    def _forced_state(self) -> KleaAgentState:
        state = KleaAgentState(query="what did you mean?")
        state.plan = PlanSchema(status="chat")
        return state

    def test_forced_chat_validation_rejects_task_route(self):
        error = self._node()._validate_result(
            RouteSchema(route="task"), self._forced_state(), LLMNodeContext()
        )
        assert error is not None
        assert "route to 'chat'" in error

    def test_forced_chat_validation_rejects_empty_answer(self):
        error = self._node()._validate_result(
            RouteSchema(route="chat", answer="  "),
            self._forced_state(),
            LLMNodeContext(),
        )
        assert error is not None
        assert "reply is required" in error

    def test_forced_chat_validation_accepts_chat_answer(self):
        assert (
            self._node()._validate_result(
                RouteSchema(route="chat", answer="it means this"),
                self._forced_state(),
                LLMNodeContext(),
            )
            is None
        )

    def test_normal_entry_is_not_validated(self):
        """A normal entry pass may legitimately choose task."""
        assert (
            self._node()._validate_result(
                RouteSchema(route="task"),
                KleaAgentState(query="list files"),
                LLMNodeContext(),
            )
            is None
        )

    def test_forced_chat_validation_feedback_in_prompt(self):
        ctx = LLMNodeContext()
        ctx.validation_feedback = "the Planner classified this request as chat"
        variables = self._node()._get_prompt_variables(self._forced_state(), ctx)
        assert "Previous attempt was rejected" in variables["forced_chat_block"]

    def test_system_prompt_reports_model_identity(self):
        """The inline-chat router can answer which model is replying."""
        node = RouteDecision(
            logger=logging.getLogger("test"),
            label="Deciding route",
            llm_models={"chat": LLMModel(instance=None, model_name="openai:gpt-4o")},
        )
        prompt = node._get_system_prompt(KleaAgentState(query="hi"), LLMNodeContext())
        assert isinstance(prompt, str)
        assert "Model in use: openai:gpt-4o" in prompt
