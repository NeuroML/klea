#!/usr/bin/env python3
"""
Tests for the agent Planner node.

File: tests/test_planner.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import unittest

from klea_agent.nodes.planner import Planner
from klea_agent.schemas import KleaAgentState, PlanSchema, StepSchema
from klea_utils.mcp.schemas import ToolInfo


class TestPlanner(unittest.TestCase):
    """Planner state updates and tiered tool disclosure."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
        )

    def test_update_state_does_not_write_goal(self):
        """The Planner is not a goal writer (ADR-0035: the goal is immutable)."""
        planner = self._planner()
        state = KleaAgentState()
        result = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="read the model file",
                    success_criteria="file content is available",
                )
            ]
        )
        update = planner._update_state(result, state)
        self.assertNotIn("goal", update)
        self.assertIn("plan", update)
        plan = update["plan"]
        self.assertEqual(plan.status, "in_progress")
        self.assertEqual(
            plan.step_list[0].success_criteria, "file content is available"
        )

    def test_empty_plan_marks_failed(self):
        """An empty plan is marked failed rather than left not_started."""
        update = self._planner()._update_state(PlanSchema(), KleaAgentState())
        self.assertEqual(update["plan"].status, "failed")

    def test_tool_descriptions_prefer_short(self):
        """The planner consumes the compact (short) tool description."""
        planner = self._planner()
        planner.set_tools_info(
            {
                "code": {
                    "read": ToolInfo(
                        description="read -- full with Parameters:",
                        short_description="read -- docstring only",
                    )
                }
            }
        )
        self.assertEqual(planner._get_tool_descriptions(), "read -- docstring only")

    def test_tool_descriptions_fall_back_to_full(self):
        """Falls back to the full description when no short form exists."""
        planner = self._planner()
        planner.set_tools_info(
            {"code": {"read": ToolInfo(description="read -- full description")}}
        )
        self.assertEqual(planner._get_tool_descriptions(), "read -- full description")


if __name__ == "__main__":
    unittest.main()
