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

    def test_status_markers_words_for_prompts(self):
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

    def test_status_markers_symbols_for_status_pane(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, description="a", status="done"),
                StepSchema(step_number=2, description="b", status="failed"),
                StepSchema(step_number=3, description="c"),
                StepSchema(step_number=4, description="d"),
            ],
            current_step_index=2,
        )
        rendered = plan.render(markdown=True)
        self.assertIn("[x] Step 1: a", rendered)
        self.assertIn("[!] Step 2: b", rendered)
        self.assertIn("[*] Step 3: c", rendered)
        self.assertIn("[ ] Step 4: d", rendered)

    def test_success_criteria_included(self):
        plan = PlanSchema(
            step_list=[StepSchema(description="x", success_criteria="x done")],
            current_step_index=0,
        )
        self.assertIn("success criteria: x done", plan.render())

    def test_markdown_steps_separated_by_blank_line(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, description="a"),
                StepSchema(step_number=2, description="b"),
            ]
        )
        rendered = plan.render(markdown=True)
        self.assertEqual(len(rendered.split("\n\n")), 2)
        self.assertTrue(rendered.startswith("[*] Step 1: a"))

    def test_step_render(self):
        step = StepSchema(
            step_number=2,
            description="edit the file",
            success_criteria="tests pass",
        )
        self.assertEqual(
            step.render(current=True),
            (
                "[CURRENT] 2. edit the file (success criteria: tests pass; "
                "suggested tools: (none); depends on: (none))"
            ),
        )
        self.assertEqual(
            step.render(current=True, markdown=True),
            "[*] Step 2: edit the file (depends on: (none))",
        )

    def test_step_render_includes_tools_and_deps(self):
        step = StepSchema(
            step_number=2,
            description="edit the file",
            success_criteria="tests pass",
            suggested_tools=["edit", "write"],
            depends_on=[1],
        )
        rendered = step.render()
        self.assertIn("suggested tools: edit, write", rendered)
        self.assertIn("depends on: 1", rendered)

    def test_status_render_is_minimal(self):
        """The status render omits criteria/tools; keeps deps (execution order)."""
        step = StepSchema(
            step_number=2,
            description="edit the file",
            success_criteria="tests pass",
            suggested_tools=["edit", "write"],
            depends_on=[1],
        )
        rendered = step.render(current=True, markdown=True)
        self.assertEqual(rendered, "[*] Step 2: edit the file (depends on: 1)")
        self.assertNotIn("success criteria", rendered)
        self.assertNotIn("suggested tools", rendered)

    def test_step_render_done_marker(self):
        step = StepSchema(description="x", status="done")
        self.assertTrue(step.render().startswith("[DONE]"))

    def test_current_step_helper(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, description="a", status="done"),
                StepSchema(step_number=2, description="b"),
            ],
            current_step_index=1,
        )
        current = plan.current_step()
        assert current is not None
        self.assertEqual(current.description, "b")
        self.assertIsNone(PlanSchema().current_step())


if __name__ == "__main__":
    unittest.main()
