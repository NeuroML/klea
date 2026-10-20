#!/usr/bin/env python3
"""
Tests for the agent's tool-call binder.

File: tests/test_tool_call_binder.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_agent.nodes.tool_call_binder import ToolCallBinder


def test_binder_uses_its_prompt_and_enables_binding_mode():
    binder = ToolCallBinder(
        logger=logging.getLogger("test"),
        label="Binding tool calls",
        llm_models={"tool_picker": object()},
        tools_info={},
    )

    assert binder.prompt_prefix == "ToolCallBinder"
    assert binder._binding_enabled() is True
    assert binder.prompt_registry_location.name == "prompts"
    assert (binder.prompt_registry_location / "ToolCallBinder_system.md").is_file()
    assert "tool-call binder" in binder._load_prompt_file("ToolCallBinder_system")
