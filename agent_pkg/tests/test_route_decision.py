#!/usr/bin/env python3
"""
Tests for the agent RouteDecision node.

File: tests/test_route_decision.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_agent.nodes.route_decision import RouteDecision
from klea_agent.schemas import KleaAgentState, RouteSchema


class TestRouteDecisionState:
    """Routing writes: inline chat answer, or task only."""

    def _node(self) -> RouteDecision:
        return RouteDecision(
            logger=logging.getLogger("test"),
            label="Deciding route",
            llm_models={"chat": object()},
        )

    def test_chat_writes_message(self):
        update = self._node()._update_state(
            RouteSchema(route="chat", answer="hello"), KleaAgentState(query="hi")
        )
        assert update["route"].route == "chat"
        assert update["message_for_user"] == "hello"

    def test_task_writes_no_message(self):
        update = self._node()._update_state(
            RouteSchema(route="task"), KleaAgentState(query="list files")
        )
        assert update["route"].route == "task"
        assert "message_for_user" not in update

    def test_chat_without_answer_is_not_delivered(self):
        update = self._node()._update_state(
            RouteSchema(route="chat", answer="  "), KleaAgentState(query="hi")
        )
        assert "message_for_user" not in update

    def test_default_error_result_is_task(self):
        assert self._node()._get_default_error_result().route == "task"
