#!/usr/bin/env python3
"""
Agent tools caller tests

Tests for the agent-side override of the shared tools caller's plan section
hook: :meth:`klea_agent.nodes.agent_tools_caller.AgentToolsCaller._get_running_steps_state`
must return the glyphed plan payload (``key="plan"``, preformatted display and
one structured ``items`` content block) for a state carrying a plan, and
``None`` when the state carries no plan.  See ``devdocs/system/streams.md``.

File: tests/test_agent_tools_caller.py
"""

# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
# Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>

import logging
import unittest
from types import SimpleNamespace
from typing import cast

from klea_agent.nodes.agent_tools_caller import AgentToolsCaller, plan_step_summaries
from klea_agent.nodes.plan_content import (
    ROLE_CURRENT,
    ROLE_PENDING,
    plan_content_blocks,
)
from klea_agent.schemas import PlannerPlanSchema, PlanSchema, StepSchema
from klea_utils.content_blocks import ItemsBlock
from klea_utils.mcp.schemas import ToolCallSchema
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


def _make_node() -> AgentToolsCaller:
    """Return an agent tools caller wired for the hook unit test."""
    return AgentToolsCaller(
        logger=logger,
        label="Running tools",
        mcp_client=None,
        tool_infos=None,
    )


def _stamped_call(step: int = 1) -> ToolCallSchema:
    """Return one bound call stamped with its originating plan step."""
    return ToolCallSchema(tool="read_file", args={"path": "notes.txt"}, step=step)


class _Goal(BaseModel):
    """Minimal stand-in for the run's goal object carried on the state."""

    goal: str = ""


class _HookState(BaseModel):
    """Minimal real state carrying the plan and the batch of bound calls."""

    plan: PlanSchema | None = None
    goal: _Goal | None = None
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)


def _block_text(block: object) -> str:
    """Return a content block's textual payload, whatever its model shape."""
    text = getattr(block, "text", None)
    if isinstance(text, str):
        return text
    return str(block)


class TestAgentToolsCallerPlanSection(unittest.TestCase):
    """The overridden hook attaches the glyphed plan section."""

    def test_running_steps_state_carries_glyphed_plan(self) -> None:
        """A plan-carrying state yields the preformatted plan payload."""
        node = _make_node()
        plan = PlanSchema(step_list=[StepSchema(step_number=1, summary="only")])
        state = _HookState(plan=plan, tool_calls=[_stamped_call()])

        data = node._get_running_steps_state(state)

        assert data is not None
        self.assertEqual(data.key, "plan")
        self.assertTrue(data.preformatted)
        self.assertTrue(data.display)
        content = data.content
        self.assertIsNotNone(content)
        assert content is not None
        self.assertEqual(len(content), 1)
        block = cast(ItemsBlock, content[0])
        self.assertEqual(block.kind, "items")
        self.assertEqual(len(block.items), len(plan.step_list))
        self.assertTrue(block.items)
        self.assertEqual(block.items[0].text, "Step 1/1: only")

    def test_running_steps_state_prepends_goal_block(self) -> None:
        """A goal-bearing state leads with the goal, then the unchanged steps."""
        node = _make_node()
        goal = "Implement the feature end to end"
        plan = PlanSchema(step_list=[StepSchema(step_number=1, summary="only")])
        state = _HookState(
            plan=plan, goal=_Goal(goal=goal), tool_calls=[_stamped_call()]
        )

        data = node._get_running_steps_state(state)

        assert data is not None
        content = data.content
        self.assertIsNotNone(content)
        assert content is not None
        # Goal text block, a blank-line spacer, then the steps block.
        self.assertEqual(len(content), 3)

        # The goal text block comes first and stays on a single rendered line.
        goal_block = content[0]
        self.assertNotEqual(goal_block.kind, "items")
        goal_text = _block_text(goal_block)
        self.assertIn(goal, goal_text)
        self.assertNotIn("\n", goal_text)

        # A blank-line spacer (a single space, so the renderers keep it)
        # separates the goal line from the steps.
        spacer_block = content[1]
        self.assertNotEqual(spacer_block.kind, "items")
        self.assertEqual(getattr(spacer_block, "text", "").strip(), "")

        # The steps block follows unchanged.
        steps_block = cast(ItemsBlock, content[2])
        self.assertEqual(steps_block.kind, "items")
        self.assertEqual(len(steps_block.items), len(plan.step_list))
        self.assertTrue(steps_block.items)
        self.assertEqual(steps_block.items[0].text, "Step 1/1: only")

    def test_running_steps_state_marks_every_dispatched_step_current(self) -> None:
        """A parallel batch marks each dispatched step current, the rest pending."""
        node = _make_node()
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, summary="first"),
                StepSchema(step_number=2, summary="second"),
                StepSchema(step_number=3, summary="third"),
            ]
        )
        state = _HookState(
            plan=plan,
            tool_calls=[_stamped_call(step=1), _stamped_call(step=2)],
        )

        data = node._get_running_steps_state(state)

        assert data is not None
        content = data.content
        self.assertIsNotNone(content)
        assert content is not None
        self.assertEqual(len(content), 1)
        block = cast(ItemsBlock, content[0])
        self.assertEqual(block.kind, "items")
        self.assertEqual(len(block.items), len(plan.step_list))
        self.assertEqual(
            [item.role for item in block.items],
            [ROLE_CURRENT, ROLE_CURRENT, ROLE_PENDING],
        )

    def test_plan_content_blocks_default_marks_single_frontier_head(self) -> None:
        """Omitting current_numbers keeps the single frontier-head behaviour."""
        plan = PlanSchema(
            step_list=[
                StepSchema(step_number=1, summary="first"),
                StepSchema(step_number=2, summary="second"),
                StepSchema(step_number=3, summary="third"),
            ]
        )

        blocks = plan_content_blocks(plan)

        self.assertEqual(len(blocks), 1)
        block = cast(ItemsBlock, blocks[0])
        self.assertEqual(block.kind, "items")
        self.assertEqual(len(block.items), len(plan.step_list))
        self.assertEqual(
            [item.role for item in block.items],
            [ROLE_CURRENT, ROLE_PENDING, ROLE_PENDING],
        )

    def test_running_steps_state_without_plan_returns_none(self) -> None:
        """A plan-less state yields no plan section at all."""
        node = _make_node()
        state = _HookState(tool_calls=[_stamped_call()])

        self.assertIsNone(node._get_running_steps_state(state))


