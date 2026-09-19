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
            EvaluationSchema(evaluation="step_done", reason="ok"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        plan = update["plan"]
        self.assertEqual(plan.current_step_index, 1)
        self.assertEqual(plan.step_list[0].status, "done")
        self.assertEqual(plan.status, "in_progress")

    def test_plan_done_completes_plan_without_answering(self):
        """The Evaluator never writes ``message_for_user`` (that is AnswerFromResults)."""
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="plan_done"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        plan = update["plan"]
        self.assertEqual(plan.status, "completed")
        self.assertEqual(plan.current_step_index, len(plan.step_list))

    def test_need_replan_marks_step_failed_without_answering(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="need_replan"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        self.assertEqual(update["plan"].step_list[0].status, "failed")

    def test_need_replan_sets_replan_reason(self):
        """The verdict reason is carried to the Planner via replan_reason."""
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="need_replan", reason="cannot proceed"),
            self._state(),
        )
        self.assertEqual(update["replan_reason"], "cannot proceed")

    def test_non_replan_verdict_clears_stale_replan_reason(self):
        """A non-replan verdict clears any stale replan reason."""
        state = self._state()
        state.replan_reason = "old failure"
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="step_incomplete"), state
        )
        self.assertEqual(update["replan_reason"], "")

    def test_step_incomplete_leaves_plan_unchanged(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="step_incomplete"), self._state()
        )
        self.assertNotIn("plan", update)
        self.assertEqual(update["evaluation"].evaluation, "step_incomplete")

    def test_plan_done_without_plan(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="plan_done"),
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
            EvaluationSchema(evaluation="step_done", reason="looks done"), state
        )
        self.assertEqual(update["evaluation"].evaluation, "plan_done")
        self.assertEqual(update["plan"].status, "completed")

    def test_default_error_result_replans(self):
        self.assertEqual(
            self._evaluator()._get_default_error_result().evaluation, "need_replan"
        )

    def test_verdict_recorded_in_messages(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="need_replan", reason="no progress"),
            self._state(),
        )
        self.assertIn("need_replan", update["messages"][-1].content)
        self.assertIn("no progress", update["messages"][-1].content)

    def test_step_incomplete_escalates_at_attempt_budget(self):
        """Repeated ``step_incomplete`` on a step escalates to a replan."""
        evaluator = self._evaluator()  # max_step_attempts=3
        state = self._state()
        state.step_attempt_counts = {1: 2}
        update = evaluator._update_state(
            EvaluationSchema(evaluation="step_incomplete", reason="still going"),
            state,
        )
        self.assertEqual(update["evaluation"].evaluation, "need_replan")
        self.assertEqual(update["plan"].step_list[0].status, "failed")
        self.assertEqual(update["step_attempt_counts"][1], 3)

    def test_progress_clears_step_attempt_budget(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(evaluation="step_done", reason="done"),
            self._state(),
        )
        self.assertEqual(update["step_attempt_counts"], {})

    def test_tool_round_budget_aborts(self):
        """The global tool-round budget aborts a non-terminating run."""
        evaluator = self._evaluator()  # max_tool_rounds=8
        state = self._state()
        state.tool_rounds = 8
        update = evaluator._update_state(
            EvaluationSchema(evaluation="step_incomplete", reason="looping"),
            state,
        )
        self.assertEqual(update["evaluation"].evaluation, "abort")
        self.assertEqual(update["plan"].status, "aborted")
        self.assertIn("tool-round budget exhausted", update["failure_reason"])

    def test_model_abort_uses_its_own_reason(self):
        """A model ``abort`` verdict reports the real cause, not a budget."""
        evaluator = self._evaluator()
        state = self._state()
        update = evaluator._update_state(
            EvaluationSchema(
                evaluation="abort",
                reason="input file does not exist and may not be created",
            ),
            state,
        )
        self.assertEqual(update["evaluation"].evaluation, "abort")
        self.assertEqual(update["plan"].status, "aborted")
        self.assertEqual(
            update["failure_reason"],
            "input file does not exist and may not be created",
        )

    def test_model_abort_without_reason_gets_a_default(self):
        """An empty abort reason still yields an explanation for the answer."""
        evaluator = self._evaluator()
        update = evaluator._update_state(
            EvaluationSchema(evaluation="abort", reason=""),
            self._state(),
        )
        self.assertEqual(update["failure_reason"], "the goal cannot be achieved")

    def test_prompt_variables_include_criteria(self):
        evaluator = self._evaluator()
        variables = evaluator._get_prompt_variables(self._state())
        self.assertIn("do x", variables["goal"])
        self.assertIn("x is done", variables["goal"])
        self.assertIn("step one", variables["plan"])
        self.assertIn("success criteria", variables["plan"])
        self.assertIn("[CURRENT]", variables["plan"])

    def test_prompt_variables_include_executed_tools_sentinel(self):
        """The declared ``executed_tools`` input is always rendered."""
        variables = self._evaluator()._get_prompt_variables(self._state())
        self.assertEqual(variables["executed_tools"], "(none)")

    def test_status_refreshes_live_plan_section(self):
        """The Evaluator updates the Planner's plan section in place.

        Both emit ``key="plan"`` so the status pane keeps exactly one live plan
        entry instead of a stale Planner copy plus a duplicate Evaluator copy.
        """
        evaluator = self._evaluator()
        evaluator._last_state = self._state()
        status = evaluator._get_status()
        assert status is not None
        self.assertEqual(status.heading, "Plan")
        self.assertEqual(status.key, "plan")
        self.assertTrue(status.preformatted)
        self.assertIn("2 step(s)", status.summary)


if __name__ == "__main__":
    unittest.main()
