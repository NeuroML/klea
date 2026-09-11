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
from klea_agent.schemas import (
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlannerOutput,
    PlanSchema,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolInfo


class TestPlannerState(unittest.TestCase):
    """Planner state updates: inline answer, goal lock, plan, failure."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
        )

    def test_direct_answer_sets_not_needed(self):
        update = self._planner()._update_state(
            PlannerOutput(direct_answer="hello"), KleaAgentState(query="hi")
        )
        self.assertEqual(update["plan"].status, "not_needed")
        self.assertEqual(update["message_for_user"], "hello")

    def test_plan_writes_goal_and_in_progress(self):
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="g", success_criteria="c"),
                plan=PlanSchema(
                    step_list=[
                        StepSchema(
                            step_number=1,
                            description="read the model file",
                            success_criteria="file content is available",
                        )
                    ]
                ),
            ),
            KleaAgentState(query="q"),
        )
        self.assertEqual(update["goal"].goal, "g")
        self.assertEqual(update["plan"].status, "in_progress")
        self.assertEqual(
            update["plan"].step_list[0].success_criteria, "file content is available"
        )

    def test_goal_is_locked_once_set(self):
        """A replan cannot move the fixed goal (ADR-0035)."""
        state = KleaAgentState(goal=GoalSchema(goal="fixed", success_criteria="c"))
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="different"),
                plan=PlanSchema(step_list=[StepSchema(description="s")]),
            ),
            state,
        )
        self.assertNotIn("goal", update)
        self.assertEqual(update["plan"].status, "in_progress")

    def test_empty_plan_is_unplannable(self):
        update = self._planner()._update_state(PlannerOutput(), KleaAgentState())
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("failure_reason", update)

    def test_completed_steps_are_reapplied(self):
        """Steps already done in the old plan stay done in a fresh plan."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, status="done"),
                    StepSchema(step_number=2),
                ],
                status="in_progress",
                current_step_index=1,
            )
        )
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlanSchema(
                    step_list=[
                        StepSchema(step_number=1, description="a"),
                        StepSchema(step_number=2, description="b"),
                    ]
                )
            ),
            state,
        )
        plan = update["plan"]
        self.assertEqual(plan.step_list[0].status, "done")
        self.assertEqual(plan.step_list[1].status, "pending")
        self.assertEqual(plan.current_step_index, 1)

    def test_in_review_status_is_kept(self):
        """A plan the Planner flags for review keeps ``in_review``."""
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlanSchema(
                    step_list=[StepSchema(description="s")], status="in_review"
                ),
            ),
            KleaAgentState(query="q"),
        )
        self.assertEqual(update["plan"].status, "in_review")

    def test_human_feedback_is_consumed(self):
        """Review feedback is cleared once the Planner has read it."""
        state = KleaAgentState(
            human_feedback="looks good, proceed",
            plan=PlanSchema(
                step_list=[StepSchema(description="s")], status="in_review"
            ),
        )
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlanSchema(
                    step_list=[StepSchema(description="s")], status="in_progress"
                )
            ),
            state,
        )
        self.assertEqual(update["human_feedback"], "")
        self.assertEqual(update["plan"].status, "in_progress")

    def test_evaluation_feedback_is_exposed(self):
        """The evaluator's reason reaches the Planner on a replan."""
        state = KleaAgentState(
            evaluation=EvaluationSchema(next_step="need_replan", reason="no progress")
        )
        variables = self._planner()._get_prompt_variables(state)
        self.assertEqual(variables["evaluation_feedback"], "no progress")

    def test_plan_recorded_in_messages(self):
        update = self._planner()._update_state(
            PlannerOutput(plan=PlanSchema(step_list=[StepSchema(description="s")])),
            KleaAgentState(query="q"),
        )
        self.assertEqual(len(update["messages"]), 1)
        self.assertIn("Plan (in_progress)", update["messages"][0].content)

    def test_revision_budget_exhausted_is_unplannable(self):
        planner = Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
            max_plan_revisions=2,
        )
        update = planner._update_state(
            PlannerOutput(plan=PlanSchema(step_list=[StepSchema(description="s")])),
            KleaAgentState(plan_revisions=2),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("failure_reason", update)


class TestPlannerToolDisclosure(unittest.TestCase):
    """The planner consumes the compact (short) tool description."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
        )

    def test_tool_descriptions_prefer_short(self):
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
        planner = self._planner()
        planner.set_tools_info(
            {"code": {"read": ToolInfo(description="read -- full description")}}
        )
        self.assertEqual(planner._get_tool_descriptions(), "read -- full description")


if __name__ == "__main__":
    unittest.main()
