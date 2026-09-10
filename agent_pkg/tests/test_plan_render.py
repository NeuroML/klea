#!/usr/bin/env python3
"""
Tests for PlanSchema/StepSchema rendering.

File: tests/test_plan_render.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import unittest

from klea_agent.schemas import PlanSchema, StepSchema


class TestPlanRender(unittest.TestCase):
    """``PlanSchema.render`` text and markdown output."""

    def test_empty_plan(self):
        self.assertEqual(PlanSchema().render(), "(no plan)")

    def test_status_markers(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, description="a", status="done"),
                StepSchema(step_number=2, description="b", status="failed"),
                StepSchema(step_number=3, description="c"),
                StepSchema(step_number=4, description="d"),
            ],
            current_step_index=2,
        )
        rendered = plan.render()
        self.assertIn("[DONE] 1. a", rendered)
        self.assertIn("[FAILED] 2. b", rendered)
        self.assertIn("[CURRENT] 3. c", rendered)
        self.assertIn("[PENDING] 4. d", rendered)

    def test_success_criteria_included(self):
        plan = PlanSchema(
            step_list=[StepSchema(description="x", success_criteria="x done")],
            current_step_index=0,
        )
        self.assertIn("success criteria: x done", plan.render())

    def test_markdown_prefix(self):
        plan = PlanSchema(step_list=[StepSchema(description="x")])
        self.assertTrue(plan.render(markdown=True).startswith("- [CURRENT] 1. x"))


if __name__ == "__main__":
    unittest.main()
