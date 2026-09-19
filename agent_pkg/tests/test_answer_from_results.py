#!/usr/bin/env python3
"""
Tests for the agent AnswerFromResults node (ADR-0035).

File: tests/test_answer_from_results.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
import unittest

from klea_agent.nodes.answer_from_results import AnswerFromResults, AnswerSchema
from klea_agent.schemas import GoalSchema, KleaAgentState, PlanSchema, StepSchema


class TestAnswerFromResults(unittest.TestCase):
    """Synthesis writes ``message_for_user``; never judges."""

    def _node(self) -> AnswerFromResults:
        return AnswerFromResults(
            logger=logging.getLogger("test"),
            label="Composing answer",
            llm_models={"chat": object()},
        )

    def _state(self) -> KleaAgentState:
        state = KleaAgentState(query="list files")
        state.goal = GoalSchema(goal="list files", success_criteria="listed")
        state.plan = PlanSchema(
            step_list=[
                StepSchema(
                    step_number=1,
                    description="list files",
                    success_criteria="files listed",
                    status="done",
                )
            ],
            status="completed",
            current_step_index=1,
        )
        return state

    def test_writes_message_from_answer(self):
        update = self._node()._update_state(
            AnswerSchema(answer="here you go"), self._state()
        )
        self.assertEqual(update["message_for_user"], "here you go")

    def test_empty_answer_falls_back_to_step_description(self):
        update = self._node()._update_state(AnswerSchema(), self._state())
        self.assertIn("list files", update["message_for_user"])

    def test_prompt_variables_include_plan_and_goal(self):
        variables = self._node()._get_prompt_variables(self._state())
        self.assertIn("list files", variables["goal"])
        self.assertIn("list files", variables["plan"])
        self.assertIn("observations", variables)
        self.assertEqual(variables["outcome"], "success")

    def _failed_state(self) -> KleaAgentState:
        state = KleaAgentState(query="do x")
        state.plan = PlanSchema(status="aborted")
        state.failure_reason = "tool-round budget exhausted"
        return state

    def test_failure_outcome_in_prompt_variables(self):
        variables = self._node()._get_prompt_variables(self._failed_state())
        self.assertEqual(variables["outcome"], "failure")
        self.assertEqual(variables["failure_reason"], "tool-round budget exhausted")

    def test_failure_fallback_mentions_reason(self):
        answer = self._node()._fallback_answer(self._failed_state())
        self.assertIn("could not complete", answer)
        self.assertIn("tool-round budget exhausted", answer)

    def test_unplannable_plan_is_failure(self):
        state = KleaAgentState(query="do x")
        state.plan = PlanSchema(status="unplannable")
        self.assertTrue(self._node()._is_failure(state))

    def _needs_input_state(self) -> KleaAgentState:
        state = KleaAgentState(query="do x")
        state.plan = PlanSchema(status="needs_input")
        state.pending_question = "which file?"
        return state

    def test_needs_input_outcome_in_prompt_variables(self):
        variables = self._node()._get_prompt_variables(self._needs_input_state())
        self.assertEqual(variables["outcome"], "needs_input")
        self.assertEqual(variables["pending_question"], "which file?")

    def test_needs_input_is_not_failure(self):
        self.assertFalse(self._node()._is_failure(self._needs_input_state()))

    def test_needs_input_fallback_asks_question(self):
        answer = self._node()._fallback_answer(self._needs_input_state())
        self.assertIn("which file?", answer)

    def test_success_persists_deliverable_artefact(self):
        update = self._node()._update_state(
            AnswerSchema(answer="here you go"), self._state()
        )
        artefacts = update["artefacts"]
        assert len(artefacts) == 1
        artefact = next(iter(artefacts.values()))
        assert artefact.type_ == "result"
        assert "list files" in artefact.content
        assert artefact.metadata["goal"] == "list files"

    def test_same_goal_supersedes_artefact(self):
        node = self._node()
        first = node._update_state(AnswerSchema(answer="a"), self._state())
        state = self._state()
        state.artefacts = first["artefacts"]
        second = node._update_state(AnswerSchema(answer="b"), state)
        assert len(second["artefacts"]) == 1
        assert "b" in next(iter(second["artefacts"].values())).content

    def test_failure_does_not_persist_artefact(self):
        update = self._node()._update_state(
            AnswerSchema(answer="failed"), self._failed_state()
        )
        assert "artefacts" not in update

    def test_needs_input_does_not_persist_artefact(self):
        update = self._node()._update_state(
            AnswerSchema(answer="question"), self._needs_input_state()
        )
        assert "artefacts" not in update


if __name__ == "__main__":
    unittest.main()
