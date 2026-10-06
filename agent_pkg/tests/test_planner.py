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
from klea_utils.nodes.context import LLMNodeContext


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
            LLMNodeContext(),
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
            LLMNodeContext(),
        )
        self.assertNotIn("goal", update)
        self.assertEqual(update["plan"].status, "in_progress")

    def test_empty_plan_is_unplannable(self):
        update = self._planner()._update_state(
            PlannerOutput(), KleaAgentState(), LLMNodeContext()
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("failure_reason", update)

    def test_model_plan_statuses_are_used_verbatim(self):
        """Design A: the Planner's per-step statuses are not mutated.

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
                )
            ),
            state,
            LLMNodeContext(),
        )
        plan = update["plan"]
        self.assertEqual(plan.step_list[0].status, "done")
        self.assertEqual(plan.step_list[1].status, "pending")
        self.assertEqual(plan.current_step().step_number, 2)

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
                )
            ),
            state,
            LLMNodeContext(),
        )
        plan = update["plan"]
        self.assertEqual(plan.step_list[0].status, "pending")
        self.assertEqual(plan.current_step().step_number, 1)

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
            )
        )
        error = planner._validate_result(output, KleaAgentState(), LLMNodeContext())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("depends on 2", error)

    def test_validate_result_rejects_forward_dependency(self):
        planner = self._planner()
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        suggested_tools=["read_file"],
                        depends_on=[2],
                    ),
                    StepSchema(step_number=2, suggested_tools=["read_file"]),
                ],
            )
        )
        error = planner._validate_result(output, KleaAgentState(), LLMNodeContext())
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("earlier", error)

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
            )
        )
        self.assertIsNone(
            planner._validate_result(output, KleaAgentState(), LLMNodeContext())
        )

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
            LLMNodeContext(),
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
            LLMNodeContext(),
        )
        self.assertEqual(update["human_feedback"], "")
        self.assertEqual(update["plan"].status, "in_progress")
        self.assertEqual(update["plan"].human_feedback_rounds, 1)

    def test_human_input_is_exposed_and_consumed(self):
        """needs_input answers reach the prompt, then are cleared and not
        counted as a review round."""
        state = KleaAgentState(
            human_input={1: ["models/cell.nml"]},
            plan=PlanSchema(
                status="needs_input",
                step_list=[StepSchema(step_number=1, needs_input=["which file?"])],
            ),
        )
        planner = self._planner()

        variables = planner._get_prompt_variables(state, LLMNodeContext())
        self.assertIn("## User input", variables["feedback_block"])
        self.assertIn("models/cell.nml", variables["feedback_block"])

        update = planner._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ],
                    status="in_progress",
                )
            ),
            state,
            LLMNodeContext(),
        )
        self.assertEqual(update["human_input"], {})
        self.assertEqual(update["plan"].human_feedback_rounds, 0)
        self.assertEqual(update["plan"].automated_plan_revisions, 0)

    def test_replan_reason_is_exposed(self):
        """The unified replan reason reaches the Planner on a replan."""
        state = KleaAgentState(replan_reason="no progress")
        variables = self._planner()._get_prompt_variables(state, LLMNodeContext())
        self.assertIn("## Replan reason", variables["feedback_block"])
        self.assertIn("no progress", variables["feedback_block"])

    def test_feedback_block_is_empty_by_default(self):
        """No conditional feedback means no feedback section at all."""
        variables = self._planner()._get_prompt_variables(
            KleaAgentState(), LLMNodeContext()
        )
        self.assertEqual(variables["feedback_block"], "")

    def test_discovery_is_rendered(self):
        """Project context (AGENTS.md) is rendered into the prompt variable."""
        state = KleaAgentState()
        state.discovery_persistent.upsert("AGENTS.md", "use uv")
        variables = self._planner()._get_prompt_variables(state, LLMNodeContext())
        self.assertIn("### AGENTS.md", variables["discovery"])
        self.assertIn("use uv", variables["discovery"])

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
            LLMNodeContext(),
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
            LLMNodeContext(),
        )
        self.assertEqual(len(update["messages"]), 1)
        self.assertIn("Plan (in_progress)", update["messages"][0].content)

    def test_revision_budget_exhausted_is_unplannable(self):
        planner = Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
            max_automated_plan_revisions=2,
        )
        update = planner._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(
                plan=PlanSchema(plan_version=3, automated_plan_revisions=2),
                replan_reason="tool failed",
            ),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["plan"].automated_plan_revisions, 3)
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
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].plan_version, 1)
        self.assertEqual(update["plan"].automated_plan_revisions, 0)

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
            KleaAgentState(
                human_feedback="looks good",
                plan=PlanSchema(
                    status="in_review", plan_version=1, automated_plan_revisions=3
                ),
            ),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].automated_plan_revisions, 0)
        # The review round is recorded on the plan (durable history signal).
        self.assertEqual(update["plan"].human_feedback_rounds, 1)
        self.assertEqual(update["plan"].plan_version, 2)

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
                replan_reason="tool failed",
                plan=PlanSchema(
                    status="in_progress", plan_version=1, automated_plan_revisions=1
                ),
            ),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].plan_version, 2)
        self.assertEqual(update["plan"].automated_plan_revisions, 2)

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
            KleaAgentState(
                plan=PlanSchema(plan_version=1, automated_plan_revisions=1),
                replan_reason="tool failed",
            ),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].automated_plan_revisions, 2)
        self.assertEqual(update["plan"].plan_version, 2)


class TestPlannerValidation(unittest.TestCase):
    """Kind/tool validation and the unplannable contract (ADR-0035 2026-09-19)."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
        )

    @staticmethod
    def _steps(count: int) -> list[StepSchema]:
        return [
            StepSchema(
                step_number=i,
                description=f"step {i}",
                suggested_tools=["read_file"],
            )
            for i in range(1, count + 1)
        ]

    def test_rejects_plan_over_step_limit(self):
        output = PlannerOutput(plan=PlannerPlanSchema(step_list=self._steps(31)))
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("at most 30", error)

    def test_accepts_plan_at_step_limit(self):
        output = PlannerOutput(plan=PlannerPlanSchema(step_list=self._steps(30)))
        self.assertIsNone(
            self._planner()._validate_result(output, KleaAgentState(), LLMNodeContext())
        )

    def test_oversize_plan_fails_closed(self):
        """A plan over the limit that survives retries fails as unplannable."""
        update = self._planner()._update_state(
            PlannerOutput(plan=PlannerPlanSchema(step_list=self._steps(31))),
            KleaAgentState(query="q"),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["plan"].step_list, [])
        self.assertIn("maximum of 30 steps", update["failure_reason"])

    def test_rejects_unplannable_with_steps(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                status="unplannable",
                step_list=[StepSchema(description="s", suggested_tools=["read_file"])],
            )
        )
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("unplannable", error)

    def test_accepts_blocked_step_as_draft(self):
        """A blocked step is exempt from the executable-step tool check."""
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                status="needs_input",
                step_list=[
                    StepSchema(description="deploy", needs_input=["which env?"])
                ],
            )
        )
        self.assertIsNone(
            self._planner()._validate_result(output, KleaAgentState(), LLMNodeContext())
        )

    def test_rejects_needs_input_without_questions(self):
        """needs_input with no blocked step is contradictory."""
        output = PlannerOutput(
            plan=PlannerPlanSchema(status="needs_input"), reason="which file?"
        )
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("no step has questions", error)

    def test_blocked_step_forces_needs_input_status(self):
        """A step with questions makes the plan a draft, whatever status."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[StepSchema(description="deploy", needs_input=["env?"])]
                )
            ),
            KleaAgentState(query="q"),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "needs_input")
        self.assertEqual(update["plan"].step_list[0].needs_input, ["env?"])
        self.assertNotIn("failure_reason", update)

    def test_multiple_questions_on_a_step_are_carried(self):
        """A step may carry several questions for one interrupt."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(
                            description="deploy",
                            needs_input=["which file?", "which mode?"],
                        )
                    ]
                )
            ),
            KleaAgentState(),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "needs_input")
        self.assertEqual(
            update["plan"].step_list[0].needs_input, ["which file?", "which mode?"]
        )

    def test_rejects_tool_step_without_tools(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(step_list=[StepSchema(description="s")])
        )
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
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
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("reasoning step but names tools", error)

    def test_accepts_reasoning_step_without_tools(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[StepSchema(description="analyse", kind="reasoning")]
            )
        )
        self.assertIsNone(
            self._planner()._validate_result(output, KleaAgentState(), LLMNodeContext())
        )

    def test_explicit_unplannable_uses_reason(self):
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(status="unplannable"),
                reason="required input file is missing",
            ),
            KleaAgentState(),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["failure_reason"], "required input file is missing")

    def test_empty_plan_with_runnable_status_is_coerced(self):
        """No steps but a runnable status -> deterministic unplannable."""
        update = self._planner()._update_state(
            PlannerOutput(plan=PlannerPlanSchema(status="in_progress")),
            KleaAgentState(),
            LLMNodeContext(),
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
            LLMNodeContext(),
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
            LLMNodeContext(),
        )
        self.assertNotIn("goal", update)


