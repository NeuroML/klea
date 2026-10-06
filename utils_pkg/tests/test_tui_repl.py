#!/usr/bin/env python3
"""
Tests for the shared TUI REPL's HITL prompt (ADR-0046).

``_prompt_interrupt`` is pure (it only reads ``input()``), so it is tested
without a server or the yaspin dependency.

File: tests/test_tui_repl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import builtins

from klea_utils.ui.tui import repl


def test_review_approve(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "a")
    response, cancel = repl._prompt_interrupt({"kind": "review"}, "klea")
    assert response == {"decision": "approve"}
    assert cancel is False


def test_review_revise_collects_feedback(monkeypatch):
    answers = iter(["r", "use api v2"])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(answers))
    response, cancel = repl._prompt_interrupt({"kind": "review"}, "klea")
    assert response == {"decision": "revise", "feedback": "use api v2"}
    assert cancel is False


def test_review_cancel(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "c")
    response, cancel = repl._prompt_interrupt({"kind": "review"}, "klea")
    assert response is None
    assert cancel is True


def test_input_answers_each_question(monkeypatch):
    answers = iter(["a.txt", "fast"])
    monkeypatch.setattr(builtins, "input", lambda *a, **k: next(answers))
    response, cancel = repl._prompt_interrupt(
        {
            "kind": "input",
            "questions": [
                {"question": "which file?"},
                {"question": "which mode?"},
            ],
        },
        "klea",
    )
    assert response == {"answers": ["a.txt", "fast"]}
    assert cancel is False


def test_input_cancel(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "cancel")
    response, cancel = repl._prompt_interrupt(
        {"kind": "input", "questions": [{"question": "q?"}]}, "klea"
    )
    assert response is None
    assert cancel is True


def test_input_falls_back_to_single_question(monkeypatch):
    monkeypatch.setattr(builtins, "input", lambda *a, **k: "yes")
    response, cancel = repl._prompt_interrupt(
        {"kind": "input", "question": "proceed?"}, "klea"
    )
    assert response == {"answers": ["yes"]}
    assert cancel is False
