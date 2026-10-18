#!/usr/bin/env python3
"""
HITL review/input/permission panels for the Klea TUI (ADR-0046).

When a run pauses at an interrupt, the app mounts an inline panel in the chat
panel rather than a modal, so the transcript stays scrollable while the user
reads the ask and decides.  A ``review`` ask offers Approve / Request changes /
Cancel; an ``input`` ask offers one field per question plus Answer / Cancel; a
``permission`` ask offers once/session/not-a-path/deny per requested path
(ADR-0007).

Each panel posts :class:`HitlDecisionPanel.Decided` (carrying a
:class:`HitlDecision`) instead of dismissing; the app removes the panel and
resumes the same run.

File: klea_utils/ui/tui/components/hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Input, RadioButton, RadioSet, Static

logger = logging.getLogger(__name__)

#: Path-permission decision options and their labels.  "Not a path" is offered
#: only for a ``guess``-tier request (see :meth:`PermissionPanel._options`).
_PERMISSION_OPTION_LABELS = {
    "once": "Once",
    "session": "Session",
    "not_a_path": "Not a path",
    "deny": "Deny",
}


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


class HitlDecisionPanel(VerticalScroll):
    """Base for the inline HITL panels.

    A panel owns its form and posts :class:`Decided` when the user answers; the
    app removes it and resumes the run.  It is a sibling of the transcript (not
    a modal), so the chat stays scrollable while the ask is open.  It is
    bounded (``max-height``) and scrolls internally so a long plan/digest does
    not push the transcript off screen.
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Cancel")]

    DEFAULT_CSS = """
    HitlDecisionPanel {
        height: auto;
        max-height: 60%;
        border: round $primary;
        padding: 0 2;
        background: $surface;
    }
    HitlDecisionPanel #hitl-title,
    HitlDecisionPanel #perm-title {
        text-style: bold;
        height: 1;
    }
    HitlDecisionPanel #hitl-headline {
        text-style: bold;
        height: auto;
    }
    HitlDecisionPanel .hitl-section-title {
        text-style: bold;
        color: $text-muted;
        height: auto;
        margin-top: 1;
    }
    HitlDecisionPanel .hitl-section-body {
        height: auto;
    }
    HitlDecisionPanel Input {
        width: 1fr;
        margin-top: 1;
    }
    HitlDecisionPanel RadioSet {
        margin-top: 1;
    }
    """

    class Decided(Message):
        """Posted when the user answers an ask.

        :param decision: The user's :class:`HitlDecision`.
        """

        def __init__(self, decision: HitlDecision) -> None:
            self.decision = decision
            super().__init__()

    def action_cancel(self) -> None:
        """Cancel the ask (Escape or the Cancel button)."""
        self.post_message(self.Decided(HitlDecision(None, cancel=True)))


