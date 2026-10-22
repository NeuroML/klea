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
from klea_agent.schemas import (
    ArtefactSchema,
    GoalSchema,
    KleaAgentState,
    PlanSchema,
    StepSchema,
)
from klea_utils.llm import LLMModel
from klea_utils.nodes.context import LLMNodeContext
from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate


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
        )
        return state

    def test_writes_message_from_answer(self):
        update = self._node()._update_state(
            AnswerSchema(answer="here you go"), self._state(), LLMNodeContext()
        )
        self.assertEqual(update["message_for_user"], "here you go")

    def test_empty_answer_falls_back_to_step_description(self):
        update = self._node()._update_state(
            AnswerSchema(), self._state(), LLMNodeContext()
        )
        self.assertIn("list files", update["message_for_user"])

    def test_writes_bounded_continuation_evidence(self):
        update = self._node()._update_state(
            AnswerSchema(answer="here you go"), self._state(), LLMNodeContext()
        )

        evidence = update["continuation_evidence"]
        self.assertEqual(evidence.goal, "list files")
        self.assertEqual(evidence.final_answer, "here you go")
        self.assertEqual(evidence.steps[0].step_number, 1)
        self.assertEqual(evidence.steps[0].status, "done")

    def test_prompt_variables_include_plan_and_goal(self):
        variables = self._node()._get_prompt_variables(self._state(), LLMNodeContext())
        self.assertIn("list files", variables["goal"])
        self.assertIn("list files", variables["plan"])
        self.assertIn("observations", variables)
        self.assertEqual(variables["outcome"], "success")
        # On success the outcome-detail block is omitted entirely.
        self.assertEqual(variables["outcome_details"], "")

    def test_prompt_includes_rendered_discovery(self):
        state = self._state()
        state.discovery_persistent.upsert("AGENTS.md", "use uv")
        variables = self._node()._get_prompt_variables(state, LLMNodeContext())
        self.assertIn("### AGENTS.md", variables["discovery"])
        self.assertIn("use uv", variables["discovery"])

    def test_prompt_variables_include_artefacts(self):
        state = self._state()
        state.artefacts["earlier"] = ArtefactSchema(
            id_="earlier", type_="result", content="earlier finding X"
        )
        variables = self._node()._get_prompt_variables(state, LLMNodeContext())
        self.assertIn("earlier finding X", variables["artefacts"])

    def test_prompt_artefacts_sentinel_when_empty(self):
        variables = self._node()._get_prompt_variables(self._state(), LLMNodeContext())
        self.assertEqual(variables["artefacts"], "(no artefacts)")

    def test_user_prompt_renders_artefacts_section(self):
        state = self._state()
        state.artefacts["earlier"] = ArtefactSchema(
            id_="earlier", type_="result", content="earlier finding X"
        )
        node = self._node()
        variables = node._get_prompt_variables(state, LLMNodeContext())
        template = ChatPromptTemplate(
            [("human", node._load_prompt_file("AnswerFromResults_user"))]
        )
        text = template.format_messages(**variables)[0].content
        self.assertIn("Earlier results (artefacts)", text)
        self.assertIn("earlier finding X", text)

    def test_prompt_omits_plan_history_for_single_plan(self):
        variables = self._node()._get_prompt_variables(self._state(), LLMNodeContext())
        self.assertEqual(variables["plan_history_block"], "")

    def test_prompt_includes_plan_history_after_review(self):
        state = self._state()
        state.plan.plan_version = 2
        state.plan.human_feedback_rounds = 1
        variables = self._node()._get_prompt_variables(state, LLMNodeContext())
        self.assertIn("## Plan history", variables["plan_history_block"])
        self.assertIn("version 2", variables["plan_history_block"])
        self.assertIn("1 human review round(s)", variables["plan_history_block"])

    def _failed_state(self) -> KleaAgentState:
        state = KleaAgentState(query="do x")
        state.plan = PlanSchema(status="aborted")
        state.failure_reason = "tool-round budget exhausted"
        return state

    def test_failure_outcome_in_prompt_variables(self):
        variables = self._node()._get_prompt_variables(
            self._failed_state(), LLMNodeContext()
        )
        self.assertEqual(variables["outcome"], "failure")
        self.assertEqual(
            variables["outcome_details"], "Failure reason: tool-round budget exhausted"
        )

    def test_failure_fallback_mentions_reason(self):
        answer = self._node()._fallback_answer(self._failed_state())
        self.assertIn("could not complete", answer)
        self.assertIn("tool-round budget exhausted", answer)

    def test_unplannable_plan_is_failure(self):
        state = KleaAgentState(query="do x")
        state.plan = PlanSchema(status="unplannable")
        self.assertTrue(self._node()._is_failure(state))

    def test_success_persists_deliverable_artefact(self):
        update = self._node()._update_state(
            AnswerSchema(answer="here you go"), self._state(), LLMNodeContext()
        )
        artefacts = update["artefacts"]
        assert len(artefacts) == 1
        artefact = next(iter(artefacts.values()))
        assert artefact.type_ == "result"
        assert "list files" in artefact.content
        assert artefact.metadata["goal"] == "list files"

    def test_same_goal_supersedes_artefact(self):
        node = self._node()
        first = node._update_state(
            AnswerSchema(answer="a"), self._state(), LLMNodeContext()
        )
        state = self._state()
        state.artefacts = first["artefacts"]
        second = node._update_state(AnswerSchema(answer="b"), state, LLMNodeContext())
        assert len(second["artefacts"]) == 1
        assert "b" in next(iter(second["artefacts"].values())).content

    def test_superseded_artefact_moves_to_most_recent(self):
        """A re-run counts as a fresh write for the bounded prompt block."""
        node = self._node()
        first = node._update_state(
            AnswerSchema(answer="first"), self._state(), LLMNodeContext()
        )
        state = self._state()
        state.artefacts = {
            **first["artefacts"],
            "other": ArtefactSchema(id_="other", content="other result"),
        }
        second = node._update_state(
            AnswerSchema(answer="second"), state, LLMNodeContext()
        )
        # The re-authored goal artefact is now the last (newest) entry.
        assert list(second["artefacts"]) == ["other", "list-files"]

    def test_failure_does_not_persist_artefact(self):
        update = self._node()._update_state(
            AnswerSchema(answer="failed"), self._failed_state(), LLMNodeContext()
        )
        assert "artefacts" not in update

    def test_only_deliverable_is_persisted_not_observations(self):
        """Artefacts hold the concise result, not the step observations.

        The full reply also reaches ``messages`` via ``AnswerUser`` (lossy
        continuity); only the deliverable is session-scoped, so intermediate
        tool/reasoning outputs (``step_outputs``) must not leak into
        ``artefacts``.
        """
        state = self._state()
        state.step_outputs = {1: []}
        update = self._node()._update_state(
            AnswerSchema(answer="the concise result"), state, LLMNodeContext()
        )
        artefact = next(iter(update["artefacts"].values()))
        assert "the concise result" in artefact.content
        assert "step_outputs" not in artefact.content
        # The reply itself is delivered via message_for_user (-> messages).
        assert update["message_for_user"] == "the concise result"

    def test_system_prompt_reports_model_identity(self):
        """The answer-composer can answer which model is replying."""
        node = AnswerFromResults(
            logger=logging.getLogger("test"),
            label="Composing answer",
            llm_models={"chat": LLMModel(instance=None, model_name="openai:gpt-4o")},
        )
        prompt = node._get_system_prompt(self._state(), LLMNodeContext())
        assert isinstance(prompt, str)
        assert "Model in use: openai:gpt-4o" in prompt

    def test_prose_reply_is_recovered_as_the_answer(self):
        """A prose reply that fails JSON parsing is used as the answer."""
        result = self._node()._process_output(
            LLMNodeContext(
                output=AIMessage(content="The tests now pass on macOS."),
                output_schema=AnswerSchema,
            )
        )
        assert result == AnswerSchema(answer="The tests now pass on macOS.")

    def test_prose_recovery_strips_thought_prefix(self):
        """Only the text after ``</think>`` becomes the recovered answer."""
        result = self._node()._process_output(
            LLMNodeContext(
                output=AIMessage(content="internal thought</think>The result."),
                output_schema=AnswerSchema,
            )
        )
        assert result == AnswerSchema(answer="The result.")

    def test_broken_json_is_not_recovered_as_an_answer(self):
        """Broken JSON-looking output falls back to an empty result."""
        result = self._node()._get_default_error_result(
            LLMNodeContext(output=AIMessage(content='{"answer": '))
        )
        assert result == AnswerSchema()

    def test_empty_answer_falls_back_to_plan_with_status(self):
        """Empty synthesis shows the plan and its outcome, not tool results."""
        state = self._state()
        update = self._node()._update_state(AnswerSchema(), state, LLMNodeContext())
        answer = update["message_for_user"]
        assert "The task plan completed successfully." in answer
        assert "list files" in answer
        assert "## Tool Results" not in answer

    def test_failure_fallback_shows_plan_and_reason(self):
        """A failed synthesis reports the reason and renders the plan."""
        state = self._failed_state()
        answer = self._node()._fallback_answer(state)
        assert "tool-round budget exhausted" in answer
        assert "aborted" in answer.lower()


if __name__ == "__main__":
    unittest.main()
