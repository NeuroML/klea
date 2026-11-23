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
        "questions": [
            {"step_number": 2, "question": "How should I proceed?"},
        ],
    }


def test_input_interrupt_renders_step_context_without_plan_history():
    payload = _input_payload()
    payload["questions"] = [
        {
            "step_number": 1,
            "summary": "read the file",
            "question": "How should I proceed?",
        }
    ]

    text = _interrupt_question(_Interrupt(payload))

    assert text == "Step 1: read the file\nHow should I proceed?"
    assert "Plan version" not in text
    assert "Abandoned" not in text


def test_input_interrupt_without_step_context_keeps_the_question_only():
    payload = _input_payload()
    payload["questions"] = [{"question": "How should I proceed?"}]

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


def test_input_event_renders_step_context_without_plan_history():
    payload = _input_payload()
    payload["questions"] = [
        {
            "step_number": 1,
            "summary": "read the file",
            "question": "How should I proceed?",
        }
    ]

    text = _interrupt_event_question({"data": payload})

    assert text == "Step 1: read the file\nHow should I proceed?"
    assert "Plan version" not in text
