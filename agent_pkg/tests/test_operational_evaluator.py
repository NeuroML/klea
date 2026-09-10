#!/usr/bin/env python3
"""
Tests for the general operational evaluator node (ADR-0035).

File: tests/test_operational_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import unittest

from fastmcp.client.client import CallToolResult
from klea_agent.nodes.operational_evaluator import OperationalEvaluator
from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    StepSchema,
)


class TestOperationalEvaluator(unittest.TestCase):
    """Verdict handling: plan advancement and answer synthesis."""

    def _evaluator(self) -> OperationalEvaluator:
        return OperationalEvaluator(
            logger=logging.getLogger("test"),
            label="Evaluating",
            llm_models={"chat": object()},
        )

    def _state(self, *, with_plan: bool = True) -> KleaAgentState:
        state = KleaAgentState()
        state.goal = GoalSchema(goal="do x", success_criteria="x is done")
        if with_plan:
            state.plan = PlanSchema(
                step_list=[
                    StepSchema(step_number=1, description="step one"),
                    StepSchema(step_number=2, description="step two"),
                ],
                current_step_index=0,
            )
        return state

    def test_step_done_advances_plan_without_answering(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="step_done", reason="ok"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        plan = update["plan"]
        self.assertEqual(plan.current_step_index, 1)
        self.assertEqual(plan.step_list[0].status, "done")
        self.assertEqual(plan.status, "in_progress")

    def test_plan_done_completes_plan_and_answers(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="plan_done", answer="all done"),
            self._state(),
        )
        self.assertEqual(update["message_for_user"], "all done")
        plan = update["plan"]
        self.assertEqual(plan.status, "completed")
        self.assertEqual(plan.current_step_index, len(plan.step_list))

    def test_need_replan_marks_step_failed_without_answering(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="need_replan"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        self.assertEqual(update["plan"].step_list[0].status, "failed")

    def test_step_incomplete_leaves_plan_unchanged(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="step_incomplete"), self._state()
        )
        self.assertNotIn("plan", update)
        self.assertEqual(update["evaluation"].next_step, "step_incomplete")

    def test_act_path_plan_done_answers_without_plan(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="plan_done", answer="hi"),
            self._state(with_plan=False),
        )
        self.assertEqual(update["message_for_user"], "hi")
        self.assertNotIn("plan", update)

    def test_step_done_on_final_step_coerces_to_plan_done(self):
        """A final-step ``step_done`` is treated as completion, with an answer."""
        evaluator = self._evaluator()
        state = KleaAgentState()
        state.plan = PlanSchema(
            step_list=[StepSchema(step_number=1, description="only step")],
            current_step_index=0,
        )
        update = evaluator._update_state(
            EvaluationSchema(next_step="step_done", reason="looks done"), state
        )
        self.assertEqual(update["evaluation"].next_step, "plan_done")
        self.assertEqual(update["plan"].status, "completed")
        self.assertTrue(update["message_for_user"])

    def test_final_step_fallback_uses_tool_results(self):
        """The fallback answer prefers the latest tool outputs."""
        evaluator = self._evaluator()
        state = KleaAgentState()
        state.plan = PlanSchema(
            step_list=[StepSchema(step_number=1, description="run pwd")],
            current_step_index=0,
        )
        state.tool_results = [
            CallToolResult(
                content=[], structured_content=None, meta=None, is_error=False
            )
        ]
        update = evaluator._update_state(EvaluationSchema(next_step="step_done"), state)
        self.assertTrue(update["message_for_user"])

    def test_default_error_result_replans(self):
        self.assertEqual(
            self._evaluator()._get_default_error_result().next_step, "need_replan"
        )

    def test_prompt_variables_include_criteria(self):
        evaluator = self._evaluator()
        variables = evaluator._get_prompt_variables(self._state())
        self.assertIn("do x", variables["goal"])
        self.assertIn("step one", variables["plan"])
        self.assertIn("success criteria", variables["current_step"])
        self.assertIn("[CURRENT]", variables["plan"])


if __name__ == "__main__":
    unittest.main()
