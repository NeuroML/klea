#!/usr/bin/env python3
"""
Tests for the general operational evaluator node (ADR-0035).

File: tests/test_operational_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import unittest

from klea_agent.nodes.operational_evaluator import OperationalEvaluator
from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    StepSchema,
)


class TestOperationalEvaluator(unittest.TestCase):
    """Verdict handling: plan advancement only (judge, never generate)."""

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

    def test_plan_done_completes_plan_without_answering(self):
        """The Evaluator never writes ``message_for_user`` (that is AnswerFromResults)."""
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="plan_done"), self._state()
        )
        self.assertNotIn("message_for_user", update)
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

    def test_plan_done_without_plan(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="plan_done"),
            self._state(with_plan=False),
        )
        self.assertNotIn("message_for_user", update)
        self.assertNotIn("plan", update)

    def test_step_done_on_final_step_coerces_to_plan_done(self):
        """A final-step ``step_done`` is treated as completion."""
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

    def test_default_error_result_replans(self):
        self.assertEqual(
            self._evaluator()._get_default_error_result().next_step, "need_replan"
        )

    def test_verdict_recorded_in_messages(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(next_step="need_replan", reason="no progress"),
            self._state(),
        )
        self.assertIn("need_replan", update["messages"][-1].content)
        self.assertIn("no progress", update["messages"][-1].content)

    def test_prompt_variables_include_criteria(self):
        evaluator = self._evaluator()
        variables = evaluator._get_prompt_variables(self._state())
        self.assertIn("do x", variables["goal"])
        self.assertIn("step one", variables["plan"])
        self.assertIn("success criteria", variables["current_step"])
        self.assertIn("[CURRENT]", variables["plan"])


if __name__ == "__main__":
    unittest.main()
