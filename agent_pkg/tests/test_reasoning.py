#!/usr/bin/env python3
"""
Tests for the agent ReasoningNode (ADR-0035 update 2026-09-19).

File: tests/test_reasoning.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import unittest

from klea_agent.nodes.reasoning import ReasoningNode
from klea_agent.schemas import (
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    ReasoningSchema,
    StepSchema,
)
from klea_utils.mcp.schemas import ToolCallSchema


class TestReasoningNode(unittest.TestCase):
    """A reasoning step records a text conclusion as a step output."""

    def _node(self) -> ReasoningNode:
        return ReasoningNode(
            logger=logging.getLogger("test"),
            label="Reasoning",
            llm_models={"chat": object()},
        )

    def _state(self) -> KleaAgentState:
        state = KleaAgentState(query="generate a hypothesis")
        state.goal = GoalSchema(goal="generate a hypothesis", success_criteria="stated")
        state.plan = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="form a hypothesis from the observations",
                    success_criteria="the hypothesis is stated",
                    kind="reasoning",
                )
            ],
            status="in_progress",
            current_step_index=0,
        )
        return state

    def test_records_conclusion_as_step_output(self):
        update = self._node()._update_state(
            ReasoningSchema(conclusion="H1: X causes Y", rationale="from obs"),
            self._state(),
        )
        entries = update["step_outputs"][1]
        assert len(entries) == 1
        assert entries[0].result == "H1: X causes Y"
        assert entries[0].tool == ""

    def test_conclusion_renders_as_reasoning(self):
        update = self._node()._update_state(
            ReasoningSchema(conclusion="H1: X causes Y"), self._state()
        )
        rendered = update["step_outputs"][1][0].render()
        assert rendered.startswith("### reasoning (displayed_to_user: no)")
        assert "H1: X causes Y" in rendered

    def test_appends_ai_message_with_rationale(self):
        update = self._node()._update_state(
            ReasoningSchema(conclusion="H1", rationale="because obs"), self._state()
        )
        content = update["messages"][-1].content
        assert "Reasoning (step 1): H1" in content
        assert "Rationale: because obs" in content

    def test_clears_stale_tool_calls_and_results(self):
        """A reasoning step must not report the previous tool step's calls."""
        state = self._state()
        state.tool_calls = [ToolCallSchema(tool="read_file")]
        state.tool_results = []
        update = self._node()._update_state(ReasoningSchema(conclusion="H1"), state)
        assert update["tool_calls"] == []
        assert update["tool_results"] == []

    def test_pre_exec_requires_a_current_step(self):
        node = self._node()
        assert node._pre_exec(self._state()) is True
        assert node._pre_exec(KleaAgentState()) is False

    def test_prompt_variables_include_current_step(self):
        variables = self._node()._get_prompt_variables(self._state())
        assert "form a hypothesis" in variables["current_step"]
        assert "generate a hypothesis" in variables["goal"]
        assert "observations" in variables

    def test_default_error_result_is_empty(self):
        assert self._node()._get_default_error_result() == ReasoningSchema()


if __name__ == "__main__":
    unittest.main()
