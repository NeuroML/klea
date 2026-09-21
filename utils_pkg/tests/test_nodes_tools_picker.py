#!/usr/bin/env python3
"""
Tests for the shared MCP tools picker node.

File: utils_pkg/tests/test_nodes_tools_picker.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastmcp.client.client import CallToolResult
from klea_utils.mcp.schemas import ToolCallSchema, ToolCallsSchema, ToolInfo
from klea_utils.nodes.tools_picker import ToolsPicker
from mcp.types import TextContent
from pydantic import BaseModel, Field

TOOLS_INFO = {
    "NeuroML": {
        "get_models": ToolInfo(
            title="Get models from NeuroML-db",
            description="Find models.",
        ),
        "run_simulation": ToolInfo(
            title="Run a simulation",
            description="Run simulations.",
        ),
    },
    "Other": {
        "other_tool": ToolInfo(
            title="Other tool",
            description="Other description.",
        )
    },
}


class RagLikeState(BaseModel):
    query: str = "q"
    query_domains: list[str] = Field(default_factory=list)
    tool_results: list[CallToolResult] = Field(default_factory=list)
    access_level: str = "full"


class Step(BaseModel):
    step_number: int = 1
    description: str = "do it"
    status: str = "pending"

    def render(self, *, current: bool = False) -> str:
        return self.description


class PlanLike(BaseModel):
    current_step_index: int = 0
    step_list: list[Step] = Field(default_factory=list)

    def current_step(self) -> Step | None:
        if 0 <= self.current_step_index < len(self.step_list):
            return self.step_list[self.current_step_index]
        return None

    def next_batch(self, max_steps: int | None = None) -> list[Step]:
        # The double models a single current step (its own index).
        current = self.current_step()
        return [current] if current is not None else []


class AgentLikeState(BaseModel):
    query: str = "q"
    artefacts: dict = Field(default_factory=dict)
    tool_calls: list[ToolCallSchema] = Field(default_factory=list)
    tool_results: list[CallToolResult] = Field(default_factory=list)
    plan: PlanLike = Field(default_factory=PlanLike)
    access_level: str = "full"
    picker_attempts: int = 0
    picker_step: int = -1

    def observations_text(self) -> str:
        return "rendered observations"


def _make_picker(**kwargs) -> ToolsPicker:
    kwargs.setdefault("tools_info", TOOLS_INFO)
    kwargs.setdefault("model_type", "chat")
    return ToolsPicker(
        logger=logging.getLogger("test"),
        label="Selecting tools",
        llm_models={"chat": object(), "plan": object()},
        **kwargs,
    )


def test_get_tool_descriptions_filters_by_domain():
    picker = _make_picker()
    descriptions = picker._get_tool_descriptions(
        RagLikeState(query_domains=["NeuroML"])
    )
    assert descriptions == "Find models.\n\nRun simulations."
    assert "Other description." not in descriptions


def test_get_tool_descriptions_includes_all_without_domains():
    picker = _make_picker()
    descriptions = picker._get_tool_descriptions(AgentLikeState())
    assert descriptions == "Find models.\n\nRun simulations.\n\nOther description."


def test_get_tool_descriptions_unknown_domain_is_empty():
    picker = _make_picker()
    assert picker._get_tool_descriptions(RagLikeState(query_domains=["nope"])) == ""


ACCESS_TOOLS = {
    "d": {
        "read": ToolInfo(description="read tool", read_only=True),
        "delete": ToolInfo(description="delete tool", destructive=True),
        "plain": ToolInfo(description="plain tool"),
    }
}


def test_get_tool_descriptions_read_only_hides_disallowed():
    """read_only discloses only explicitly read-only tools (ADR-0037)."""
    picker = _make_picker(tools_info=ACCESS_TOOLS)
    state = AgentLikeState(access_level="read_only")
    assert picker._get_tool_descriptions(state) == "read tool"


def test_get_tool_descriptions_full_includes_all():
    picker = _make_picker(tools_info=ACCESS_TOOLS)
    state = AgentLikeState(access_level="full")
    descriptions = picker._get_tool_descriptions(state)
    assert "read tool" in descriptions
    assert "delete tool" in descriptions
    assert "plain tool" in descriptions


def test_pre_exec_skips_when_access_level_hides_all():
    picker = _make_picker(
        tools_info={
            "d": {"delete": ToolInfo(description="delete tool", destructive=True)}
        }
    )
    assert picker._pre_exec(AgentLikeState(access_level="read_only")) is False


def test_pre_exec_skips_when_no_tools_for_domain():
    picker = _make_picker()
    assert picker._pre_exec(RagLikeState(query_domains=["nope"])) is False
    assert picker._pre_exec(RagLikeState(query_domains=["NeuroML"])) is True


def test_update_state_writes_tool_calls():
    picker = _make_picker()
    calls = [ToolCallSchema(tool="get_models", args={"num": 3})]
    update = picker._update_state(ToolCallsSchema(tool_calls=calls), RagLikeState())
    assert update["tool_calls"] == calls
    # RAG-like state has no picker counter fields; they are simply absent.
    assert "picker_attempts" not in update


def test_default_error_result_is_empty_tool_calls():
    picker = _make_picker()
    assert picker._get_default_error_result() == ToolCallsSchema()


def test_prompt_variables_superset_for_agent_state():
    picker = _make_picker(model_type="plan")
    variables = picker._get_prompt_variables(
        AgentLikeState(plan=PlanLike(step_list=[Step()]))
    )
    assert {
        "tools_description",
        "query",
        "artefacts",
        "observations",
        "current_step",
        "picker_feedback",
    } <= set(variables)
    assert variables["current_step"] == "do it"
    # No feedback -> the whole section (heading included) is omitted.
    assert variables["picker_feedback"] == ""


def test_prompt_variables_query_driven_for_rag_state():
    picker = _make_picker()
    variables = picker._get_prompt_variables(RagLikeState(query_domains=["NeuroML"]))
    assert set(variables) == {
        "tools_description",
        "query",
        "observations",
        "picker_feedback",
    }
    assert variables["tools_description"] == "Find models.\n\nRun simulations."


def test_empty_selection_counts_up_and_adds_feedback():
    """Empty picks increment the state counter and set prompt feedback."""
    picker = _make_picker()
    state = AgentLikeState(plan=PlanLike(step_list=[Step()]))

    update = picker._update_state(ToolCallsSchema(tool_calls=[]), state)
    assert update["picker_attempts"] == 1
    assert update["picker_step"] == 1

    # The prompt feedback is derived from the incoming state counter.
    state_after = AgentLikeState(
        plan=PlanLike(step_list=[Step()]), picker_attempts=1, picker_step=1
    )
    assert (
        "no usable tool call"
        in picker._get_prompt_variables(state_after)["picker_feedback"]
    )

    update2 = picker._update_state(
        ToolCallsSchema(tool_calls=[]),
        AgentLikeState(
            plan=PlanLike(step_list=[Step()]), picker_attempts=1, picker_step=1
        ),
    )
    assert update2["picker_attempts"] == 2


def test_tool_error_adds_its_text_to_feedback():
    """A failed batch surfaces the error text so the picker can correct it."""
    picker = _make_picker()
    error = CallToolResult(
        content=[TextContent(type="text", text="old_string matched 2 times")],
        structured_content=None,
        meta=None,
        data=None,
        is_error=True,
    )
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]),
        tool_results=[error],
        picker_attempts=0,
        picker_step=0,
    )
    feedback = picker._get_prompt_variables(state)["picker_feedback"]
    assert "old_string matched 2 times" in feedback
    # Retries are arguments-only on the same tool; no switching.
    assert "same tool" in feedback
    assert "do not switch" in feedback.lower()
    # The heading renders only when there is feedback.
    assert feedback.startswith("## Feedback on your previous selection")


def test_no_error_keeps_empty_selection_feedback():
    """Without a tool error, only the empty-selection message is used."""
    picker = _make_picker()
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]), picker_attempts=1, picker_step=0
    )
    feedback = picker._get_prompt_variables(state)["picker_feedback"]
    assert "no usable tool call" in feedback


def test_non_empty_selection_resets_attempts_and_feedback():
    picker = _make_picker()
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]), picker_attempts=2, picker_step=0
    )

    update = picker._update_state(
        ToolCallsSchema(tool_calls=[ToolCallSchema(tool="get_models")]), state
    )
    assert update["picker_attempts"] == 0
    # The feedback is derived from the state that carried the reset.
    post = AgentLikeState(
        plan=PlanLike(step_list=[Step()]),
        picker_attempts=update["picker_attempts"],
        picker_step=update["picker_step"],
    )
    assert picker._get_prompt_variables(post)["picker_feedback"] == ""


def test_attempts_reset_when_step_changes():
    picker = _make_picker()
    update = picker._update_state(
        ToolCallsSchema(tool_calls=[]),
        AgentLikeState(
            plan=PlanLike(step_list=[Step()]), picker_attempts=3, picker_step=1
        ),
    )
    assert update["picker_attempts"] == 4
    assert update["picker_step"] == 1

    # A new step (number 2) resets the counter to 1.
    update2 = picker._update_state(
        ToolCallsSchema(tool_calls=[]),
        AgentLikeState(
            plan=PlanLike(
                current_step_index=1,
                step_list=[Step(step_number=1), Step(step_number=2)],
            ),
            picker_attempts=3,
            picker_step=1,
        ),
    )
    assert update2["picker_attempts"] == 1
    assert update2["picker_step"] == 2


def test_empty_name_list_counts_as_no_usable_call():
    """A list whose calls all have empty/whitespace names is a failed pick."""
    picker = _make_picker()
    state = AgentLikeState(plan=PlanLike(step_list=[Step()]))
    update = picker._update_state(
        ToolCallsSchema(
            tool_calls=[ToolCallSchema(tool=""), ToolCallSchema(tool="  ")]
        ),
        state,
    )
    assert update["picker_attempts"] == 1


def test_one_usable_name_resets_attempts():
    """A list with at least one non-empty name is a successful pick."""
    picker = _make_picker()
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]), picker_attempts=2, picker_step=0
    )
    update = picker._update_state(
        ToolCallsSchema(
            tool_calls=[ToolCallSchema(tool=""), ToolCallSchema(tool="get_models")]
        ),
        state,
    )
    assert update["picker_attempts"] == 0


def test_observations_use_rendered_text_for_agent_state():
    """The picker sees all step outputs, not just the last batch (agent)."""
    picker = _make_picker()
    variables = picker._get_prompt_variables(AgentLikeState())
    assert variables["observations"] == "rendered observations"


def test_observations_fall_back_to_tool_results_without_renderer():
    """RAG state has no ``observations_text``, so raw results are passed."""
    picker = _make_picker()
    variables = picker._get_prompt_variables(RagLikeState())
    assert variables["observations"] == []


def test_deliberate_failure_invokes_on_unusable():
    """An empty-name call with a reason is handed to the app callback."""
    seen: dict[str, str] = {}

    def on_unusable(state, reason):
        seen["reason"] = reason
        return {"replan_reason": reason}

    picker = _make_picker(on_unusable=on_unusable)
    update = picker._update_state(
        ToolCallsSchema(tool_calls=[ToolCallSchema(tool="", reason="cannot do it")]),
        AgentLikeState(plan=PlanLike(step_list=[Step()])),
    )
    assert seen["reason"] == "cannot do it"
    assert update["replan_reason"] == "cannot do it"


def test_empty_list_does_not_invoke_on_unusable():
    """A bare empty list is an emission glitch, not a deliberate failure."""
    calls: list[str] = []
    picker = _make_picker(on_unusable=lambda state, reason: calls.append(reason) or {})
    update = picker._update_state(
        ToolCallsSchema(tool_calls=[]),
        AgentLikeState(plan=PlanLike(step_list=[Step()])),
    )
    assert calls == []
    assert "replan_reason" not in update


def _error_result(text: str = "boom") -> CallToolResult:
    """Return an ``is_error`` tool result carrying *text*."""
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=None,
        meta=None,
        data=None,
        is_error=True,
    )


def test_identical_failed_retry_is_escalated():
    """Repeating the exact failed call escalates instead of dispatching again."""
    seen: dict[str, str] = {}

    def on_unusable(state, reason):
        seen["reason"] = reason
        return {"replan_reason": reason}

    picker = _make_picker(on_unusable=on_unusable)
    call = ToolCallSchema(tool="read_file", args={"path": "missing.txt"})
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]),
        tool_calls=[call],
        tool_results=[_error_result("Not a file: missing.txt")],
    )

    update = picker._update_state(
        ToolCallsSchema(tool_calls=[ToolCallSchema(tool=call.tool, args=call.args)]),
        state,
    )

    assert "identical" in seen["reason"]
    # The dispatched selection is replaced by the empty-name escalation call.
    assert update["tool_calls"][0].tool == ""
    assert "identical" in update["replan_reason"]


def test_corrected_args_after_failure_are_dispatched():
    """A changed argument binds normally; the guard does not fire."""
    calls: list[str] = []
    picker = _make_picker(on_unusable=lambda state, reason: calls.append(reason) or {})
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]),
        tool_calls=[ToolCallSchema(tool="read_file", args={"path": "a.txt"})],
        tool_results=[_error_result("Not a file: a.txt")],
    )
    new = [ToolCallSchema(tool="read_file", args={"path": "b.txt"})]

    update = picker._update_state(ToolCallsSchema(tool_calls=new), state)

    assert calls == []
    assert update["tool_calls"] == new
    assert update["picker_attempts"] == 0


def test_identical_repeat_after_success_is_allowed():
    """A repeat is only blocked when the previous batch actually failed."""
    calls: list[str] = []
    picker = _make_picker(on_unusable=lambda state, reason: calls.append(reason) or {})
    call = ToolCallSchema(tool="read_file", args={"path": "a.txt"})
    state = AgentLikeState(
        plan=PlanLike(step_list=[Step()]),
        tool_calls=[call],
        tool_results=[
            CallToolResult(
                content=[],
                structured_content=None,
                meta=None,
                data=None,
                is_error=False,
            )
        ],
    )

    update = picker._update_state(ToolCallsSchema(tool_calls=[call]), state)

    assert calls == []
    assert update["tool_calls"] == [call]


def test_identical_repeat_without_on_unusable_is_allowed():
    """The guard is inert without an app callback (RAG has no replan edge)."""
    picker = _make_picker()
    call = ToolCallSchema(tool="read_file", args={"path": "a.txt"})
    state = RagLikeState(tool_results=[_error_result("boom")])

    update = picker._update_state(ToolCallsSchema(tool_calls=[call]), state)

    assert update["tool_calls"] == [call]


def test_usable_calls_are_stamped_with_the_current_step():
    """The agent picker stamps each call with its originating step (ADR-0041)."""
    picker = _make_picker()
    state = AgentLikeState(plan=PlanLike(step_list=[Step(step_number=3)]))

    update = picker._update_state(
        ToolCallsSchema(tool_calls=[ToolCallSchema(tool="get_models")]), state
    )

    assert update["tool_calls"][0].step == 3


def test_rag_calls_keep_step_zero():
    """RAG has no plan, so its calls are not stamped."""
    picker = _make_picker()
    update = picker._update_state(
        ToolCallsSchema(tool_calls=[ToolCallSchema(tool="get_models")]), RagLikeState()
    )
    assert update["tool_calls"][0].step == 0


def test_calls_are_attributed_to_batch_steps_with_fallback():
    """Each call keeps its batch step; an invalid step falls back to the first."""

    class MultiPlan(PlanLike):
        def next_batch(self, max_steps: int | None = None) -> list[Step]:
            return self.step_list

    picker = _make_picker()
    state = AgentLikeState(
        plan=MultiPlan(step_list=[Step(step_number=1), Step(step_number=2)])
    )

    update = picker._update_state(
        ToolCallsSchema(
            tool_calls=[
                ToolCallSchema(tool="get_models", step=2),
                ToolCallSchema(tool="other_tool"),  # no step -> first batch step
                ToolCallSchema(tool="get_models", step=9),  # invalid -> first
            ]
        ),
        state,
    )

    assert [call.step for call in update["tool_calls"]] == [2, 1, 1]
