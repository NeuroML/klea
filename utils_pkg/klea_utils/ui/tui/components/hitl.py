#!/usr/bin/env python3
"""
HITL review/input modal for the Klea TUI (ADR-0046).

When a run pauses at an interrupt, the app opens this modal to collect the
user's answer.  A ``review`` ask offers Approve / Request changes / Cancel; an
``input`` ask offers one field per question plus Answer / Cancel.  The modal
dismisses with a :class:`HitlDecision` (the resume mapping, or a cancel) that
the app sends back through the same run.

The path-permission ask has its own modal (a later step); this one covers the
review and input kinds.

File: klea_utils/ui/tui/components/hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HitlDecision:
    """The user's answer to a HITL ask.

    :param response: The resume mapping sent to the server, or ``None`` on a
        cancel.
    :param cancel: Whether the user cancelled the interrupt instead of
        answering.
    """

    response: dict[str, Any] | None
    cancel: bool = False


class HitlModal(ModalScreen[HitlDecision]):
    """A modal collecting the answer to a review / input interrupt."""

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    HitlModal {
        align: center middle;
    }
    #hitl-dialog {
        width: 80%;
        height: auto;
        max-height: 80%;
        border: round $primary;
        padding: 1 2;
        background: $surface;
    }
    #hitl-title {
        text-style: bold;
        height: 1;
    }
    #hitl-plan {
        height: auto;
        max-height: 12;
    }
    """

    def __init__(self, ask: dict[str, Any]) -> None:
        """Build the modal for the interrupt *ask*.

        :param ask: The interrupt ``data`` (``kind``, ``question``/
            ``questions``, ``plan``).
        """
        super().__init__()
        self.ask = ask
        self._questions: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        """Compose the review or input form."""
        kind = self.ask.get("kind", "input")
        with Vertical(id="hitl-dialog"):
            yield Static(
                "Plan review" if kind == "review" else "User input", id="hitl-title"
            )
            if kind == "review":
                yield from self._compose_review()
            else:
                yield from self._compose_input()

    def _compose_review(self) -> ComposeResult:
        """Compose the review form (plan, question, feedback, buttons)."""
        plan = self.ask.get("plan")
        if plan:
            yield Static(plan, id="hitl-plan", markup=False)
        question = self.ask.get("question")
        if question:
            yield Static(str(question), id="hitl-question")
        yield Input(
            placeholder="Feedback (required to request changes)", id="hitl-feedback"
        )
        with Horizontal():
            yield Button("Approve", id="hitl-approve", variant="primary")
            yield Button("Request changes", id="hitl-revise")
            yield Button("Cancel", id="hitl-cancel", variant="error")

    def _compose_input(self) -> ComposeResult:
        """Compose the input form (one field per question, buttons)."""
        questions = self.ask.get("questions")
        if not isinstance(questions, list) or not questions:
            fallback = self.ask.get("question") or "More information is needed."
            questions = [{"question": fallback}]
        self._questions = [q for q in questions if isinstance(q, dict)]
        for index, question in enumerate(self._questions):
            yield Static(str(question.get("question", "")))
            yield Input(id=f"hitl-answer-{index}")
        with Horizontal():
            yield Button("Answer", id="hitl-answer", variant="primary")
            yield Button("Cancel", id="hitl-cancel", variant="error")

    def on_mount(self) -> None:
        """Focus the first input field."""
        fields = list(self.query(Input))
        if fields:
            fields[0].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dismiss with the decision for the pressed button.

        :param event: The ``Button.Pressed`` event.
        """
        button_id = event.button.id
        if button_id == "hitl-approve":
            self.dismiss(HitlDecision({"decision": "approve"}))
        elif button_id == "hitl-revise":
            feedback = self.query_one("#hitl-feedback", Input).value or ""
            self.dismiss(HitlDecision({"decision": "revise", "feedback": feedback}))
        elif button_id == "hitl-answer":
            answers = [
                self.query_one(f"#hitl-answer-{index}", Input).value or ""
                for index in range(len(self._questions))
            ]
            self.dismiss(HitlDecision({"answers": answers}))
        elif button_id == "hitl-cancel":
            self.action_cancel()

    def action_cancel(self) -> None:
        """Dismiss as a cancel (Escape or the Cancel button)."""
        self.dismiss(HitlDecision(None, cancel=True))
