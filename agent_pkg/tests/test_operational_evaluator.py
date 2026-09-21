#!/usr/bin/env python3
"""
Tests for the general operational evaluator node (ADR-0035/ADR-0041).

File: tests/test_operational_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import unittest
from typing import Literal

from klea_agent.nodes.operational_evaluator import OperationalEvaluator
from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    StepEvaluation,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolCallSchema


def _verdict(
    step: int,
    verdict: Literal["step_done", "step_incomplete", "need_replan"],
    reason: str = "",
) -> EvaluationSchema:
    """Build a single-step evaluation map."""
    return EvaluationSchema(
        evaluations={step: StepEvaluation(verdict=verdict, reason=reason)}
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
            )
        return state

    def test_step_done_advances_plan_without_answering(self):
        update = self._evaluator()._update_state(
            _verdict(1, "step_done", "ok"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        plan = update["plan"]
        self.assertEqual(plan.step_list[0].status, "done")
        self.assertEqual(plan.status, "in_progress")
        self.assertEqual(plan.current_step().step_number, 2)

    def test_plan_done_completes_plan_without_answering(self):
        """The Evaluator never writes ``message_for_user`` (that is AnswerFromResults)."""
        update = self._evaluator()._update_state(
            EvaluationSchema(overall="plan_done"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        plan = update["plan"]
        self.assertEqual(plan.status, "completed")
        self.assertTrue(all(step.status == "done" for step in plan.step_list))

    def test_need_replan_marks_step_failed_without_answering(self):
        update = self._evaluator()._update_state(
            _verdict(1, "need_replan"), self._state()
        )
        self.assertNotIn("message_for_user", update)
        self.assertEqual(update["plan"].step_list[0].status, "failed")

    def test_need_replan_sets_replan_reason(self):
        """The verdict reason is carried to the Planner via replan_reason."""
        update = self._evaluator()._update_state(
            _verdict(1, "need_replan", "cannot proceed"), self._state()
        )
        self.assertEqual(update["replan_reason"], "cannot proceed")

    def test_non_replan_verdict_clears_stale_replan_reason(self):
        """A non-replan verdict clears any stale replan reason."""
        state = self._state()
        state.replan_reason = "old failure"
        update = self._evaluator()._update_state(_verdict(1, "step_incomplete"), state)
        self.assertEqual(update["replan_reason"], "")

    def test_step_incomplete_keeps_step_pending(self):
        update = self._evaluator()._update_state(
            _verdict(1, "step_incomplete"), self._state()
        )
        self.assertEqual(update["evaluation"].evaluations[1].verdict, "step_incomplete")
        self.assertEqual(update["plan"].step_list[0].status, "pending")

    def test_plan_done_without_plan(self):
        update = self._evaluator()._update_state(
            EvaluationSchema(overall="plan_done"),
            self._state(with_plan=False),
        )
        self.assertNotIn("message_for_user", update)
        self.assertEqual(update["plan"].status, "completed")

    def test_final_step_done_completes_plan(self):
        """A ``step_done`` on the only step completes the plan."""
        evaluator = self._evaluator()
        state = KleaAgentState()
        state.plan = PlanSchema(
            step_list=[StepSchema(step_number=1, description="only step")],
        )
        update = evaluator._update_state(_verdict(1, "step_done", "looks done"), state)
        self.assertEqual(update["plan"].status, "completed")

    def test_empty_evaluation_escalates_to_replan(self):
        """A missing verdict (e.g. a failed LLM call) escalates deterministically."""
        evaluator = self._evaluator()
        update = evaluator._update_state(
            evaluator._get_default_error_result(), self._state()
        )
        self.assertEqual(update["evaluation"].evaluations[1].verdict, "need_replan")
        self.assertTrue(update["replan_reason"])

    def test_verdict_recorded_in_messages(self):
        update = self._evaluator()._update_state(
            _verdict(1, "need_replan", "no progress"), self._state()
        )
        self.assertIn("need_replan", update["messages"][-1].content)
        self.assertIn("no progress", update["messages"][-1].content)

    def test_step_incomplete_escalates_at_attempt_budget(self):
        """Repeated ``step_incomplete`` on a step escalates to a replan."""
        evaluator = self._evaluator()  # max_step_attempts=3
        state = self._state()
        state.step_attempt_counts = {1: 2}
        update = evaluator._update_state(
            _verdict(1, "step_incomplete", "still going"), state
        )
        self.assertEqual(update["evaluation"].evaluations[1].verdict, "need_replan")
        self.assertEqual(update["plan"].step_list[0].status, "failed")
        self.assertEqual(update["step_attempt_counts"][1], 3)

    def test_progress_clears_step_attempt_budget(self):
        update = self._evaluator()._update_state(
            _verdict(1, "step_done", "done"), self._state()
        )
        self.assertEqual(update["step_attempt_counts"], {})

    def test_tool_round_budget_aborts(self):
        """The global tool-round budget aborts a non-terminating run."""
        evaluator = self._evaluator()  # max_tool_rounds=8
        state = self._state()
        state.tool_rounds = 8
        update = evaluator._update_state(
            _verdict(1, "step_incomplete", "looping"), state
        )
        self.assertEqual(update["evaluation"].overall, "abort")
        self.assertEqual(update["plan"].status, "aborted")
        self.assertIn("tool-round budget exhausted", update["failure_reason"])

    def test_model_abort_uses_its_own_reason(self):
        """A model ``abort`` verdict reports the real cause, not a budget."""
        evaluator = self._evaluator()
        state = self._state()
        update = evaluator._update_state(
            EvaluationSchema(
                overall="abort",
                reason="input file does not exist and may not be created",
            ),
            state,
        )
        self.assertEqual(update["evaluation"].overall, "abort")
        self.assertEqual(update["plan"].status, "aborted")
        self.assertEqual(
            update["failure_reason"],
            "input file does not exist and may not be created",
        )

    def test_model_abort_without_reason_gets_a_default(self):
        """An empty abort reason still yields an explanation for the answer."""
        evaluator = self._evaluator()
        update = evaluator._update_state(
            EvaluationSchema(overall="abort", reason=""),
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

    def test_prompt_variables_group_executed_tools_by_step(self):
        """The flat call list is rendered grouped by originating step."""
        state = self._state()
        state.tool_calls = [
            ToolCallSchema(tool="list_files", step=1),
            ToolCallSchema(tool="read_file", step=1),
            ToolCallSchema(tool="write_file", step=2),
        ]
        variables = self._evaluator()._get_prompt_variables(state)
        self.assertEqual(
            variables["executed_tools"],
            "Step 1: list_files, read_file\nStep 2: write_file",
        )

    def test_status_refreshes_live_plan_section(self):
        """The Evaluator updates the Planner's plan section in place."""
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