class HitlPanel(HitlDecisionPanel):
    """An inline review / input ask.

    A ``review`` ask renders the headline and sections (the plan/digest) with an
    approve / request-changes / cancel row; an ``input`` ask renders one field
    per question with an answer / cancel row.
    """

    def __init__(self, ask: dict[str, Any]) -> None:
        """Build the panel for the interrupt *ask*.

        :param ask: The interrupt ``data`` (``kind``, ``question``/
            ``questions``, ``headline``/``sections``).
        """
        super().__init__()
        self.ask = ask
        self._questions: list[dict[str, Any]] = []

    def compose(self) -> ComposeResult:
        """Compose the review or input form."""
        kind = self.ask.get("kind", "input")
        yield Static(
            "Plan review" if kind == "review" else "User input", id="hitl-title"
        )
        if kind == "review":
            yield from self._compose_review()
        else:
            yield from self._compose_input()

    def _compose_review(self) -> ComposeResult:
        """Compose the review form (headline, sections, feedback, buttons).

        The payload's ``headline`` and ``sections`` adapt to the review's
        position (a plan approval or a progress checkpoint; ADR-0051); both
        frontends render the same sections.
        """
        headline = self.ask.get("headline")
        if headline:
            yield Static(str(headline), id="hitl-headline", markup=False)
        question = self.ask.get("question")
        if question:
            yield Static(str(question), id="hitl-question", markup=False)
        for index, section in enumerate(self.ask.get("sections") or []):
            if not isinstance(section, Mapping):
                continue
            title = str(section.get("title", ""))
            if title:
                yield Static(
                    title,
                    id=f"hitl-section-{index}-title",
                    classes="hitl-section-title",
                    markup=False,
                )
            yield Static(
                str(section.get("body", "")),
                id=f"hitl-section-{index}-body",
                classes="hitl-section-body",
                markup=False,
            )
        yield Input(
            placeholder="Feedback (required to request changes)", id="hitl-feedback"
        )
        with Horizontal(classes="actions"):
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
        with Horizontal(classes="actions"):
            yield Button("Answer", id="hitl-answer", variant="primary")
            yield Button("Cancel", id="hitl-cancel", variant="error")

    def on_mount(self) -> None:
        """Focus the first input field."""
        fields = list(self.query(Input))
        if fields:
            fields[0].focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Post the decision for the pressed button.

        :param event: The ``Button.Pressed`` event.
        """
        button_id = event.button.id
        if button_id == "hitl-approve":
            self.post_message(self.Decided(HitlDecision({"decision": "approve"})))
        elif button_id == "hitl-revise":
            feedback = self.query_one("#hitl-feedback", Input).value or ""
            self.post_message(
                self.Decided(HitlDecision({"decision": "revise", "feedback": feedback}))
            )
        elif button_id == "hitl-answer":
            answers = [
                self.query_one(f"#hitl-answer-{index}", Input).value or ""
                for index in range(len(self._questions))
            ]
            self.post_message(self.Decided(HitlDecision({"answers": answers})))
        elif button_id == "hitl-cancel":
            self.action_cancel()


def _permission_key(request: Mapping[str, Any]) -> str:
    """Return the approval key for a path request (ADR-0007).

    The resolved file for a ``sensitive`` request, the resolved directory for an
    ``outside`` one.

    :param request: A permission request dict.
    :returns: The key the decision is recorded against.
    """
    if request.get("kind") == "sensitive":
        return str(request.get("path", ""))
    return str(request.get("directory", ""))


class PermissionPanel(HitlDecisionPanel):
    """An inline ask collecting a once/session/deny decision per requested path.

    Each request (a sensitive file or an outside-project directory, ADR-0007)
    gets a :class:`~textual.widgets.RadioSet` defaulting to ``deny``; Submit
    posts the per-key decisions, Cancel (or Escape) cancels the ask.
    """

    def __init__(self, ask: dict[str, Any]) -> None:
        """Build the panel for the permission interrupt *ask*.

        :param ask: The interrupt ``data`` (``requests``, ``question``).
        """
        super().__init__()
        self.ask = ask

    def _requests(self) -> list[Mapping[str, Any]]:
        """Return the ask's path requests (a fallback when the list is empty)."""
        requests = self.ask.get("requests")
        if not isinstance(requests, list) or not requests:
            requests = [{"kind": "outside", "path": self.ask.get("question", "")}]
        return [request for request in requests if isinstance(request, Mapping)]

    @staticmethod
    def _title(request: Mapping[str, Any]) -> str:
        """Return the row title for a request."""
        if request.get("kind") == "sensitive":
            return f"Sensitive file: {request.get('path', '')}"
        return f"Outside project: {request.get('directory', '')}"

    @staticmethod
    def _hint(request: Mapping[str, Any]) -> str:
        """Return the tool-call context (tool, argument, value, confidence)."""
        tool = str(request.get("tool", ""))
        argument = str(request.get("argument", ""))
        confidence = str(request.get("confidence", ""))
        raw = str(request.get("raw", ""))
        detail = (
            f"{tool}: {argument} = {raw} ({confidence})" if tool or argument else ""
        )
        if request.get("kind") == "sensitive":
            note = "Commonly holds credentials."
            return f"{detail} - {note}" if detail else note
        return detail

    @staticmethod
    def _options(request: Mapping[str, Any]) -> tuple[str, ...]:
        """Return the decision options for a request (``RadioSet`` order).

        "Not a path" is offered only for a ``guess``-tier request, where the
        gate may have mistaken a non-path value for a path.
        """
        options = ["once", "session"]
        if request.get("confidence") == "guess":
            options.append("not_a_path")
        options.append("deny")
        return tuple(options)

    def compose(self) -> ComposeResult:
        """Compose one radio group per requested path, plus Submit / Cancel."""
        yield Static("Permission required", id="perm-title")
        question = self.ask.get("question")
        if question:
            yield Static(str(question), id="perm-question")
        for index, request in enumerate(self._requests()):
            yield Static(self._title(request))
            hint = self._hint(request)
            if hint:
                yield Static(hint)
            with RadioSet(id=f"perm-{index}"):
                for option in self._options(request):
                    yield RadioButton(
                        _PERMISSION_OPTION_LABELS[option],
                        id=f"perm-{index}-{option}",
                        value=option == "deny",
                    )
        with Horizontal(classes="actions"):
            yield Button("Submit", id="perm-submit", variant="primary")
            yield Button("Cancel", id="perm-cancel", variant="error")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Submit the decisions or cancel.

        :param event: The ``Button.Pressed`` event.
        """
        if event.button.id == "perm-submit":
            self._submit()
        elif event.button.id == "perm-cancel":
            self.action_cancel()

    def _submit(self) -> None:
        """Post one decision per requested path."""
        decisions: list[dict[str, str]] = []
        for index, request in enumerate(self._requests()):
            radio_set = self.query_one(f"#perm-{index}", RadioSet)
            options = self._options(request)
            pressed = radio_set.pressed_index
            decision = options[pressed] if 0 <= pressed < len(options) else "deny"
            decisions.append({"key": _permission_key(request), "decision": decision})
        self.post_message(self.Decided(HitlDecision({"decisions": decisions})))
