#!/usr/bin/env python3
"""
Tests for the agent schemas (literals, defaults) and msgpack checkpointing.

File: tests/test_schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import pytest
from fastmcp.client.client import CallToolResult
from klea_agent.klea_agent import KleaAgent
from klea_agent.nodes.answer_from_results import AnswerSchema
from klea_agent.schemas import (
    MAX_CONTINUATION_STEPS,
    MAX_PREVIOUS_PLANS,
    ContinuationEvidence,
    EvaluationSchema,
    GoalSchema,
    KleaAgentState,
    PlannerOutput,
    PlannerPlanSchema,
    PlanRevision,
    PlanSchema,
    ReasoningSchema,
    RouteSchema,
    StepEvaluation,
    StepOutput,
    StepSchema,
    trim_plan_history,
)
from klea_utils.graph.schemas import TokenUsage
from klea_utils.graph.state import BaseGraphSchema
from klea_utils.mcp.schemas import ToolCallSchema
from langgraph.channels.binop import BinaryOperatorAggregate
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import StateGraph
from mcp.types import TextContent
from pydantic import BaseModel


class TestRouteSchema:
    """Entry routing defaults to the fail-closed ``task``."""

    def test_defaults_to_task(self):
        assert RouteSchema().route == "task"

    @pytest.mark.parametrize("route", ["chat", "task"])
    def test_accepts_routes(self, route):
        assert RouteSchema(route=route).route == route


class TestPlanSchema:
    """Entry routing statuses on the plan."""

    @pytest.mark.parametrize("status", ["in_progress", "unplannable"])
    def test_entry_statuses_accepted(self, status):
        assert PlanSchema(status=status).status == status


class TestPlanRevision:
    """Superseded plan versions render and stay FIFO-bounded."""

    def test_render_includes_reason_and_steps(self):
        revision = PlanRevision(
            version=2,
            steps=[StepSchema(step_number=1, description="read the input")],
            abandoned_reason="tool failed",
        )
        rendered = revision.render()
        assert "Plan version 2" in rendered
        assert "Abandoned: tool failed" in rendered
        assert "read the input" in rendered

    def test_previous_plans_text_and_sections(self):
        history = [PlanRevision(version=1, abandoned_reason="first attempt")]
        state = KleaAgentState(
            plan_history=history,
            plan=PlanSchema(
                step_list=[StepSchema(step_number=1, description="try again")],
                plan_version=2,
            ),
        )
        assert "Plan version 1" in state.previous_plans_text()
        sections = state.plan_history_sections()
        assert [section["title"] for section in sections] == [
            "Plan version 1 (abandoned)",
            "Current plan version 2",
        ]
        assert len(state.plan_history_sections(include_current=False)) == 1

    def test_trim_plan_history_keeps_the_most_recent(self):
        history = [PlanRevision(version=index) for index in range(12)]
        trimmed = trim_plan_history(history)
        assert len(trimmed) == MAX_PREVIOUS_PLANS
        assert [revision.version for revision in trimmed] == list(range(3, 12))


class TestContinuationEvidence:
    """The cross-run evidence record is compact and bounded."""

    def _state(self) -> KleaAgentState:
        state = KleaAgentState(project_root="/tmp/project")
        state.goal = GoalSchema(
            goal="make a plan for X", success_criteria="plan produced"
        )
        state.plan = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="read the source",
                    success_criteria="source read",
                    suggested_tools=["read_file"],
                    status="done",
                ),
                StepSchema(
                    step_number=2,
                    description="write the plan",
                    success_criteria="plan written",
                    kind="reasoning",
                    status="done",
                ),
                StepSchema(step_number=3, description="implement", status="pending"),
            ],
            status="completed",
            plan_version=2,
        )
        state.plan_history = [PlanRevision(version=1, abandoned_reason="tool failed")]
        state.step_outputs = {
            1: [
                StepOutput(
                    result=CallToolResult(
                        content=[TextContent(type="text", text="SECRET " * 200)],
                        structured_content=None,
                        meta=None,
                    ),
                    tool="read_file",
                )
            ],
            2: [
                StepOutput(
                    result="A plan for X: first do Y",
                    rationale="because",
                )
            ],
        }
        return state

    def test_builds_bounded_summary_without_raw_output(self):
        evidence = self._state().build_continuation_evidence(
            "Final plan answer",
            captured_at="2026-10-21T00:00:00+00:00",
            artefact_ids=["plan-x"],
        )

        assert evidence.goal == "make a plan for X"
        assert evidence.success_criteria == "plan produced"
        assert evidence.plan_version == 2
        assert evidence.plan_status == "completed"
        assert len(evidence.steps) == 3
        assert evidence.steps[0].summary == "read_file: ok"
        assert "SECRET" not in evidence.render()
        assert evidence.steps[1].summary.startswith("reasoning:")
        assert evidence.steps[2].summary == "(no recorded output)"
        assert evidence.abandoned_plans[0].reason == "tool failed"
        assert evidence.final_answer == "Final plan answer"
        assert evidence.artefact_ids == ["plan-x"]
        assert evidence.project_root == "/tmp/project"
        assert evidence.captured_at == "2026-10-21T00:00:00+00:00"
        rendered = evidence.render()
        assert "Plan version 2" in rendered
        assert "Abandoned plans" in rendered
        assert "Final answer" in rendered

    def test_evidence_caps_steps_and_final_answer(self):
        state = KleaAgentState()
        state.plan = PlanSchema(
            step_list=[
                StepSchema(step_number=index + 1)
                for index in range(MAX_CONTINUATION_STEPS + 2)
            ]
        )
        evidence = state.build_continuation_evidence("x" * 4000)

        assert len(evidence.steps) == MAX_CONTINUATION_STEPS
        assert evidence.steps_omitted == 2
        assert len(evidence.final_answer) < 4000


class TestEvaluationSchema:
    """The evaluator verdict gains an explicit abort outcome."""

    def test_evaluation_accepts_abort(self):
        assert EvaluationSchema(overall="abort").overall == "abort"

    def test_per_step_verdicts_map(self):
        evaluation = EvaluationSchema(
            evaluations=[
                StepEvaluation(step_number=1, verdict="step_done", reason="ok")
            ]
        )
        assert evaluation.evaluations[0].step_number == 1
        assert evaluation.evaluations[0].verdict == "step_done"

    def test_step_skipped_verdict_accepted(self):
        evaluation = EvaluationSchema(
            evaluations=[StepEvaluation(step_number=1, verdict="step_skipped")]
        )
        assert evaluation.evaluations[0].verdict == "step_skipped"

    def test_step_incomplete_feedback_filters_steps_and_verdicts(self):
        evaluation = EvaluationSchema(
            evaluations=[
                StepEvaluation(
                    step_number=1,
                    verdict="step_incomplete",
                    reason="state the mechanism",
                ),
                StepEvaluation(step_number=2, verdict="step_done", reason="ok"),
                StepEvaluation(
                    step_number=3,
                    verdict="step_incomplete",
                    reason="another step",
                ),
            ]
        )
        feedback = evaluation.step_incomplete_feedback({1, 2})
        assert "Step 1: state the mechanism" in feedback
        assert "Step 2" not in feedback
        assert "Step 3" not in feedback
        assert evaluation.step_incomplete_feedback({2}) == ""
        assert evaluation.step_incomplete_feedback(set()) == ""


class TestStepStatus:
    """The distinct ``skipped`` status resolves a step that is not needed."""

    def test_skipped_status_label_and_render(self):
        step = StepSchema(step_number=1, description="optional", status="skipped")
        assert step.status_label() == "[SKIPPED]"
        assert step.status_label(markdown=True) == "[-]"
        assert "[SKIPPED]" in step.render()
        assert "[-]" in step.render(markdown=True)


class TestStateDefaults:
    """New budget/history fields default cleanly."""

    def test_new_fields_default(self):
        state = KleaAgentState()
        assert state.tool_retry_counts == {}
        assert state.step_attempt_counts == {}
        assert state.plan.plan_version == 0
        assert state.plan.human_feedback_rounds == 0
        assert state.plan.automated_plan_revisions == 0
        assert state.failure_reason == ""
        assert state.human_feedback == ""
        assert state.route == RouteSchema()


class TestSharedStateInheritance:
    """KleaAgentState extends the shared BaseGraphSchema (ADR-0032)."""

    def test_is_base_graph_schema(self):
        assert issubclass(KleaAgentState, BaseGraphSchema)

    def test_shared_defaults(self):
        state = KleaAgentState()
        assert state.query == ""
        assert state.messages == []
        assert state.messages_internal == []
        assert state.guard_decision == "safe"
        assert state.summarised_till == 0
        assert state.summarised_internal_till == 0
        assert state.message_for_user == ""
        assert state.tool_calls == []
        assert state.tool_results == []
        assert state.usage_metrics == TokenUsage()
        assert state.access_level == "full"

    def test_shared_and_app_channels_present(self):
        channels = StateGraph(KleaAgentState).channels
        assert {"messages", "tool_calls", "tool_results", "context_summary"} <= set(
            channels
        )
        assert {"mode", "plan", "route"} <= set(channels)
        assert isinstance(channels["usage_metrics"], BinaryOperatorAggregate)


class TestStepOutputRender:
    """StepOutput.render and KleaAgentState.observations_text."""

    @staticmethod
    def _result() -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text='{"a": 1}')],
            structured_content={"a": 1},
            meta=None,
        )

    def test_render_includes_tool_and_displayed(self):
        entry = StepOutput(result=self._result(), tool="edit_file", displayed=True)
        rendered = entry.render()
        assert rendered.startswith("### edit_file (displayed_to_user: yes)")
        assert '{"a": 1}' in rendered

    def test_render_reasoning_string_result(self):
        """A str result (reasoning conclusion) renders without a tool name."""
        entry = StepOutput(result="H1: X causes Y", tool="", displayed=False)
        rendered = entry.render()
        assert rendered.startswith("### reasoning (displayed_to_user: no)")
        assert "H1: X causes Y" in rendered

    def test_render_reasoning_includes_rationale(self):
        """The reasoning rationale is rendered so observations carry it."""
        entry = StepOutput(
            result="H1: X causes Y",
            tool="",
            displayed=False,
            rationale="grounded in the observations",
        )
        rendered = entry.render()
        assert "H1: X causes Y" in rendered
        assert "Rationale: grounded in the observations" in rendered

    def test_render_tool_result_omits_empty_rationale(self):
        """A tool result has no rationale, so none is rendered."""
        entry = StepOutput(result=self._result(), tool="edit_file", displayed=True)
        assert "Rationale:" not in entry.render()

    def test_observations_text_groups_by_step(self):
        state = KleaAgentState(
            step_outputs={
                2: [
                    StepOutput(result=self._result(), tool="edit_file", displayed=True),
                    StepOutput(
                        result=self._result(), tool="run_command", displayed=False
                    ),
                ]
            }
        )
        text = state.observations_text()
        assert text.startswith("Step 2:")
        assert "### edit_file (displayed_to_user: yes)" in text
        assert "### run_command (displayed_to_user: no)" in text

    def test_observations_text_empty(self):
        assert KleaAgentState().observations_text() == "(no observations)"

    def test_summary_tool_ok(self):
        entry = StepOutput(result=self._result(), tool="edit_file", displayed=True)
        assert entry.summary() == "edit_file: ok"

    def test_summary_tool_error_includes_reason(self):
        result = CallToolResult(
            content=[TextContent(type="text", text="old_string not found")],
            structured_content=None,
            meta=None,
            is_error=True,
        )
        entry = StepOutput(result=result, tool="edit_file")
        assert entry.summary() == "edit_file: error - old_string not found"

    def test_summary_reasoning_first_line(self):
        entry = StepOutput(result="H1: X causes Y\nextra detail", tool="")
        assert entry.summary() == "reasoning: H1: X causes Y"

    def test_summary_truncates_a_long_line(self):
        entry = StepOutput(result="x" * 200, tool="")
        summary = entry.summary()
        assert summary.endswith("...")
        assert len(summary) == len("reasoning: ") + 80

    def test_artefacts_text_empty(self):
        assert KleaAgentState().artefacts_text() == "(no artefacts)"

    def test_artefacts_text_renders_id_type_content_metadata(self):
        from klea_agent.schemas import ArtefactSchema

        state = KleaAgentState(
            artefacts={
                "hypothesis": ArtefactSchema(
                    id_="hypothesis",
                    type_="hypothesis",
                    content="H1: X causes Y",
                    metadata={"goal": "generate a hypothesis"},
                )
            }
        )
        text = state.artefacts_text()
        assert "hypothesis (type: hypothesis)" in text
        assert "H1: X causes Y" in text
        assert "goal=generate a hypothesis" in text

    def test_artefacts_text_keeps_most_recent_up_to_count_cap(self):
        """The prompt block keeps the newest N artefacts, oldest first."""
        from klea_agent.schemas import MAX_ARTEFACTS_IN_PROMPT, ArtefactSchema

        total = MAX_ARTEFACTS_IN_PROMPT + 3
        artefacts = {
            f"a{i}": ArtefactSchema(id_=f"a{i}", content=f"item-{i}-end")
            for i in range(total)
        }
        state = KleaAgentState(artefacts=artefacts)

        text = state.artefacts_text()

        for i in range(3):
            assert f"item-{i}-end" not in text
        for i in range(3, total):
            assert f"item-{i}-end" in text
        assert "3 older artefacts omitted" in text
        # Selected entries are emitted chronologically (oldest -> newest).
        assert text.index("item-3-end") < text.index(f"item-{total - 1}-end")

    def test_artefacts_text_char_cap_keeps_the_newest(self, monkeypatch):
        """A large older artefact is dropped before the newest one."""
        from klea_agent import schemas

        monkeypatch.setattr(schemas, "MAX_ARTEFACTS_PROMPT_CHARS", 80)
        state = KleaAgentState(
            artefacts={
                "old": schemas.ArtefactSchema(id_="old", content="o" * 100),
                "new": schemas.ArtefactSchema(id_="new", content="n" * 100),
            }
        )

        text = state.artefacts_text()

        assert "n" * 100 in text
        assert "o" * 100 not in text
        assert "1 older artefact omitted" in text


class TestReviewSections:
    """The review interrupt carries all pending steps, including review gates."""

    @staticmethod
    def _result() -> CallToolResult:
        return CallToolResult(
            content=[TextContent(type="text", text="ok")],
            structured_content={"ok": True},
            meta=None,
        )

    def test_whole_plan_approval_lists_the_pending_steps(self):
        """A review before any work presents the plan under approval."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, kind="review", description="Approve"),
                    StepSchema(step_number=2, description="fetch"),
                    StepSchema(step_number=3, description="write"),
                ]
            )
        )
        headline, sections = state.review_sections()
        # The gate is the approval action, so only work steps are counted.
        assert headline == "Approve this plan (2 steps)"
        assert [section["title"] for section in sections] == ["Plan"]
        body = sections[0]["body"]
        assert "Step 1: Approve" in body and "human review" in body
        assert "Step 2: fetch" in body and "Step 3: write" in body

    def test_checkpoint_lists_the_remaining_steps_including_the_gate(self):
        """A checkpoint shows every pending step it unblocks, gates included."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, description="fetch", status="done"),
                    StepSchema(
                        step_number=2,
                        kind="review",
                        description="Review progress",
                        depends_on=[1],
                    ),
                    StepSchema(step_number=3, description="write"),
                ]
            ),
            step_outputs={1: [StepOutput(result=self._result(), tool="run_command")]},
        )
        headline, sections = state.review_sections()
        assert headline == "Approve the remaining 1 step"
        assert [section["title"] for section in sections] == ["Remaining"]
        body = sections[0]["body"]
        assert "Step 2: Review progress" in body and "human review" in body
        assert "Step 3: write" in body
        # Completed work and its findings are not re-shown.
        assert "Step 1" not in body
        assert "run_command" not in body

    def test_skipped_steps_mark_a_checkpoint(self):
        """A skipped step counts as work done, so the ask is a checkpoint."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, description="optional", status="skipped"),
                    StepSchema(step_number=2, kind="review", description="Review"),
                    StepSchema(step_number=3, description="write"),
                ]
            )
        )
        headline, _ = state.review_sections()
        assert headline == "Approve the remaining 1 step"

    def test_checkpoint_carries_progress_before_the_pending_steps(self):
        """A synthesized progress digest is shown before the pending steps."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, description="fetch", status="done"),
                    StepSchema(
                        step_number=2,
                        kind="review",
                        description="Review progress",
                        depends_on=[1],
                    ),
                    StepSchema(step_number=3, description="write"),
                ]
            ),
            review_progress="Changed the config and reran the tests.",
        )
        _, sections = state.review_sections()
        assert [section["title"] for section in sections] == [
            "Progress",
            "Remaining",
        ]
        assert sections[0]["body"] == "Changed the config and reran the tests."

    def test_whole_plan_approval_has_no_progress_section(self):
        """A whole-plan approval (no work yet) carries no progress digest."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, kind="review", description="Approve"),
                    StepSchema(step_number=2, description="fetch"),
                ]
            )
        )
        _, sections = state.review_sections()
        assert [section["title"] for section in sections] == ["Plan"]

    def test_work_since_review_is_scoped_to_the_last_gate(self):
        """Only work after the highest approved review gate is included."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(
                        step_number=1,
                        kind="review",
                        description="Approve",
                        status="done",
                    ),
                    StepSchema(step_number=2, description="first", status="done"),
                    StepSchema(step_number=3, description="second", status="done"),
                    StepSchema(
                        step_number=4,
                        kind="review",
                        description="Review progress",
                        depends_on=[3],
                    ),
                    StepSchema(step_number=5, description="next"),
                ]
            ),
            step_outputs={
                2: [StepOutput(result=self._result(), tool="read_file")],
                3: [StepOutput(result=self._result(), tool="write_file")],
            },
        )
        pairs = state.work_since_review()
        assert [step.step_number for step, _ in pairs] == [2, 3]
        text = state.work_since_review_text()
        assert "Step 2: first" in text and "Step 3: second" in text
        assert "read_file" in text and "write_file" in text

    def test_work_since_review_is_empty_before_any_work(self):
        """No work since the last review renders a placeholder."""
        state = KleaAgentState(
            plan=PlanSchema(
                step_list=[
                    StepSchema(step_number=1, kind="review", description="Approve"),
                    StepSchema(step_number=2, description="fetch"),
                ]
            )
        )
        assert state.work_since_review() == []
        assert state.work_since_review_text() == "(no work since the last review)"


class TestPlanReviewedThrough:
    """``PlanSchema.reviewed_through`` scopes the progress digest."""

    def test_zero_before_any_review(self):
        plan = PlanSchema(
            step_list=[StepSchema(step_number=1, description="work", status="done")]
        )
        assert plan.reviewed_through() == 0

    def test_highest_approved_gate(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1, kind="review", description="Approve", status="done"
                ),
                StepSchema(step_number=2, description="work", status="done"),
                StepSchema(step_number=3, kind="review", description="Review"),
                StepSchema(step_number=4, description="work"),
            ]
        )
        assert plan.reviewed_through() == 1

    def test_pending_gate_is_not_counted(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, kind="review", description="Review"),
                StepSchema(step_number=2, description="work"),
            ]
        )
        assert plan.reviewed_through() == 0


class TestPlannerPlanSchema:
    """The Planner sees only the statuses it may set (ADR-0035 2026-09-19)."""

    def test_default_plan_status_is_in_progress(self):
        assert PlannerOutput().plan.status == "in_progress"

    def test_planner_status_enum_excludes_runtime_statuses(self):
        status = PlannerPlanSchema.model_json_schema()["properties"]["status"]
        assert set(status["enum"]) == {
            "in_progress",
            "needs_input",
            "unplannable",
            "chat",
        }


class TestPlanSchemaRuntime:
    """PlanSchema extends PlannerPlanSchema with lifecycle + history counters."""

    def test_plan_schema_extends_planner_plan_schema(self):
        assert issubclass(PlanSchema, PlannerPlanSchema)

    def test_runtime_status_enum_includes_lifecycle(self):
        status = PlanSchema.model_json_schema()["properties"]["status"]
        assert set(status["enum"]) == {
            "not_started",
            "in_progress",
            "needs_input",
            "completed",
            "failed",
            "aborted",
            "user_cancelled",
            "chat",
            "unplannable",
        }

    def test_counters_default_zero(self):
        plan = PlanSchema()
        assert plan.plan_version == 0
        assert plan.human_feedback_rounds == 0
        assert plan.automated_plan_revisions == 0

    def test_revision_summary_empty_for_single_plan_without_review(self):
        assert PlanSchema(plan_version=1).revision_summary() == ""

    def test_revision_summary_reports_version_and_review_rounds(self):
        summary = PlanSchema(plan_version=3, human_feedback_rounds=2).revision_summary()
        assert "version 3" in summary
        assert "2 human review round(s)" in summary

    def test_revision_summary_present_on_review_without_extra_version(self):
        summary = PlanSchema(plan_version=1, human_feedback_rounds=1).revision_summary()
        assert summary != ""


class TestPlanFrontier:
    """``frontier()`` returns the unblocked pending steps (ADR-0041)."""

    def test_empty_plan_has_empty_frontier(self):
        assert PlanSchema().frontier() == []

    def test_no_dependencies_all_pending_are_runnable(self):
        plan = PlanSchema(
            step_list=[StepSchema(step_number=1), StepSchema(step_number=2)]
        )
        assert [s.step_number for s in plan.frontier()] == [1, 2]

    def test_dependency_blocks_until_done(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1),
                StepSchema(step_number=2, depends_on=[1]),
            ]
        )
        assert [s.step_number for s in plan.frontier()] == [1]
        plan.step_list[0].status = "done"
        assert [s.step_number for s in plan.frontier()] == [2]

    def test_done_and_failed_steps_excluded(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, status="done"),
                StepSchema(step_number=2, status="failed"),
                StepSchema(step_number=3),
            ]
        )
        assert [s.step_number for s in plan.frontier()] == [3]

    def test_skipped_dependency_is_satisfied(self):
        """A skipped dependency unblocks its dependents (resolve-as-needed)."""
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, status="skipped"),
                StepSchema(step_number=2, depends_on=[1]),
            ]
        )
        assert [s.step_number for s in plan.frontier()] == [2]

    def test_frontier_caps_steps(self):
        plan = PlanSchema(step_list=[StepSchema(step_number=i + 1) for i in range(5)])
        assert [s.step_number for s in plan.frontier(max_steps=2)] == [1, 2]

    def test_next_batch_is_same_kind_prefix(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, kind="tool"),
                StepSchema(step_number=2, kind="tool"),
                StepSchema(step_number=3, kind="reasoning"),
                StepSchema(step_number=4, kind="tool"),
            ]
        )
        assert [s.step_number for s in plan.next_batch()] == [1, 2]
        assert [s.step_number for s in plan.next_batch(max_steps=1)] == [1]

    def test_next_batch_stops_at_blocked_step(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, kind="reasoning"),
                StepSchema(step_number=2, kind="reasoning", depends_on=[1]),
                StepSchema(step_number=3, kind="tool"),
            ]
        )
        assert [s.step_number for s in plan.next_batch()] == [1]

    def test_render_marks_supplied_current_numbers(self):
        plan = PlanSchema(
            step_list=[StepSchema(step_number=1), StepSchema(step_number=2)]
        )
        rendered = plan.render(current_numbers={2})
        assert "[CURRENT] 2." in rendered
        assert "[CURRENT] 1." not in rendered


class TestPlanValidation:
    """``validate_plan`` enforces the DAG invariants (ADR-0041)."""

    def test_backward_dependency_is_valid(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1),
                StepSchema(step_number=2, depends_on=[1]),
            ]
        )
        assert plan.validate_plan() == []

    def test_dangling_dependency_is_rejected(self):
        plan = PlanSchema(step_list=[StepSchema(step_number=1, depends_on=[9])])
        assert any("not a step" in e for e in plan.validate_plan())

    def test_forward_dependency_is_rejected(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, depends_on=[2]),
                StepSchema(step_number=2),
            ]
        )
        assert any("earlier" in e for e in plan.validate_plan())

    def test_cycle_is_rejected(self):
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, depends_on=[3]),
                StepSchema(step_number=2, depends_on=[1]),
                StepSchema(step_number=3, depends_on=[2]),
            ]
        )
        errors = plan.validate_plan()
        assert any("earlier" in e for e in errors)
        assert any("cycle" in e for e in errors)


class TestCheckpointMsgpack:
    """Nested state models round-trip through the checkpoint serializer."""

    def test_nested_models_roundtrip(self):
        allowed = KleaAgent.__new__(KleaAgent).get_allowed_msgpack_modules()
        serde = JsonPlusSerializer(allowed_msgpack_modules=allowed)
        payload = {
            "route": RouteSchema(route="chat", answer="hi"),
            "planner_output": PlannerOutput(
                goal=GoalSchema(goal="g", success_criteria="c"),
                plan=PlannerPlanSchema(
                    step_list=[StepSchema(description="s")], status="in_progress"
                ),
            ),
            "evaluation": EvaluationSchema(overall="abort"),
            "plan": PlanSchema(
                step_list=[StepSchema(description="s")],
                status="in_progress",
                plan_version=2,
                human_feedback_rounds=1,
                automated_plan_revisions=1,
            ),
            "plan_history": [
                PlanRevision(
                    version=1,
                    steps=[StepSchema(description="old")],
                    abandoned_reason="old plan failed",
                )
            ],
            "continuation_evidence": ContinuationEvidence(goal="g", final_answer="a"),
            "tool_calls": [ToolCallSchema(tool="list_files", args={"path": "."})],
            "step_outputs": {
                1: [
                    StepOutput(
                        result=CallToolResult(
                            content=[],
                            structured_content=None,
                            meta=None,
                        ),
                        tool="list_files",
                        displayed=False,
                    ),
                    # Reasoning conclusions are plain strings in the union.
                    StepOutput(result="H1: X causes Y", tool="", displayed=False),
                ]
            },
            "usage_metrics": TokenUsage(
                input_tokens=1, output_tokens=2, total_tokens=3
            ),
            "step_attempt_counts": {0: 2},
            "failure_reason": "x",
        }
        type_name, data = serde.dumps_typed(payload)
        assert serde.loads_typed((type_name, data)) == payload


def _schema_descriptions(schema: type[BaseModel]) -> list[str]:
    """Every ``description`` pydantic emits for *schema*, root and nested."""
    found: list[str] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if isinstance(node.get("description"), str):
                found.append(node["description"])
            for key, value in node.items():
                if key in ("properties", "$defs") and isinstance(value, dict):
                    for sub in value.values():
                        walk(sub)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(schema.model_json_schema())
    return found


class TestOutputSchemasAreStructureOnly:
    """LLM output schemas carry no prose: semantics live in the markdown prompts."""

    @pytest.mark.parametrize(
        "schema",
        [
            RouteSchema,
            PlannerOutput,
            EvaluationSchema,
            StepEvaluation,
            ReasoningSchema,
            AnswerSchema,
        ],
    )
    def test_no_description_reaches_the_model(self, schema):
        assert _schema_descriptions(schema) == []