class _PlanStub:
    """Stand-in plan exposing only ``step_summaries`` for the adapter test."""

    def __init__(self, summaries: dict[int, str]) -> None:
        self._summaries = summaries

    def step_summaries(self) -> dict[int, str]:
        """Return the plan's stored step-summary mapping."""
        return self._summaries


class TestPlanStepSummariesAdapter(unittest.TestCase):
    """The ``plan_step_summaries`` adapter forwards the plan's own mapping."""

    def test_state_without_plan_returns_empty(self) -> None:
        """A state with no plan, or ``plan=None``, yields an empty mapping."""
        self.assertEqual(plan_step_summaries(SimpleNamespace()), {})
        self.assertEqual(plan_step_summaries(SimpleNamespace(plan=None)), {})

    def test_plan_without_method_returns_empty(self) -> None:
        """A plan object that exposes no ``step_summaries`` yields ``{}``."""
        self.assertEqual(plan_step_summaries(SimpleNamespace(plan=object())), {})

    def test_plan_mapping_is_forwarded(self) -> None:
        """A plan exposing ``step_summaries`` returns that mapping."""
        summaries = {1: "first", 2: "second"}
        state = SimpleNamespace(plan=_PlanStub(summaries))
        self.assertEqual(plan_step_summaries(state), summaries)

    def test_returned_mapping_is_a_copy(self) -> None:
        """Mutating the returned mapping leaves the plan's mapping untouched."""
        summaries = {1: "first"}
        stub = _PlanStub(summaries)
        result = plan_step_summaries(SimpleNamespace(plan=stub))
        result[2] = "second"
        self.assertEqual(stub.step_summaries(), {1: "first"})
        self.assertEqual(summaries, {1: "first"})


class TestPlannerPlanStepSummaries(unittest.TestCase):
    """``PlannerPlanSchema.step_summaries`` derives number->summary labels."""

    def test_returns_number_to_summary(self) -> None:
        """A two-step plan maps each step number to its summary."""
        plan = PlannerPlanSchema(
            step_list=[
                StepSchema(step_number=1, summary="read the file"),
                StepSchema(step_number=2, summary="edit the file"),
            ]
        )
        self.assertEqual(
            plan.step_summaries(),
            {1: "read the file", 2: "edit the file"},
        )

    def test_skips_empty_and_whitespace_summaries(self) -> None:
        """A step with an empty or whitespace-only summary is skipped."""
        plan = PlannerPlanSchema(
            step_list=[
                StepSchema(step_number=1, summary="keep"),
                StepSchema(step_number=2, summary=""),
                StepSchema(step_number=3, summary="   "),
            ]
        )
        self.assertEqual(plan.step_summaries(), {1: "keep"})

    def test_no_steps_yields_empty(self) -> None:
        """A plan with no steps yields an empty mapping."""
        plan = PlannerPlanSchema(step_list=[])
        self.assertEqual(plan.step_summaries(), {})


if __name__ == "__main__":
    unittest.main()
