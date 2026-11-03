#!/usr/bin/env python3
"""
Tests for the Planner evidence relevance selector.

One test per rule branch of ``render_plan_evidence``: a failed step, the
pending dependency closure (including a transitive grandparent dependency), an
Evaluator-flagged step, the last completed step, an abandoned revision, an
unrelated completed step reduced to a summary plus step reference, and a review
step that is never a payload source.

File: tests/test_evidence.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from typing import Literal

from fastmcp.client.client import CallToolResult
from klea_agent.evidence import (
    NO_EVIDENCE,
    render_plan_evidence,
    render_step_observations,
)
from klea_agent.schemas import (
    PlannerPlanSchema,
    PlanRevision,
    StepOutput,
    StepSchema,
)
from mcp.types import TextContent


def _step(
    number: int,
    *,
    kind: Literal["tool", "reasoning", "review"] = "tool",
    status: Literal["pending", "done", "skipped", "failed"] = "pending",
    depends_on: list[int] | None = None,
) -> StepSchema:
    """Build a plan step for the selector tests."""
    return StepSchema(
        step_number=number,
        description=f"step {number}",
        kind=kind,
        status=status,
        depends_on=list(depends_on or []),
    )


def _output(text: str) -> StepOutput:
    """A text step output (a reasoning-style result)."""
    return StepOutput(result=text)


def _tool_output(
    tool: str,
    signature: str,
    text: str,
    *,
    is_error: bool = False,
) -> StepOutput:
    """A tool step output carrying its recorded call signature."""
    return StepOutput(
        result=CallToolResult(
            content=[TextContent(type="text", text=text)],
            structured_content=None,
            meta=None,
            is_error=is_error,
        ),
        tool=tool,
        signature=signature,
    )


def test_failed_step_is_rendered_in_full():
    """Tier 1: a failed step's latest attempt is shown in full."""
    plan = PlannerPlanSchema(step_list=[_step(1, status="failed"), _step(2)])

    result = render_plan_evidence(plan, {1: [_output("it broke")]})

    assert "### Step 1\n" in result
    assert "it broke" in result


def test_pending_dependency_closure_includes_transitive_grandparent():
    """Tier 2: the closure is transitive, not just the direct ``depends_on``.

    Step 3 depends on 2 and 2 depends on 1, so 1 is a grandparent: it is not a
    direct dependency of any pending step and is selected only by walking the
    edges transitively.
    """
    plan = PlannerPlanSchema(
        step_list=[
            _step(1, status="done"),
            _step(2, status="done", depends_on=[1]),
            _step(3, depends_on=[2]),
        ]
    )
    step_outputs = {1: [_output("grandparent payload")], 2: [_output("parent")]}

    result = render_plan_evidence(plan, step_outputs)

    assert "### Step 1\n" in result
    assert "### Step 2\n" in result
    assert "grandparent payload" in result


def test_render_step_observations_scopes_to_selected_numbers():
    """A ``numbers`` selection leaves every other recorded step out."""
    step_outputs = {1: [_output("first payload")], 2: [_output("second payload")]}

    text = render_step_observations(step_outputs, numbers=[2])

    assert "Step 2:" in text
    assert "second payload" in text
    assert "Step 1:" not in text
    assert "first payload" not in text


def test_render_step_observations_without_matching_output_is_no_evidence():
    """No recorded output for the selection yields the sentinel, not an empty block."""
    step_outputs = {1: [_output("payload")]}

    assert render_step_observations(step_outputs, numbers=[7]) == NO_EVIDENCE
    assert render_step_observations({}) == NO_EVIDENCE


def test_render_step_observations_renders_every_selected_step_in_full():
    """There is no evidence budget: even a very large payload is not demoted."""
    large = "x" * 200_000
    step_outputs = {1: [_output(large)], 2: [_output("second payload")]}

    text = render_step_observations(step_outputs)

    assert large in text
    assert "second payload" in text
    assert "(full payload omitted" not in text


