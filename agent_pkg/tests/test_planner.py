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
    GoalSchema,
    KleaAgentState,
    PlannerOutput,
    PlannerPlanSchema,
    PlanSchema,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolInfo


class TestPlannerState(unittest.TestCase):
    """Planner state updates: goal lock, plan, failure."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
        )

    def test_plan_writes_goal_and_in_progress(self):
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="g", success_criteria="c"),
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(
                            step_number=1,
                            description="read the model file",
                            success_criteria="file content is available",
                            suggested_tools=["read_file"],
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
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                ),
            ),
            state,
        )
        self.assertNotIn("goal", update)
        self.assertEqual(update["plan"].status, "in_progress")

    def test_empty_plan_is_unplannable(self):
        update = self._planner()._update_state(PlannerOutput(), KleaAgentState())
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("failure_reason", update)

    def test_model_plan_statuses_are_used_verbatim(self):
        """Design A: the Planner's per-step statuses/pointer are not mutated.

        Code no longer re-applies completion markers by matching step numbers
        across plans; the model returns the complete plan and owns its state.
        """
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
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(
                            step_number=1,
                            description="a",
                            suggested_tools=["read_file"],
                            status="done",
                        ),
                        StepSchema(
                            step_number=2,
                            description="b",
                            suggested_tools=["read_file"],
                        ),
                    ],
                    current_step_index=1,
                )
            ),
            state,
        )
        plan = update["plan"]
        self.assertEqual(plan.step_list[0].status, "done")
        self.assertEqual(plan.step_list[1].status, "pending")
        self.assertEqual(plan.current_step_index, 1)

    def test_renumbered_plan_is_not_force_marked_done(self):
        """A renumbered fresh plan keeps the model's pending status.

        Previously the old done step ``1`` collided with a renumbered new
        step ``1`` and was wrongly forced ``done`` (leaving no current step).
        """
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, status="done"),
                    StepSchema(step_number=2, status="done"),
                    StepSchema(step_number=3),
                ],
                status="in_progress",
                current_step_index=2,
            )
        )
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(
                            step_number=1,
                            description="new",
                            suggested_tools=["read_file"],
                        )
                    ],
                    current_step_index=0,
                )
            ),
            state,
        )
        plan = update["plan"]
        self.assertEqual(plan.step_list[0].status, "pending")
        self.assertEqual(plan.current_step_index, 0)

    def test_validate_result_rejects_dangling_dependency(self):
        planner = self._planner()
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        suggested_tools=["read_file"],
                        depends_on=[2],
                    )
                ],
                current_step_index=0,
            )
        )
        error = planner._validate_result(output, KleaAgentState())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("depends on 2", error)

    def test_validate_result_rejects_out_of_range_index(self):
        planner = self._planner()
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        suggested_tools=["read_file"],
                        status="done",
                    )
                ],
                current_step_index=1,
            )
        )
        error = planner._validate_result(output, KleaAgentState())
        # index == len is valid when all steps are done
        self.assertIsNone(error)

        output.plan.current_step_index = 5
        error = planner._validate_result(output, KleaAgentState())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("out of range", error)

    def test_validate_result_accepts_a_consistent_plan(self):
        planner = self._planner()
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        suggested_tools=["read_file"],
                        status="done",
                    ),
                    StepSchema(
                        step_number=2,
                        suggested_tools=["read_file"],
                        depends_on=[1],
                    ),
                ],
                current_step_index=1,
            )
        )
        self.assertIsNone(planner._validate_result(output, KleaAgentState()))

    def test_in_review_status_is_kept(self):
        """A plan the Planner flags for review keeps ``in_review``."""
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ],
                    status="in_review",
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
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ],
                    status="in_progress",
                )
            ),
            state,
        )
        self.assertEqual(update["human_feedback"], "")
        self.assertEqual(update["plan"].status, "in_progress")

    def test_replan_reason_is_exposed(self):
        """The unified replan reason reaches the Planner on a replan."""
        state = KleaAgentState(replan_reason="no progress")
        variables = self._planner()._get_prompt_variables(state)
        self.assertEqual(variables["replan_reason"], "no progress")

    def test_replan_reason_defaults_to_none(self):
        variables = self._planner()._get_prompt_variables(KleaAgentState())
        self.assertEqual(variables["replan_reason"], "(none)")

    def test_update_state_clears_replan_reason(self):
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(replan_reason="tool failed"),
        )
        self.assertEqual(update["replan_reason"], "")

    def test_plan_recorded_in_messages(self):
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
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
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(plan_revisions=2, replan_reason="tool failed"),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("failure_reason", update)

    def test_first_plan_does_not_count_as_revision(self):
        """The initial plan (entry status not_started) resets the counter."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(),
        )
        self.assertEqual(update["plan_revisions"], 0)

    def test_human_review_resets_revision_counter(self):
        """A review re-entry (entry status in_review) resets the counter."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(plan_revisions=3, plan=PlanSchema(status="in_review")),
        )
        self.assertEqual(update["plan_revisions"], 0)

    def test_automated_replan_increments_revision_counter(self):
        """A replan reason marks an automated replan and consumes the budget."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(
                plan_revisions=1,
                replan_reason="tool failed",
                plan=PlanSchema(status="in_progress"),
            ),
        )
        self.assertEqual(update["plan_revisions"], 2)

    def test_replan_reason_links_counter_without_in_progress_status(self):
        """A reason alone marks a replan, even if the plan status is stale."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(plan_revisions=1, replan_reason="tool failed"),
        )
        self.assertEqual(update["plan_revisions"], 2)


class TestPlannerValidation(unittest.TestCase):
    """Kind/tool validation and the unplannable contract (ADR-0035 2026-09-19)."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
        )

    def test_rejects_unplannable_with_steps(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                status="unplannable",
                step_list=[StepSchema(description="s", suggested_tools=["read_file"])],
            )
        )
        error = self._planner()._validate_result(output, KleaAgentState())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("unplannable", error)

    def test_rejects_tool_step_without_tools(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(step_list=[StepSchema(description="s")])
        )
        error = self._planner()._validate_result(output, KleaAgentState())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("names no tool", error)

    def test_rejects_reasoning_step_with_tools(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(
                        description="analyse",
                        kind="reasoning",
                        suggested_tools=["read_file"],
                    )
                ]
            )
        )
        error = self._planner()._validate_result(output, KleaAgentState())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("reasoning step but names tools", error)

    def test_accepts_reasoning_step_without_tools(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[StepSchema(description="analyse", kind="reasoning")]
            )
        )
        self.assertIsNone(self._planner()._validate_result(output, KleaAgentState()))

    def test_explicit_unplannable_uses_reason(self):
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(status="unplannable"),
                reason="required input file is missing",
            ),
            KleaAgentState(),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["failure_reason"], "required input file is missing")

    def test_empty_plan_with_runnable_status_is_coerced(self):
        """No steps but a runnable status -> deterministic unplannable."""
        update = self._planner()._update_state(
            PlannerOutput(plan=PlannerPlanSchema(status="in_progress")),
            KleaAgentState(),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("failure_reason", update)

    def test_reason_recorded_with_plan(self):
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                ),
                reason="single read step",
            ),
            KleaAgentState(query="q"),
        )
        content = update["messages"][0].content
        self.assertIn("Plan (in_progress)", content)
        self.assertIn("Reason: single read step", content)

    def test_unplannable_does_not_lock_goal(self):
        """A failed plan does not set the goal (handled before the goal lock)."""
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlannerPlanSchema(status="unplannable"),
                reason="impossible",
            ),
            KleaAgentState(),
        )
        self.assertNotIn("goal", update)


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
        self.assertEqual(
            planner._get_tool_descriptions(KleaAgentState()), "read -- docstring only"
        )

    def test_tool_descriptions_fall_back_to_full(self):
        planner = self._planner()
        planner.set_tools_info(
            {"code": {"read": ToolInfo(description="read -- full description")}}
        )
        self.assertEqual(
            planner._get_tool_descriptions(KleaAgentState()),
            "read -- full description",
        )

    def test_tool_descriptions_filtered_by_access_level(self):
        """read_only hides destructive and unannotated tools (ADR-0037)."""
        planner = self._planner()
        planner.set_tools_info(
            {
                "code": {
                    "read": ToolInfo(description="read tool", read_only=True),
                    "delete": ToolInfo(description="delete tool", destructive=True),
                    "plain": ToolInfo(description="plain tool"),
                }
            }
        )
        state = KleaAgentState(access_level="read_only")
        self.assertEqual(planner._get_tool_descriptions(state), "read tool")

    def test_tool_descriptions_full_includes_all(self):
        planner = self._planner()
        planner.set_tools_info(
            {
                "code": {
                    "read": ToolInfo(description="read tool", read_only=True),
                    "delete": ToolInfo(description="delete tool", destructive=True),
                }
            }
        )
        state = KleaAgentState(access_level="full")
        descriptions = planner._get_tool_descriptions(state)
        self.assertIn("read tool", descriptions)
        self.assertIn("delete tool", descriptions)

    def test_status_renders_the_plan_just_produced(self):
        """Status reads the new plan, not the pre-execution (empty) state.

        ``_last_state`` is captured at execution entry, so on a turn's first
        Planner pass it holds the empty plan ``InitGraphState`` reset.  The
        status section must render the plan from ``_last_state_updates``.
        """
        planner = self._planner()
        planner._last_state = KleaAgentState()  # empty, default plan
        planner._last_state_updates = {
            "plan": PlanSchema(
                step_list=[
                    StepSchema(step_number=1, description="a"),
                    StepSchema(step_number=2, description="b"),
                ],
                status="in_progress",
            )
        }

        status = planner._get_status()

        assert status is not None
        self.assertIn("2 step(s)", status.summary)
        self.assertIn("[*] Step 1: a", status.display)
        self.assertNotEqual(status.display, "(no plan)")
        self.assertEqual(status.key, "plan")
        self.assertTrue(status.preformatted)

    def test_status_falls_back_to_state_plan(self):
        """Without state updates, status falls back to ``_last_state.plan``."""
        planner = self._planner()
        planner._last_state = KleaAgentState(
            plan=PlanSchema(step_list=[StepSchema(description="only")])
        )
        planner._last_state_updates = {}

        status = planner._get_status()

        assert status is not None
        self.assertIn("1 step(s)", status.summary)


if __name__ == "__main__":
    unittest.main()
