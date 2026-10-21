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
    PlannerPlanSchema,
    PlanRevision,
    PlanSchema,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo
from klea_utils.nodes.context import LLMNodeContext


class TestPlannerState(unittest.TestCase):
    """Planner state updates: goal lock, plan, failure."""

    def _planner(self) -> Planner:
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
            tools_info={"code": {"read_file": ToolInfo(read_only=True)}},
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

    def test_chat_writes_status_without_goal_or_messages(self):
        """A chat status is a route signal, not a task plan (ADR-0035)."""
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="ignored"),
                plan=PlannerPlanSchema(status="chat"),
                reason="this is a conversational follow-up",
            ),
            KleaAgentState(query="what did you mean by that?"),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "chat")
        self.assertEqual(update["plan"].step_list, [])
        self.assertNotIn("goal", update)
        self.assertNotIn("failure_reason", update)
        self.assertNotIn("messages", update)

    def test_chat_with_steps_fails_closed(self):
        """A contradictory chat+steps result must not silently drop work."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    status="chat",
                    step_list=[
                        StepSchema(description="do work", suggested_tools=["read_file"])
                    ],
                )
            ),
            KleaAgentState(query="q"),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "unplannable")
        self.assertIn("chat status with steps", update["failure_reason"])

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

    def test_review_step_keeps_in_progress(self):
        """A plan reviewed via a review step stays runnable (ADR-0051)."""
        update = self._planner()._update_state(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="Review the plan", kind="review"),
                        StepSchema(
                            step_number=2,
                            description="s",
                            suggested_tools=["read_file"],
                            depends_on=[1],
                        ),
                    ],
                    status="in_progress",
                ),
            ),
            KleaAgentState(query="q"),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "in_progress")
        self.assertEqual(update["plan"].step_list[0].kind, "review")
        self.assertEqual(update["plan"].step_list[0].description, "Review the plan")

    def test_review_step_must_describe_and_name_no_tools(self):
        """A review step needs a description and must not name tools."""
        errors = self._planner()._validate_result(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlannerPlanSchema(
                    step_list=[StepSchema(kind="review", suggested_tools=["read_file"])]
                ),
            ),
            KleaAgentState(),
            LLMNodeContext(),
        )
        self.assertIsNotNone(errors)
        self.assertIn("review step", errors or "")

    def test_review_step_must_gate_pending_work(self):
        """A trailing review (no pending work after it) is rejected."""
        errors = self._planner()._validate_result(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(
                            step_number=1,
                            description="fetch",
                            suggested_tools=["read_file"],
                            status="done",
                        ),
                        StepSchema(
                            step_number=2,
                            description="Review the plan",
                            kind="review",
                            depends_on=[1],
                        ),
                    ],
                    status="in_progress",
                ),
            ),
            KleaAgentState(),
            LLMNodeContext(),
        )
        self.assertIsNotNone(errors)
        self.assertIn("no pending work after it", errors or "")

    def test_review_step_with_work_after_is_accepted(self):
        """A review that gates a pending step is valid."""
        error = self._planner()._validate_result(
            PlannerOutput(
                goal=GoalSchema(goal="g"),
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(
                            step_number=1,
                            description="Review the plan",
                            kind="review",
                        ),
                        StepSchema(
                            step_number=2,
                            description="s",
                            suggested_tools=["read_file"],
                            depends_on=[1],
                        ),
                    ],
                    status="in_progress",
                ),
            ),
            KleaAgentState(),
            LLMNodeContext(),
        )
        self.assertIsNone(error)

    def test_human_feedback_is_consumed(self):
        """Review feedback is cleared once the Planner has read it."""
        state = KleaAgentState(
            human_feedback="looks good, proceed",
            plan=PlanSchema(
                step_list=[StepSchema(description="s")], status="in_progress"
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

    def test_previous_plans_block_is_empty_by_default(self):
        variables = self._planner()._get_prompt_variables(
            KleaAgentState(), LLMNodeContext()
        )
        self.assertEqual(variables["previous_plans_block"], "")

    def test_previous_plans_block_renders_versions_and_reasons(self):
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[StepSchema(description="current approach")],
                plan_version=2,
            ),
            plan_history=[
                PlanRevision(
                    version=1,
                    steps=[
                        StepSchema(
                            step_number=1,
                            description="old approach",
                            suggested_tools=["read_file"],
                        )
                    ],
                    abandoned_reason="tool failed",
                )
            ],
        )
        variables = self._planner()._get_prompt_variables(state, LLMNodeContext())
        block = variables["previous_plans_block"]
        self.assertIn("## Previous plan versions (abandoned)", block)
        self.assertIn("Plan version 1", block)
        self.assertIn("tool failed", block)
        self.assertIn("old approach", block)
        self.assertNotIn("current approach", block)
        self.assertIn("current approach", variables["plan"])

    def test_previous_plans_block_is_bounded_to_nine(self):
        history = [
            PlanRevision(version=index, abandoned_reason=f"reason {index}")
            for index in range(1, 13)
        ]
        state = KleaAgentState(plan_history=history)
        block = self._planner()._get_prompt_variables(state, LLMNodeContext())[
            "previous_plans_block"
        ]
        versions = [
            line for line in block.splitlines() if line.startswith("### Plan version ")
        ]
        self.assertEqual(
            versions, [f"### Plan version {index}" for index in range(4, 13)]
        )

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

    def test_automated_replan_records_prior_plan(self):
        """The superseded runnable plan is kept with its replan reason."""
        prior = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="read the old file",
                    suggested_tools=["read_file"],
                )
            ],
            status="in_progress",
            plan_version=1,
            automated_plan_revisions=1,
        )
        state = KleaAgentState(
            plan=prior,
            replan_reason="tool failed",
            evaluation=EvaluationSchema(overall="abort"),
        )
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            state,
            LLMNodeContext(),
        )
        self.assertEqual(len(update["plan_history"]), 1)
        self.assertEqual(update["plan_history"][0].version, 1)
        self.assertEqual(update["plan_history"][0].abandoned_reason, "tool failed")
        self.assertEqual(
            update["plan_history"][0].steps[0].description, "read the old file"
        )
        self.assertEqual(update["evaluation"], EvaluationSchema())

    def test_review_revision_records_prior_plan_with_human_reason(self):
        state = KleaAgentState(
            human_feedback="please revise step 1",
            plan=PlanSchema(
                step_list=[StepSchema(description="s")],
                status="in_progress",
                plan_version=1,
            ),
        )
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            state,
            LLMNodeContext(),
        )
        self.assertEqual(
            update["plan_history"][0].abandoned_reason,
            "Human review: please revise step 1",
        )

    def test_needs_input_draft_is_not_recorded_as_a_failed_plan(self):
        """A blocked draft is a pause, not an approach that failed."""
        prior = PlanSchema(
            status="needs_input",
            plan_version=2,
            step_list=[StepSchema(step_number=1, needs_input=["which file?"])],
        )
        state = KleaAgentState(
            plan=prior,
            plan_history=[PlanRevision(version=1, abandoned_reason="old")],
            human_input={1: ["models/cell.nml"]},
        )
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            state,
            LLMNodeContext(),
        )
        self.assertEqual([item.version for item in update["plan_history"]], [1])

    def test_plan_history_is_fifo_capped(self):
        prior = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="latest attempt",
                    suggested_tools=["read_file"],
                )
            ],
            status="in_progress",
            plan_version=10,
        )
        history = [PlanRevision(version=index) for index in range(1, 10)]
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(plan=prior, plan_history=history),
            LLMNodeContext(),
        )
        self.assertEqual(
            [item.version for item in update["plan_history"]],
            list(range(2, 11)),
        )

    def test_first_plan_clears_stale_evaluation(self):
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(evaluation=EvaluationSchema(overall="abort")),
            LLMNodeContext(),
        )
        self.assertEqual(update["evaluation"], EvaluationSchema())

    def test_plan_write_resets_binding_calls(self):
        """A new plan invalidates any calls bound for the previous one."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(binding_calls=[ToolCallSchema(tool="read_file", step=1)]),
            LLMNodeContext(),
        )
        self.assertEqual(update["binding_calls"], [])

    def test_plan_recorded_in_internal_history(self):
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
        self.assertEqual(len(update["messages_internal"]), 1)
        self.assertIn("Plan (in_progress)", update["messages_internal"][0].content)

    def test_revision_budget_exhausted_asks_for_guidance(self):
        """Budget exhaustion pauses for guidance and keeps the old steps."""
        planner = Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
            max_automated_plan_revisions=2,
        )
        prior_step = StepSchema(
            step_number=1, description="s", suggested_tools=["read_file"]
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
                plan=PlanSchema(
                    step_list=[prior_step],
                    plan_version=3,
                    automated_plan_revisions=2,
                ),
                replan_reason="tool failed",
            ),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "needs_input")
        self.assertEqual(update["plan"].automated_plan_revisions, 3)
        self.assertEqual(update["plan"].step_list[0].description, "s")
        guidance = update["plan"].step_list[-1]
        self.assertEqual(guidance.description, "Provide guidance to continue")
        self.assertIn("automated replan budget", guidance.needs_input[0])
        self.assertIn("tool failed", guidance.needs_input[0])
        self.assertIn("previous plan versions", guidance.needs_input[0])
        self.assertNotIn("failure_reason", update)
        self.assertEqual(update["replan_reason"], "")

    def test_validation_failure_preserves_prior_plan(self):
        """A rejected invalid plan keeps the previous plan visible."""
        planner = self._planner()
        prior = PlanSchema(
            step_list=[StepSchema(step_number=1, description="existing step")],
            plan_version=2,
        )
        ctx = LLMNodeContext()
        ctx.validation_feedback = "invalid plan"

        update = planner._update_state(PlannerOutput(), KleaAgentState(plan=prior), ctx)

        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["plan"].step_list[0].description, "existing step")
        self.assertIn("plan validation failed", update["failure_reason"])

    def test_explicit_unplannable_preserves_prior_plan(self):
        """A Planner-decided impossible task keeps the previous plan visible."""
        prior = PlanSchema(
            step_list=[StepSchema(step_number=1, description="existing step")],
            plan_version=2,
        )

        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(status="unplannable"), reason="impossible"
            ),
            KleaAgentState(plan=prior),
            LLMNodeContext(),
        )

        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["plan"].step_list[0].description, "existing step")
        self.assertEqual(update["failure_reason"], "impossible")

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

    def test_guidance_reentry_resets_revision_counter(self):
        """Human guidance after budget exhaustion resets the replan budget."""
        update = self._planner()._update_state(
            PlannerOutput(
                plan=PlannerPlanSchema(
                    step_list=[
                        StepSchema(description="s", suggested_tools=["read_file"])
                    ]
                )
            ),
            KleaAgentState(
                plan=PlanSchema(
                    step_list=[StepSchema(step_number=1, description="blocked")],
                    status="needs_input",
                    automated_plan_revisions=3,
                ),
                human_input={1: ["use a different approach"]},
            ),
            LLMNodeContext(),
        )
        self.assertEqual(update["plan"].status, "in_progress")
        self.assertEqual(update["plan"].automated_plan_revisions, 0)

    def test_human_review_resets_revision_counter(self):
        """A review re-entry (feedback set) resets the counter."""
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
                    status="in_progress", plan_version=1, automated_plan_revisions=3
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

    def _planner(self, tools_info=None) -> Planner:
        if tools_info is None:
            tools_info = {"code": {"read_file": ToolInfo(read_only=True)}}
        return Planner(
            logger=logging.getLogger("test"),
            label="Planning",
            llm_models={"plan": object()},
            tools_info=tools_info,
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

    def test_large_plan_is_not_capped(self):
        """Long-horizon plans are not rejected by an arbitrary step limit."""
        planner = self._planner()
        output = PlannerOutput(plan=PlannerPlanSchema(step_list=self._steps(100)))

        self.assertIsNone(
            planner._validate_result(output, KleaAgentState(), LLMNodeContext())
        )
        update = planner._update_state(
            output, KleaAgentState(query="q"), LLMNodeContext()
        )
        self.assertEqual(update["plan"].status, "in_progress")
        self.assertEqual(len(update["plan"].step_list), 100)

    def test_exhausted_validation_fails_closed(self):
        """An invalid plan that survives retries must not be accepted.

        The base retry loop exposes the last validation error through
        ``ctx.validation_feedback``; the Planner must fail closed instead of
        writing the invalid plan.
        """
        ctx = LLMNodeContext()
        ctx.validation_feedback = "step 1 depends on 2, which does not exist"
        invalid = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        description="do work",
                        suggested_tools=["read_file"],
                        depends_on=[2],
                    )
                ]
            )
        )

        update = self._planner()._update_state(invalid, KleaAgentState(query="q"), ctx)

        self.assertEqual(update["plan"].status, "unplannable")
        self.assertEqual(update["plan"].step_list, [])
        self.assertIn("plan validation failed", update["failure_reason"])
        self.assertIn(ctx.validation_feedback, update["failure_reason"])
        self.assertIn("Planning failed", update["messages_internal"][-1].content)

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

    def test_rejects_empty_in_progress_plan(self):
        """There is no zero-step bypass: already-known facts need a step."""
        output = PlannerOutput(
            goal=GoalSchema(goal="g"),
            plan=PlannerPlanSchema(status="in_progress"),
        )
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("at least one step", error)

    def test_accepts_chat_without_goal_or_steps(self):
        """Chat is a route signal: no goal and no steps are required."""
        output = PlannerOutput(plan=PlannerPlanSchema(status="chat"))
        self.assertIsNone(
            self._planner()._validate_result(output, KleaAgentState(), LLMNodeContext())
        )

    def test_rejects_chat_with_steps(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                status="chat",
                step_list=[StepSchema(description="s", suggested_tools=["read_file"])],
            )
        )
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("chat", error)

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

    def test_rejects_tool_step_when_no_tools_available(self):
        """A tool step is invalid when there is nothing to bind or dispatch."""
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[StepSchema(description="s", suggested_tools=["read_file"])]
            )
        )
        error = self._planner(tools_info={})._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("no tools are available", error)

    def test_rejects_tool_excluded_by_access_level(self):
        planner = self._planner(
            tools_info={
                "code": {
                    "read_file": ToolInfo(read_only=True),
                    "write_file": ToolInfo(read_only=False),
                }
            }
        )
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(description="write", suggested_tools=["write_file"])
                ]
            )
        )
        error = planner._validate_result(
            output,
            KleaAgentState(access_level="read_only"),
            LLMNodeContext(),
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("does not permit", error)
        self.assertIn("read_only", error)

    def test_rejects_unknown_tool(self):
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[StepSchema(description="s", suggested_tools=["not_a_tool"])]
            )
        )
        error = self._planner()._validate_result(
            output, KleaAgentState(), LLMNodeContext()
        )
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("unknown tool", error)

    def test_accepts_disclosed_read_only_tool(self):
        planner = self._planner(
            tools_info={
                "code": {
                    "read_file": ToolInfo(read_only=True),
                    "write_file": ToolInfo(read_only=False),
                }
            }
        )
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                step_list=[
                    StepSchema(description="read", suggested_tools=["read_file"])
                ]
            )
        )
        self.assertIsNone(
            planner._validate_result(
                output,
                KleaAgentState(access_level="read_only"),
                LLMNodeContext(),
            )
        )

    def test_blocked_step_skips_tool_name_check(self):
        """A blocked draft need not name an available tool yet."""
        planner = self._planner(
            tools_info={"code": {"write_file": ToolInfo(read_only=False)}}
        )
        output = PlannerOutput(
            plan=PlannerPlanSchema(
                status="needs_input",
                step_list=[
                    StepSchema(
                        description="write",
                        needs_input=["which file?"],
                        suggested_tools=["write_file"],
                    )
                ],
            )
        )
        self.assertIsNone(
            planner._validate_result(
                output,
                KleaAgentState(access_level="read_only"),
                LLMNodeContext(),
            )
        )

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
        content = update["messages_internal"][0].content
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
