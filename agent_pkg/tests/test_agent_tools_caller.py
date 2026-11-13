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
from typing import cast

from klea_agent.nodes.agent_tools_caller import AgentToolsCaller
from klea_agent.schemas import PlanSchema, StepSchema
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
        self.assertEqual(block.items[0].text, "Step 1: only")

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
        self.assertEqual(len(content), 2)

        # The goal text block comes first and stays on a single rendered line.
        goal_block = content[0]
        self.assertNotEqual(goal_block.kind, "items")
        goal_text = _block_text(goal_block)
        self.assertIn(goal, goal_text)
        self.assertNotIn("\n", goal_text)

        # The steps block follows unchanged.
        steps_block = cast(ItemsBlock, content[1])
        self.assertEqual(steps_block.kind, "items")
        self.assertEqual(len(steps_block.items), len(plan.step_list))
        self.assertTrue(steps_block.items)
        self.assertEqual(steps_block.items[0].text, "Step 1: only")

    def test_running_steps_state_without_plan_returns_none(self) -> None:
        """A plan-less state yields no plan section at all."""
        node = _make_node()
        state = _HookState(tool_calls=[_stamped_call()])

        self.assertIsNone(node._get_running_steps_state(state))


if __name__ == "__main__":
    unittest.main()
