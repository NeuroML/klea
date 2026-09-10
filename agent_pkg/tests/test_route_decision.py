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
    """Routing writes: inline answer, one-step plan, or nothing."""

    def _node(self) -> RouteDecision:
        return RouteDecision(
            logger=logging.getLogger("test"),
            label="Deciding route",
            llm_models={"chat": object()},
        )

    def test_answer_writes_message_no_plan(self):
        update = self._node()._update_state(
            RouteSchema(route="answer", answer="hi"), KleaAgentState(query="q")
        )
        assert update["message_for_user"] == "hi"
        assert "plan" not in update

    def test_act_seeds_single_step_plan(self):
        """An ``act`` is modelled as a one-step plan for the shared picker."""
        update = self._node()._update_state(
            RouteSchema(route="act", rationale="one hop"),
            KleaAgentState(query="pwd"),
        )
        plan = update["plan"]
        assert plan.status == "in_progress"
        assert plan.current_step_index == 0
        assert len(plan.step_list) == 1
        assert plan.step_list[0].description == "pwd"
        assert plan.step_list[0].success_criteria
        assert "message_for_user" not in update

    def test_plan_route_leaves_plan_unset(self):
        update = self._node()._update_state(
            RouteSchema(route="plan"), KleaAgentState(query="q")
        )
        assert "plan" not in update
        assert "message_for_user" not in update