def test_failed_step_over_the_old_budget_is_still_rendered_in_full():
    """A failed step is never demoted, even when its full render is very large."""
    large = "x" * 200_000
    plan = PlannerPlanSchema(step_list=[_step(1, status="failed")])
    step_outputs = {1: [_output(large)]}

    result = render_plan_evidence(plan, step_outputs)

    assert "### Step 1\n" in result
    assert large in result


def test_unselected_step_summary_keeps_the_call_signature():
    """The Planner fallback still names the call a summarised step ran."""
    plan = PlannerPlanSchema(
        step_list=[_step(1, status="done"), _step(2, status="done")]
    )
    step_outputs = {
        1: [_tool_output("grep", 'grep(pattern="foo", path=".")', "[]")],
        2: [_output("last completed")],
    }

    result = render_plan_evidence(plan, step_outputs)

    assert "### Step 1 (summary)" in result
    assert "### Step 2\n" in result
    assert 'grep(pattern="foo", path="."): ok' in result


def test_evaluator_flagged_step_is_rendered_in_full():
    """Tier 3: a step the Evaluator flagged incomplete is shown in full."""
    plan = PlannerPlanSchema(
        step_list=[
            _step(1, status="done"),
            _step(2, status="done"),
            _step(3, depends_on=[2]),
        ]
    )
    step_outputs = {1: [_output("flagged payload")], 2: [_output("last done")]}

    result = render_plan_evidence(plan, step_outputs, flagged={1})

    # Step 2 is the last completed step; step 1 is selected only by the flag.
    assert "### Step 1\n" in result
    assert "### Step 2\n" in result
    assert "flagged payload" in result


def test_last_completed_step_is_rendered_in_full():
    """Tier 4: the highest ``done`` step with a recorded result is shown."""
    plan = PlannerPlanSchema(
        step_list=[
            _step(1, status="done"),
            _step(2, status="done"),
            _step(3),
        ]
    )
    step_outputs = {1: [_output("older")], 2: [_output("newest")]}

    result = render_plan_evidence(plan, step_outputs)

    assert "### Step 2\n" in result
    assert "### Step 1 (summary)" in result
    assert "newest" in result


def test_abandoned_revision_step_is_rendered_in_full():
    """Tier 5: a step from the latest abandoned revision is shown in full."""
    plan = PlannerPlanSchema(step_list=[_step(1), _step(2, status="done")])
    step_outputs = {1: [_output("old attempt payload")], 2: [_output("current")]}
    abandoned = PlanRevision(
        version=1,
        steps=[_step(1)],
        abandoned_reason="the old approach failed",
    )

    result = render_plan_evidence(plan, step_outputs, abandoned=abandoned)

    # Step 2 is the last completed step; step 1 is selected only by the
    # abandoned revision.
    assert "### Step 1\n" in result
    assert "### Step 2\n" in result
    assert "old attempt payload" in result


def test_unrelated_completed_step_is_reduced_to_summary_and_pointer():
    """Every other recorded step keeps a summary plus a step reference."""
    plan = PlannerPlanSchema(
        step_list=[
            _step(1, status="done"),
            _step(2, status="done"),
            _step(3, depends_on=[2]),
        ]
    )
    step_outputs = {1: [_output("unrelated payload")], 2: [_output("reused")]}

    result = render_plan_evidence(plan, step_outputs)

    assert "### Step 2\n" in result
    assert "### Step 1 (summary)" in result
    assert "retrieve it by planning a step that depends on 1" in result
    # The unselected payload is compressed, not rendered as a full block.
    assert "### Step 1\n" not in result
    assert NO_EVIDENCE not in result


def test_review_step_yields_no_evidence():
    """A review step is never a payload source, even if an output exists."""
    plan = PlannerPlanSchema(
        step_list=[_step(1, kind="review", status="done"), _step(2, status="done")]
    )
    # A review step records no StepOutput in practice; the contrived entry must
    # still be excluded by the selector.
    step_outputs = {1: [_output("user approved")], 2: [_output("work")]}

    result = render_plan_evidence(plan, step_outputs)

    assert "Step 1" not in result
    assert "### Step 2\n" in result
