#!/usr/bin/env python3
"""
Tests for HITL transcript rendering.

File: tests/test_api_hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from klea_utils.api.hitl import _interrupt_event_question, _interrupt_question


class _Interrupt:
    """Minimal stand-in for ``langgraph.types.Interrupt``."""

    def __init__(self, value: dict):
        self.value = value


def _input_payload() -> dict:
    return {
        "kind": "input",
        "sections": [
            {
                "title": "Plan version 1 (abandoned)",
                "body": "Abandoned: tool failed\nSteps:\n[FAILED] 1. read the file",
            }
        ],
        "questions": [
            {"step_number": 2, "question": "How should I proceed?"},
        ],
    }


def test_input_interrupt_includes_sections_before_questions():
    text = _interrupt_question(_Interrupt(_input_payload()))

    assert text.startswith("Plan version 1 (abandoned)\nAbandoned: tool failed")
    assert text.endswith("How should I proceed?")


def test_input_interrupt_without_sections_keeps_questions_only():
    payload = _input_payload()
    payload.pop("sections")

    assert _interrupt_question(_Interrupt(payload)) == "How should I proceed?"


def test_review_interrupt_does_not_duplicate_sections():
    payload = {
        "kind": "review",
        "question": "Approve, request changes, or cancel.",
        "sections": [{"title": "Plan", "body": "(plan)"}],
    }

    assert (
        _interrupt_question(_Interrupt(payload))
        == "Approve, request changes, or cancel."
    )


def test_input_event_includes_sections_before_questions():
    text = _interrupt_event_question({"data": _input_payload()})

    assert text.startswith("Plan version 1 (abandoned)\nAbandoned: tool failed")
    assert text.endswith("How should I proceed?")
