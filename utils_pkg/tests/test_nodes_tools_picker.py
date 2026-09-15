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


class AgentLikeState(BaseModel):
    query: str = "q"
    artefacts: dict = Field(default_factory=dict)
    tool_results: list[CallToolResult] = Field(default_factory=list)
    plan: PlanLike = Field(default_factory=PlanLike)
    access_level: str = "full"


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
    assert picker._update_state(ToolCallsSchema(tool_calls=calls), RagLikeState()) == {
        "tool_calls": calls
    }


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
    } <= set(variables)
    assert variables["current_step"] == "do it"


def test_prompt_variables_query_driven_for_rag_state():
    picker = _make_picker()
    variables = picker._get_prompt_variables(RagLikeState(query_domains=["NeuroML"]))
    assert set(variables) == {"tools_description", "query", "observations"}
    assert variables["tools_description"] == "Find models.\n\nRun simulations."


def test_get_status_uses_title_and_formats_arguments():
    picker = _make_picker()
    picker._last_state_updates = {
        "tool_calls": [
            ToolCallSchema(
                tool="get_models",
                args={
                    "search_query": "cortical",
                    "num": 5,
                    "download": False,
                },
                reason="Find relevant models",
            )
        ]
    }

    status = picker._get_status()

    assert status.display == (
        "**Get models from NeuroML-db**\n\n"
        "- `search_query`: `cortical`\n"
        "- `num`: `5`\n"
        "- `download`: `false`"
    )


def test_get_status_falls_back_to_tool_name():
    picker = _make_picker()
    picker._last_state_updates = {"tool_calls": [ToolCallSchema(tool="unknown_tool")]}

    status = picker._get_status()

    assert status.display == "**unknown_tool**\n\n"