class TestPlannerToolDisclosure(unittest.TestCase):
    """The planner consumes the compact (short) tool description."""

    def _planner(self, tools_info=None) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
            tools_info=tools_info,
        )

    def test_tool_descriptions_prefer_short(self):
        planner = self._planner(
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
        planner = self._planner(
            {"code": {"read": ToolInfo(description="read -- full description")}}
        )
        self.assertEqual(
            planner._get_tool_descriptions(KleaAgentState()),
            "read -- full description",
        )

    def test_tool_descriptions_filtered_by_access_level(self):
        """read_only hides destructive and unannotated tools (ADR-0037)."""
        planner = self._planner(
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
        planner = self._planner(
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

        The incoming ``state`` on a turn's first Planner pass holds the empty
        plan ``InitGraphState`` reset.  The status section must render the plan
        from ``ctx.state_updates`` instead.
        """
        planner = self._planner()
        state = KleaAgentState()  # empty, default plan
        ctx = LLMNodeContext()
        ctx.state_updates = {
            "plan": PlanSchema(
                step_list=[
                    StepSchema(step_number=1, description="a"),
                    StepSchema(step_number=2, description="b"),
                ],
                status="in_progress",
            )
        }

        status = planner._get_status(state, ctx)

        assert status is not None
        self.assertIn("2 step(s)", status.summary)
        self.assertIn("[*] Step 1: a", status.display)
        self.assertNotEqual(status.display, "(no plan)")
        self.assertEqual(status.key, "plan")
        self.assertTrue(status.preformatted)

    def test_status_falls_back_to_state_plan(self):
        """Without state updates, status falls back to the incoming state plan."""
        planner = self._planner()
        state = KleaAgentState(
            plan=PlanSchema(step_list=[StepSchema(description="only")])
        )

        status = planner._get_status(state, LLMNodeContext())

        assert status is not None
        self.assertIn("1 step(s)", status.summary)


if __name__ == "__main__":
    unittest.main()
