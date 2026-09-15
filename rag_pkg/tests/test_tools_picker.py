#!/usr/bin/env python3
"""
Tests for RAG tool selection presentation.

File: rag_pkg/tests/test_tools_picker.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo
from klea_utils.nodes.tools_picker import ToolsPicker
from pydantic import BaseModel, Field


class _TestState(BaseModel):
    query_domains: list[str] = Field(default_factory=list)


def _make_picker() -> ToolsPicker:
    picker = object.__new__(ToolsPicker)
    # ``__init__`` is skipped to avoid building the LLM node; supply the
    # logger that every real node has (the node logs its disclosure).
    picker.logger = logging.getLogger("test")
    picker._tools_info = {
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
    return picker


def test_get_tool_descriptions_filters_by_domain() -> None:
    picker = _make_picker()

    descriptions = picker._get_tool_descriptions(_TestState(query_domains=["NeuroML"]))

    assert descriptions == "Find models.\n\nRun simulations."
    assert "Other description." not in descriptions


def test_get_tool_descriptions_no_domains_includes_all() -> None:
    picker = _make_picker()

    descriptions = picker._get_tool_descriptions(_TestState())

    assert descriptions == "Find models.\n\nRun simulations.\n\nOther description."


def test_get_status_uses_title_and_formats_arguments() -> None:
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
    assert status.details == {}


def test_get_status_falls_back_to_tool_name() -> None:
    picker = _make_picker()
    picker._last_state_updates = {"tool_calls": [ToolCallSchema(tool="unknown_tool")]}

    status = picker._get_status()

    assert status.display == "**unknown_tool**\n\n"
