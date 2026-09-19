#!/usr/bin/env python3
"""
Tests for the prompt-section helper (prompt conventions).

File: tests/test_prompt_sections.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from klea_utils.nodes.base import BaseLLMNode


def test_optional_section_omits_when_empty():
    assert BaseLLMNode._optional_section("Outcome details", "") == ""
    assert BaseLLMNode._optional_section("Outcome details", "   ") == ""


def test_optional_section_renders_heading_and_body():
    rendered = BaseLLMNode._optional_section(
        "Outcome details", "Pending question: why?"
    )
    assert rendered == "## Outcome details\n\nPending question: why?"


def test_optional_section_respects_heading_level():
    rendered = BaseLLMNode._optional_section("Detail", "x", level=3)
    assert rendered.startswith("### Detail\n\n")
