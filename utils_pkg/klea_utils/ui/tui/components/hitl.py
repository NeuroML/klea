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

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.widgets import Button, Collapsible, Input, RadioButton, RadioSet, Rule

from klea_utils.ui.tui.components.buttons import action_button
from klea_utils.ui.tui.components.selectable import SelectableStatic

logger = logging.getLogger(__name__)

#: Path-permission decision options and their labels.  "Not a path" is offered
#: only for a ``guess``-tier request (see :meth:`PermissionPanel._options`).
_PERMISSION_OPTION_LABELS = {
    "once": "Once",
    "session": "Session",
    "not_a_path": "Not a path",
    "deny": "Deny",
}

#: Cap for one plan-step summary line in the permission ask (display only).
MAX_PERMISSION_STEP_SUMMARY_CHARS = 160
#: Cap for the number of step lines the permission ask renders.
MAX_PERMISSION_STEP_LINES = 8


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
    HitlDecisionPanel .hitl-section {
        height: auto;
        margin-top: 1;
    }
    HitlDecisionPanel .hitl-section-title {
        text-style: bold;
        color: $text-muted;
        height: auto;
        margin-top: 0;
    }
    HitlDecisionPanel .hitl-section-body {
        height: auto;
        padding-left: 2;
    }
    HitlDecisionPanel Input {
        width: 1fr;
        margin-top: 1;
    }
    HitlDecisionPanel RadioSet {
        margin-top: 1;
    }
    HitlDecisionPanel .hitl-hint {
        color: $text-muted;
        height: auto;
    }
    HitlDecisionPanel .perm-request {
        height: auto;
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
        yield SelectableStatic(
            "Plan review" if kind == "review" else "User input", id="hitl-title"
        )
        if kind == "review":
            yield from self._compose_review()
        else:
            yield from self._compose_input()

    def _compose_sections(self) -> ComposeResult:
        """Compose optional ask sections (review plan/progress, input history).

        Each section is rendered inside a :class:`~textual.widgets.Collapsible`
        so a multi-section ask (for example several candidate plans) can be
        reviewed one entry at a time.  The payload title is shown verbatim
        (falling back to ``Details`` when it is empty) and only the first
        section starts expanded, so comparing against the history opens on the
        first entry while the rest stay collapsed until the user expands them.
        """
        sections = [
            section
            for section in (self.ask.get("sections") or [])
            if isinstance(section, Mapping)
        ]
        single = len(sections) == 1
        for index, section in enumerate(sections):
            title = str(section.get("title") or "").strip() or "Details"
            if single:
                yield SelectableStatic(
                    title,
                    id=f"hitl-section-{index}-title",
                    classes="hitl-section-title",
                    markup=False,
                )
                yield SelectableStatic(
                    str(section.get("body", "")),
                    id=f"hitl-section-{index}-body",
                    classes="hitl-section-body",
                    markup=False,
                )
                continue
            with Collapsible(
                title=title,
                collapsed=index > 0,
                id=f"hitl-section-{index}",
                classes="hitl-section",
            ):
                yield SelectableStatic(
                    str(section.get("body", "")),
                    id=f"hitl-section-{index}-body",
                    classes="hitl-section-body",
                    markup=False,
                )

    def _compose_review(self) -> ComposeResult:
        """Compose the review form (headline, sections, feedback, buttons).

        The payload's ``headline`` and ``sections`` adapt to the review's
        position (a plan approval or a progress checkpoint; ADR-0051); both
        frontends render the same sections.
        """
        headline = self.ask.get("headline")
        if headline:
            yield SelectableStatic(str(headline), id="hitl-headline", markup=False)
        question = self.ask.get("question")
        if question:
            yield SelectableStatic(str(question), id="hitl-question", markup=False)
        yield from self._compose_sections()
        yield Input(
            placeholder="Feedback (required to request changes)", id="hitl-feedback"
        )
        yield SelectableStatic("", id="hitl-feedback-hint", classes="hitl-hint")
        with Horizontal(classes="actions"):
            yield action_button(
                "Approve", id="hitl-approve", variant="primary", compact=True
            )
            yield action_button("Request changes", id="hitl-revise", compact=True)
            yield action_button(
                "Cancel", id="hitl-cancel", variant="default", compact=True
            )

    def _compose_input(self) -> ComposeResult:
        """Compose the input form (plan history, questions, buttons)."""
        yield from self._compose_sections()
        questions = self.ask.get("questions")
        if not isinstance(questions, list) or not questions:
            fallback = self.ask.get("question") or "More information is needed."
            questions = [{"question": fallback}]
        self._questions = [q for q in questions if isinstance(q, dict)]
        for index, question in enumerate(self._questions):
            yield SelectableStatic(str(question.get("question", "")))
            yield Input(id=f"hitl-answer-{index}")
        with Horizontal(classes="actions"):
            yield action_button(
                "Answer", id="hitl-answer", variant="primary", compact=True
            )
            yield action_button(
                "Cancel", id="hitl-cancel", variant="default", compact=True
            )

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
            field = self.query_one("#hitl-feedback", Input)
            feedback = field.value or ""
            hint = self.query_one("#hitl-feedback-hint", SelectableStatic)
            if not feedback.strip():
                hint.update("Feedback is required to revise")
                field.focus()
                return
            hint.update("")
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


def _permission_path_display(request: Mapping[str, Any]) -> str:
    """Return the bounded target path for a permission request."""
    return str(
        request.get("path_display")
        or request.get("path")
        or request.get("directory", "")
    )


def _permission_directory_display(request: Mapping[str, Any]) -> str:
    """Return the bounded approval directory for a permission request."""
    return str(request.get("directory_display") or request.get("directory", ""))


def _permission_scope(request: Mapping[str, Any]) -> str:
    """Return the session-approval scope when it differs from the target path."""
    path = str(request.get("path", ""))
    directory = str(request.get("directory", ""))
    if request.get("kind") != "sensitive" and path and directory and path != directory:
        return f"Session approval covers: {_permission_directory_display(request)}"
    return ""


class PermissionPanel(HitlDecisionPanel):
    """An inline ask collecting a once/session/deny decision per requested path.

    Each request (a sensitive file or an outside-project path, ADR-0007)
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
        path = _permission_path_display(request)
        if request.get("kind") == "sensitive":
            return f"Sensitive file: {path}"
        return f"Outside project path: {path}"

    @staticmethod
    def _hint(request: Mapping[str, Any]) -> str:
        """Return the tool-call context (tool, argument, value, confidence)."""
        tool = str(request.get("tool", ""))
        argument = str(request.get("argument", ""))
        confidence = str(request.get("confidence", ""))
        value = str(request.get("argument_preview") or request.get("raw", ""))
        raw = str(request.get("raw_preview") or request.get("raw", ""))
        source = str(request.get("source", ""))
        detail = (
            f"{tool}: {argument} = {value} ({confidence})" if tool or argument else ""
        )
        if raw and (source.startswith("value:") or raw not in value):
            detail = (
                f"{detail}\nDetected token: {raw}"
                if detail
                else f"Detected token: {raw}"
            )
        if source:
            detail = f"{detail}\nSource: {source}" if detail else f"Source: {source}"
        if request.get("kind") == "sensitive":
            note = "Commonly holds credentials."
            return f"{detail} - {note}" if detail else note
        return detail

    @classmethod
    def _hint_renderable(cls, request: Mapping[str, Any]) -> Text | str:
        """Return the tool-call context as a styled :class:`rich.text.Text`.

        Keeps :meth:`_hint`'s plain-string contract for the fallback while
        offering a richer renderable for the panel: the tool and argument, an
        explicit detected path with its confidence reason, and a ``Context:``
        block whose detected token is highlighted (reverse video) with
        :meth:`rich.text.Text.stylize` at the request's
        ``preview_token_start``/``preview_token_end`` offsets, followed by an
        omission/line note.  A request that carries no token offsets (an older
        checkpoint) falls back to the plain :meth:`_hint` string so the panel
        still renders.

        :param request: A permission request dict.
        :returns: A styled ``Text``, or the plain :meth:`_hint` string when the
            preview offsets are missing.
        """
        token_start = request.get("preview_token_start", -1)
        token_end = request.get("preview_token_end", -1)
        if not isinstance(token_start, int) or token_start < 0:
            return cls._hint(request)

        tool = str(request.get("tool", ""))
        argument = str(request.get("argument", ""))
        confidence = str(request.get("confidence", ""))
        reason = str(request.get("confidence_reason") or confidence)
        raw = str(request.get("raw_preview") or request.get("raw", ""))
        preview = str(request.get("argument_preview", ""))
        token_line = request.get("token_line", 1)
        if not isinstance(token_line, int) or token_line < 1:
            token_line = 1
        omitted_before = request.get("preview_omitted_before", 0)
        omitted_after = request.get("preview_omitted_after", 0)
        omitted = (omitted_before if isinstance(omitted_before, int) else 0) + (
            omitted_after if isinstance(omitted_after, int) else 0
        )
        total_lines = preview.count("\n") + 1

        logger.debug(
            f"Rendering permission hint\n{tool = }\n{argument = }\n"
            f"{token_start = }\n{token_end = }\n{reason = }"
        )

        text = Text()
        if tool:
            text.append(f"Tool: {tool}\n")
        if argument:
            text.append(f"Argument: {argument}\n")
        if raw:
            text.append(f"Detected path: {raw} ({reason})\n")
        context = Text(preview)
        if isinstance(token_end, int) and token_end > token_start:
            context.stylize("reverse", token_start, min(token_end, len(preview)))
        text.append("Context: ")
        text.append_text(context)
        text.append(
            f" ({omitted} chars omitted before/after, line {token_line} of {total_lines})"
        )
        return text

    @staticmethod
    def _coverage(request: Mapping[str, Any]) -> str:
        """Return the paths covered by this request's session approval."""
        paths = (
            request.get("covered_paths_display") or request.get("covered_paths") or []
        )
        if not isinstance(paths, list) or len(paths) < 2:
            return ""
        lines = "\n".join(f"- {path}" for path in paths)
        return f"Covers:\n{lines}"

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

    @classmethod
    def _step_lines(cls, ask: Mapping[str, Any]) -> list[str]:
        """Return bounded ``Step N: summary`` lines for the permission ask.

        The ask carries display-only plan-step context (``steps``) so the user
        can see what work each requested path is wanted for (ADR-0007).  Each
        entry is rendered as a single, whitespace-collapsed line; a summary
        longer than :data:`MAX_PERMISSION_STEP_SUMMARY_CHARS` is cut at the end
        with an ASCII ``...``.  The list is capped at
        :data:`MAX_PERMISSION_STEP_LINES`, so a pathological payload cannot
        flood the panel.  An ask without step context (an older checkpoint, or
        a plan-less run such as Klea RAG) yields an empty list, so nothing is
        rendered and the panel is unchanged.

        :param ask: The permission interrupt payload.
        :returns: The bounded step lines, in the payload's order.
        """
        steps = ask.get("steps")
        if not isinstance(steps, list):
            return []
        lines: list[str] = []
        for step in steps[:MAX_PERMISSION_STEP_LINES]:
            if not isinstance(step, Mapping):
                continue
            number = step.get("step_number")
            summary = " ".join(str(step.get("summary") or "").split())
            if not summary:
                continue
            if len(summary) > MAX_PERMISSION_STEP_SUMMARY_CHARS:
                summary = summary[:MAX_PERMISSION_STEP_SUMMARY_CHARS].rstrip() + "..."
            lines.append(f"Step {number}: {summary}" if number is not None else summary)
        return lines

    #: Heading for requests that carry no plan-step context (grouped layout).
    OTHER_HEADING = "Other paths"

    #: Cap for the number of display lines one plan-step heading may take.
    STEP_HEADING_MAX_LINES = 2

    #: Width a plan-step heading wraps at: the panel's inner width at 80 cols.
    STEP_HEADING_WRAP_CHARS = 76

    @staticmethod
    def _request_steps(request: Mapping[str, Any]) -> list[int]:
        """Return the plan-step numbers a request serves, sorted.

        :param request: One serialized permission request.
        :returns: The step numbers, ascending; empty when it carries none.
        """
        steps = request.get("steps")
        if not isinstance(steps, list):
            return []
        numbers: list[int] = []
        for step in steps:
            if not isinstance(step, Mapping):
                continue
            number = step.get("step_number")
            if isinstance(number, int) and number not in numbers:
                numbers.append(number)
        return sorted(numbers)

    @classmethod
    def _step_heading(cls, number: int, summary: str) -> str:
        """Return one bounded ``Step N: summary`` heading line (display only).

        It is whitespace-collapsed and cut to
        :data:`MAX_PERMISSION_STEP_SUMMARY_CHARS` with an ASCII ``...``, like
        :meth:`_step_lines`.

        :param number: The plan step number.
        :param summary: The step summary, possibly empty.
        :returns: The heading line.
        """
        text = " ".join(summary.split())
        if len(text) > MAX_PERMISSION_STEP_SUMMARY_CHARS:
            text = text[:MAX_PERMISSION_STEP_SUMMARY_CHARS].rstrip() + "..."
        return f"Step {number}: {text}" if text else f"Step {number}"

    @classmethod
    def _step_static(cls, line: str, coverage: str = "") -> SelectableStatic:
        """Return one plan-step heading line, set apart and bold.

        The heading sets its plan step off from the request blocks around it
        (ADR-0007): a blank line above it and the ``Step N: summary`` text in
        bold.  The emphasis comes from a bold Rich ``Text`` rather than Rich
        markup, so the model-authored summary is still never parsed as markup;
        the blank line rides on the same renderable, so no extra widget is
        inserted and the rest of the panel is unchanged.  A long summary wraps
        onto at most :attr:`STEP_HEADING_MAX_LINES` lines and ends in an ASCII
        ``...`` when it is cut.  Optional *coverage* text (the group's path
        counts) is appended below the heading as one muted line, still on the
        same markup-free renderable.

        :param line: The bounded ``Step N: summary`` (or heading) line.
        :param coverage: The muted coverage line, or ``""`` for none.
        :returns: The heading widget.
        """
        text = Text("\n")
        heading = Text(cls._wrap_heading(line))
        heading.stylize("bold")
        text.append_text(heading)
        if coverage:
            text.append("\n")
            note = Text(coverage)
            note.stylize("dim")
            text.append_text(note)
        return SelectableStatic(text, markup=False)

    @classmethod
    def _wrap_heading(cls, line: str) -> str:
        """Return *line* wrapped to :attr:`STEP_HEADING_WRAP_CHARS` and capped.

        The wrap breaks on spaces, keeps at most
        :attr:`STEP_HEADING_MAX_LINES` lines, and marks a cut tail with an
        ASCII ``...`` trimmed so the last line still fits the wrap width.

        :param line: The heading line to wrap.
        :returns: The heading, with newline separators between its lines.
        """
        width = cls.STEP_HEADING_WRAP_CHARS
        lines: list[str] = []
        current = ""
        for word in line.split():
            if not current:
                current = word
            elif len(current) + 1 + len(word) <= width:
                current = f"{current} {word}"
            else:
                lines.append(current)
                current = word
        if current:
            lines.append(current)
        if len(lines) <= cls.STEP_HEADING_MAX_LINES:
            return "\n".join(lines)
        kept = lines[: cls.STEP_HEADING_MAX_LINES]
        ellipsis = "..."
        if len(kept[-1]) + len(ellipsis) > width:
            kept[-1] = kept[-1][: width - len(ellipsis)].rstrip()
        kept[-1] = f"{kept[-1]}{ellipsis}"
        return "\n".join(kept)

    @staticmethod
    def _group_coverage(group: list[tuple[int, Mapping[str, Any], str]]) -> str:
        """Return one muted coverage line for a group of permission requests.

        The line counts the requests under a heading by the ``kind`` the
        payload carries (``outside`` / ``sensitive``), so the user sees how
        many paths a step needs: ``3 paths: 2 outside the project, 1
        sensitive``.  The noun is singular for a single path, a zero-count
        category is omitted, and a request with a missing or unknown ``kind``
        is counted in a trailing ``unknown`` bucket only when non-zero.

        :param group: The ``(index, request, hint)`` rows under one heading.
        :returns: The coverage line, or ``""`` for an empty group.
        """
        outside = sensitive = unknown = 0
        for _, request, _ in group:
            kind = request.get("kind")
            if kind == "outside":
                outside += 1
            elif kind == "sensitive":
                sensitive += 1
            else:
                unknown += 1
        total = outside + sensitive + unknown
        if not total:
            return ""
        parts = [
            f"{count} {label}"
            for count, label in (
                (outside, "outside the project"),
                (sensitive, "sensitive"),
                (unknown, "unknown"),
            )
            if count
        ]
        noun = "path" if total == 1 else "paths"
        return f"{total} {noun}: " + ", ".join(parts)

    @classmethod
    def _group_also(cls, group: list[tuple[int, Mapping[str, Any], str]]) -> str:
        """Return one muted ``Also serves step N`` line for a group of requests.

        A request that serves several plan steps is shown under its lowest
        number (see :meth:`_groups`).  The further steps are unioned across the
        whole group, sorted and de-duplicated, so several multi-step requests
        produce one line beneath the heading instead of one hint per row.

        :param group: The ``(index, request, hint)`` rows under one heading.
        :returns: The ``Also serves step ...`` line, or ``""`` when no request
            serves more than one step.
        """
        numbers: set[int] = set()
        for _, request, _ in group:
            numbers.update(cls._request_steps(request)[1:])
        if not numbers:
            return ""
        return "Also serves step " + ", ".join(str(n) for n in sorted(numbers))

    @classmethod
    def _groups(
        cls, requests: list[Any]
    ) -> list[tuple[str, list[tuple[int, Mapping[str, Any], str]]]]:
        """Group the requests under their plan-step headings.

        Each request may carry its own display-only ``steps`` context.  A
        request is placed under the heading of its lowest step number, keeping
        its global display number and its ``perm-{index}`` radio ids; a request
        that also serves further steps gets a bounded ``Also serves step N``
        hint.  Step-less requests go to a trailing :attr:`OTHER_HEADING`
        bucket, shown only when at least one request carries a step.  When no
        request carries step context the whole payload is returned as one
        unheaded group, so the flat layout is unchanged.

        :param requests: The serialized requests, in payload order.
        :returns: ``(heading, [(index, request, hint), ...])`` pairs; the
            heading is empty for the flat layout.
        """
        entries = [
            (index, request)
            for index, request in enumerate(requests, start=1)
            if isinstance(request, Mapping)
        ]
        member_steps: dict[int, list[int]] = {}
        summaries: dict[int, str] = {}
        for index, request in entries:
            member_steps[index] = cls._request_steps(request)
            steps = request.get("steps")
            if not isinstance(steps, list):
                continue
            for step in steps:
                if not isinstance(step, Mapping):
                    continue
                number = step.get("step_number")
                if not isinstance(number, int) or summaries.get(number):
                    continue
                text = str(step.get("summary") or "").strip()
                if text:
                    summaries[number] = text

        if not any(member_steps.get(index) for index, _ in entries):
            return [("", [(index, request, "") for index, request in entries])]

        buckets: dict[int, list[tuple[int, Mapping[str, Any], str]]] = {}
        others: list[tuple[int, Mapping[str, Any], str]] = []
        for index, request in entries:
            numbers = member_steps.get(index) or []
            if not numbers:
                others.append((index, request, ""))
                continue
            also = ""
            if len(numbers) > 1:
                also = "Also serves step " + ", ".join(str(n) for n in numbers[1:])
            buckets.setdefault(numbers[0], []).append((index, request, also))

        groups = [
            (cls._step_heading(number, summaries.get(number, "")), buckets[number])
            for number in sorted(buckets)
        ]
        if others:
            groups.append((cls.OTHER_HEADING, others))
        return groups

    def compose(self) -> ComposeResult:
        """Compose one heading per plan step, then that step's requests."""
        yield SelectableStatic("Permission required", id="perm-title")
        question = self.ask.get("question")
        if question:
            yield SelectableStatic(str(question), id="perm-question")
        requests = self._requests()
        # Display-only plan-step context: the work each requested path serves.
        # ``markup=False`` so the model-authored summary is never parsed as Rich
        # markup; absent context renders nothing.  Per-request step context is
        # grouped into the blocks below, so these heading lines are kept only
        # for a legacy payload whose step context sits on the ask alone.
        if not any(
            self._request_steps(request)
            for request in requests
            if isinstance(request, Mapping)
        ):
            for step_line in self._step_lines(self.ask):
                yield self._step_static(step_line)
        shown = False
        for heading, group in self._groups(requests):
            if heading:
                yield self._step_static(heading, self._group_coverage(group))
                also_serves = self._group_also(group)
                if also_serves:
                    yield SelectableStatic(
                        also_serves, classes="hitl-hint", markup=False
                    )
            for index, request, _ in group:
                if shown:
                    yield Rule()
                shown = True
                with Vertical(classes="perm-request"):
                    yield SelectableStatic(
                        f"{index}. {self._title(request)}", markup=False
                    )
                    scope = _permission_scope(request)
                    if scope:
                        yield SelectableStatic(scope, classes="hitl-hint", markup=False)
                    hint = self._hint_renderable(request)
                    if hint:
                        yield SelectableStatic(hint, classes="hitl-hint", markup=False)
                    coverage = self._coverage(request)
                    if coverage:
                        yield SelectableStatic(
                            coverage, classes="hitl-hint", markup=False
                        )
                    radio_index = index - 1
                    with RadioSet(id=f"perm-{radio_index}"):
                        for option in self._options(request):
                            yield RadioButton(
                                _PERMISSION_OPTION_LABELS[option],
                                id=f"perm-{radio_index}-{option}",
                                value=option == "deny",
                            )
        with Horizontal(classes="actions"):
            yield action_button("Submit", id="perm-submit", variant="primary")
            yield action_button("Cancel", id="perm-cancel", variant="default")

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
