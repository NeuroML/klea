#!/usr/bin/env python3
"""
Chat area component: message list, scroll, and empty-state.

Builds the scroll area that holds the message bubbles and the stream
progress container, and registers the render/scroll callbacks on the
page context.

File: klea_utils/ui/web/nicegui/components/chat_area.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import html
import json
import logging
import time
from typing import Any

from nicegui import ui

from klea_utils.llm import missing_required_roles
from klea_utils.ui.linkify import linkify_md
from klea_utils.ui.state import TurnStatus, chats, missing_credentials
from klea_utils.ui.web.nicegui.components.chat_bubble import ChatBubble
from klea_utils.ui.web.nicegui.components.choice import choice_buttons
from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.state_edit import render_state_edit

logger = logging.getLogger(__name__)

#: Maximum number of message bubbles mounted in the chat scroll area at once.
#: Mirrors the TUI transcript's ``Transcript.MAX_MOUNTED``: the newest window of
#: messages is mounted and the oldest bubbles beyond it are left unrendered, so
#: a long session keeps its widget tree (and layout cost) bounded.  Only the
#: rendered window is trimmed -- the chat store keeps the full message list,
#: which ``/export`` and hydration read.
MOUNTED_MESSAGE_LIMIT = 80

#: Number of stored messages mounted per scroll-to-top / scroll-to-bottom batch
#: in the web transcript.  Mirrors the TUI transcript's
#: ``Transcript.SCROLL_BATCH`` (``tui/components/transcript.py``): the mounted
#: window slides in fixed steps while staying bounded by
#: :data:`MOUNTED_MESSAGE_LIMIT`.
SCROLL_BATCH = 20


def render_turn_status(ctx: PageContext, retry_cb=None) -> None:
    """Render the turn status region for the active chat from its state.

    The region is a single slot under the transcript, owned by the chat's
    ``status`` dict (``state.ChatData``), so it is restored on chat switch
    and replaced wholesale on the next turn.  ``kind`` selects the content:

    * ``progress`` - a spinner plus the live ``heading``.
    * ``error``    - the message and, when ``resumable``, a Retry action.
    * ``stopped``  - a plain "Stopped" line.
    * ``idle``/missing - nothing.

    :param ctx: The shared page context (uses ``turn_status_container``).
    :param retry_cb: Callable invoked by the Retry button; the stream
        component passes it when rendering a live error.
    """
    status = _current_turn_status(ctx)
    kind = status.get("kind", "idle")
    container = ctx.turn_status_container
    container.clear()
    ctx.interrupt_container.clear()
    if kind == "idle":
        return
    if kind == "awaiting_input":
        # The ask replaces the composer: render it in the pinned container
        # between the transcript and the input row (the input area hides its
        # row while awaiting, mirroring the TUI).
        with ctx.interrupt_container:
            _render_interrupt_form(ctx)
        return
    with container:
        if kind == "progress":
            with ui.row().classes("w-full items-center gap-2 p-2"):
                ui.spinner(type="dots").classes("w-4 h-4")
                label = ui.label(status.get("heading", "")).classes(
                    "text-xs text-grey-5 italic"
                )
            ctx.turn_status_label = label
        elif kind == "error":
            with ui.row().classes("w-full items-center gap-2 p-2"):
                ui.icon("error").classes("text-negative")
                message = f"Error: {status.get('message', '')}"
                if "No model configured" in message:
                    message += (
                        " Use the settings (gear) icon to choose a model for "
                        "this chat, then retry."
                    )
                ui.label(message).classes("text-xs text-negative flex-grow")
                if status.get("resumable") and retry_cb is not None:
                    ui.button("Retry", on_click=retry_cb).props(
                        "flat dense color=primary"
                    )
        elif kind == "stopped":
            ui.label("Stopped").classes("text-xs text-grey-5 italic p-2")


def _current_turn_status(ctx: PageContext) -> TurnStatus:
    """Return the active chat's status dict, or ``{"kind": "idle"}``."""
    if not ctx.chat_id:
        return {"kind": "idle"}
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
    if not chat:
        return {"kind": "idle"}
    return chat.get("turn_status") or {"kind": "idle"}


def _interrupt_ask(ctx: PageContext) -> dict[str, Any]:
    """Return the active chat's pending HITL ask, or ``{}``."""
    if not ctx.chat_id:
        return {}
    chat = chats.get(f"{ctx.user_id}:{ctx.chat_id}")
    return (chat or {}).get("interrupt") or {}


def _render_ask_sections(ask: dict[str, Any]) -> None:
    """Render optional HITL sections (a review's plan/progress content).

    Only review asks carry sections; they are rendered monospace so tool names
    and paths stay literal, and full results remain in the inspection pane.
    """
    sections = [
        section for section in (ask.get("sections") or []) if isinstance(section, dict)
    ]
    for index, section in enumerate(sections):
        title = str(section.get("title") or "").strip() or "Details"
        title_classes = "text-xs font-bold"
        if index:
            title_classes = f"{title_classes} mt-2"
        ui.label(title).classes(title_classes)
        if body := section.get("body"):
            ui.code(body, language=None).classes(
                "text-sm w-full hitl-ask-body nicegui-code-noformat"
            )


def _render_interrupt_form(ctx: PageContext) -> None:
    """Render the HITL ask form in the turn status region (ADR-0046).

    A ``review`` ask gets Continue / Revise with feedback / Cancel; an
    ``input`` ask gets one text field per question plus Answer / Cancel; a
    ``permission`` ask (ADR-0007) gets one allow-once / allow-session / deny
    choice per requested path; a ``state_edit`` ask renders the generic
    session-state editor.  Submitting calls
    :attr:`PageContext.submit_interrupt` (registered by the input area).
    """
    ask = _interrupt_ask(ctx)
    kind = ask.get("kind", "input")
    interrupt_id = ask.get("interrupt_id")
    if kind == "permission":
        _render_permission_form(ctx, ask, interrupt_id)
        return
    if kind == "state_edit":
        render_state_edit(ctx, ask, interrupt_id)
        return
    heading = "Plan review" if kind == "review" else "User input"
    with ui.column().classes("w-full gap-2 p-3 border border-primary rounded hitl-ask"):
        ui.label(heading).classes("text-sm font-bold")
        if kind == "review":
            # ``headline`` + ``sections`` adapt to the review's position
            # (ADR-0051): a plan approval, or a checkpoint showing the work
            # done since the last review plus what approval unblocks.  The
            # sections are rendered monospace so tool names/paths stay literal
            # (full results are in the inspection pane).
            if headline := ask.get("headline"):
                ui.label(headline).classes("text-sm font-bold")
            if question := ask.get("question"):
                ui.label(question).classes("text-xs text-grey-6")
            _render_ask_sections(ask)
            feedback = (
                ui.textarea(placeholder="Feedback (required to revise)")
                .props("outlined autogrow")
                .classes("w-full chat-input")
            )
            with ui.row().classes("gap-2"):
                ui.button(
                    "Continue",
                    on_click=lambda: ctx.submit_interrupt(
                        {"decision": "approve"}, interrupt_id, False
                    ),
                ).props("unelevated color=primary")
                ui.button(
                    "Revise with feedback",
                    on_click=lambda: ctx.submit_interrupt(
                        {"decision": "revise", "feedback": feedback.value or ""},
                        interrupt_id,
                        False,
                    ),
                ).props("outline")
                ui.button(
                    "Cancel",
                    on_click=lambda: ctx.submit_interrupt(None, interrupt_id, True),
                ).props("flat color=negative")
            return

        # input kind: plan history (when present), then one text field per
        # question.
        _render_ask_sections(ask)
        questions = ask.get("questions")
        if not isinstance(questions, list) or not questions:
            fallback = ask.get("question") or "More information is needed to continue."
            questions = [{"question": fallback}]
        fields = []
        for question in questions:
            ui.label(str(question.get("question", ""))).classes("text-sm")
            fields.append(ui.input().props("outlined dense").classes("w-full"))

        def _submit() -> None:
            ctx.submit_interrupt(
                {"answers": [field.value or "" for field in fields]},
                interrupt_id,
                False,
            )

        with ui.row().classes("gap-2"):
            ui.button("Answer", on_click=_submit).props("unelevated color=primary")
            ui.button(
                "Cancel",
                on_click=lambda: ctx.submit_interrupt(None, interrupt_id, True),
            ).props("flat color=negative")


def _permission_key(request: dict[str, Any]) -> str:
    """Return the approval key for a serialized path request (ADR-0007).

    The resolved file for a ``sensitive`` request, the resolved directory for
    an ``outside`` one.
    """
    if request.get("kind") == "sensitive":
        return str(request.get("path", ""))
    return str(request.get("directory", ""))


def _permission_path_display(request: dict[str, Any]) -> str:
    """Return the bounded target path for a permission request."""
    return str(
        request.get("path_display")
        or request.get("path")
        or request.get("directory", "")
    )


def _permission_directory_display(request: dict[str, Any]) -> str:
    """Return the bounded approval directory for a permission request."""
    return str(request.get("directory_display") or request.get("directory", ""))


def _permission_scope(request: dict[str, Any]) -> str:
    """Return the session-approval scope when it differs from the target path."""
    path = str(request.get("path", ""))
    directory = str(request.get("directory", ""))
    if request.get("kind") != "sensitive" and path and directory and path != directory:
        return f"Session approval covers: {_permission_directory_display(request)}"
    return ""


def _permission_hint(request: dict[str, Any]) -> str:
    """Return the tool-call context for a request (tool, argument, value).

    Gives the user the tool call behind the ask so they can judge it.  Includes
    the confidence tier: a ``guess`` is a heuristic path-like value (e.g. a
    regex fragment), which is also the only tier offered the "Not a path"
    choice.  The argument value is a bounded preview; the full target path is
    shown in the row title.
    """
    tool = str(request.get("tool", ""))
    argument = str(request.get("argument", ""))
    confidence = str(request.get("confidence", ""))
    value = str(request.get("argument_preview") or request.get("raw", ""))
    raw = str(request.get("raw_preview") or request.get("raw", ""))
    source = str(request.get("source", ""))
    detail = f"{tool}: {argument} = {value} ({confidence})" if tool or argument else ""
    if raw and (source.startswith("value:") or raw not in value):
        detail = (
            f"{detail}\nDetected token: {raw}" if detail else f"Detected token: {raw}"
        )
    if source:
        detail = f"{detail}\nSource: {source}" if detail else f"Source: {source}"
    if request.get("kind") == "sensitive":
        note = "Commonly holds credentials."
        return f"{detail} - {note}" if detail else note
    return detail


def _permission_hint_parts(request: dict[str, Any]) -> dict[str, Any]:
    """Return the structured parts of a permission hint (ADR-0007).

    Splits the tool-call context into separately rendered elements so the web
    form can show the tool, the argument, the detected token (with its
    confidence reason) and the argument context with the token highlighted.
    The context is escaped before it is embedded in HTML: it is a
    model-provided argument value, never trusted markup.

    Returns an empty mapping when the request carries no token offsets (an
    older checkpoint), so the caller can fall back to the single-line
    :func:`_permission_hint` text.

    :param request: A serialized path request.
    :returns: The hint parts, or ``{}`` when the preview offsets are missing.
    """
    token_start = request.get("preview_token_start", -1)
    token_end = request.get("preview_token_end", -1)
    if not isinstance(token_start, int) or token_start < 0:
        return {}

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
    start = min(token_start, len(preview))
    end = token_end if isinstance(token_end, int) and token_end > start else start
    end = min(end, len(preview))
    logger.debug(
        f"Building permission hint parts\n{tool = }\n{argument = }\n"
        f"{start = }\n{end = }\n{reason = }"
    )
    context_html = (
        html.escape(preview[:start])
        + "<mark><code>"
        + html.escape(preview[start:end])
        + "</code></mark>"
        + html.escape(preview[end:])
    )
    return {
        "tool": tool,
        "argument": argument,
        "reason": reason,
        "raw": raw,
        "context_html": context_html,
        "omitted": omitted,
        "token_line": token_line,
        "total_lines": total_lines,
    }


def _render_permission_hint(request: dict[str, Any]) -> None:
    """Render a permission request's tool-call context (ADR-0007).

    Emits separate elements: the tool and argument, the detected token in
    monospace with its confidence reason as a caption, and the argument
    context with the detected token highlighted.  The context is escaped HTML
    rendered through :func:`nicegui.ui.html`, never raw markup.  A request
    without token offsets (an older checkpoint) falls back to the single-line
    :func:`_permission_hint` text, so an ask always renders.

    :param request: A serialized path request.
    """
    parts = _permission_hint_parts(request)
    if not parts:
        hint = _permission_hint(request)
        if hint:
            ui.label(hint).classes("text-xs text-grey-6").style("white-space: pre-line")
        return
    if parts["tool"]:
        ui.label(f"Tool: {parts['tool']}").classes("text-xs font-bold")
    if parts["argument"]:
        ui.label(f"Argument: {parts['argument']}").classes("text-xs font-bold")
    if parts["raw"]:
        ui.code(parts["raw"], language=None).classes("text-xs")
    if parts["reason"]:
        ui.label(parts["reason"]).classes("text-xs text-grey-6")
    ui.html(
        "<div>Context: "
        f"{parts['context_html']} "
        f"({parts['omitted']} chars omitted before/after, "
        f"line {parts['token_line']} of {parts['total_lines']})</div>"
    ).classes("text-xs text-grey-6").style("white-space: pre-wrap")


def _permission_coverage(request: dict[str, Any]) -> str:
    """Return the paths covered by this request's session approval, if plural."""
    paths = request.get("covered_paths_display") or request.get("covered_paths") or []
    if not isinstance(paths, list) or len(paths) < 2:
        return ""
    lines = "\n".join(f"- {path}" for path in paths)
    return f"Covers:\n{lines}"


def _permission_options(request: dict[str, Any]) -> dict[str, str]:
    """Return the per-request decision options.

    The "Not a path" dismissal is offered only for a ``guess``-tier request,
    where the gate may have mistaken a non-path value for a path.
    """
    options = {"once": "Once", "session": "Session"}
    if request.get("confidence") == "guess":
        options["not_a_path"] = "Not a path"
    options["deny"] = "Deny"
    return options


#: Cap for one plan-step summary line in the permission ask (display only).
MAX_PERMISSION_STEP_SUMMARY_CHARS = 160
#: Cap for the number of step lines the permission ask renders.
MAX_PERMISSION_STEP_LINES = 8


def _permission_step_lines(ask: dict[str, Any]) -> list[str]:
    """Return bounded ``Step N: summary`` lines for the permission ask.

    The ask carries display-only plan-step context (``steps``) so the user can
    see what work each requested path is wanted for (ADR-0007).  Each entry is
    rendered as a single, whitespace-collapsed line; a summary longer than
    :data:`MAX_PERMISSION_STEP_SUMMARY_CHARS` is cut at the end with an ASCII
    ``...``.  The list is capped at :data:`MAX_PERMISSION_STEP_LINES`, so a
    pathological payload cannot flood the form.  An ask without step context
    (an older checkpoint, or a plan-less run such as Klea RAG) yields an empty
    list, so nothing is rendered and the form is unchanged.

    The summary is model-authored: it is returned as plain text and the caller
    renders it through :func:`nicegui.ui.label`, which never interprets it as
    markup, so it cannot inject HTML.

    :param ask: The stored permission interrupt payload.
    :returns: The bounded step lines, in the payload's order.
    """
    steps = ask.get("steps")
    if not isinstance(steps, list):
        return []
    lines: list[str] = []
    for step in steps[:MAX_PERMISSION_STEP_LINES]:
        if not isinstance(step, dict):
            continue
        number = step.get("step_number")
        summary = " ".join(str(step.get("summary") or "").split())
        if not summary:
            continue
        if len(summary) > MAX_PERMISSION_STEP_SUMMARY_CHARS:
            summary = summary[:MAX_PERMISSION_STEP_SUMMARY_CHARS].rstrip() + "..."
        lines.append(f"Step {number}: {summary}" if number is not None else summary)
    return lines


#: Heading for requests that carry no plan-step context (grouped layout only).
PERMISSION_OTHER_HEADING = "Other paths"

#: Cap for the number of display lines one plan-step heading may take.
MAX_PERMISSION_STEP_HEADING_LINES = 2

#: Width a plan-step heading wraps at: the approval form's inner width, chosen
#: to match the TUI's approval panel for parity.
MAX_PERMISSION_STEP_HEADING_LINE_CHARS = 76

#: Classes for one plan-step heading: bold, with a blank line above it via
#: ``mt-2``, the same spacing token as the surrounding column's ``gap-2``, and
#: ``whitespace-pre-line`` so a wrapped summary keeps its line break.
PERMISSION_STEP_HEADING_CLASSES = "text-xs font-bold mt-2 whitespace-pre-line"

#: Classes for the muted coverage line under a heading: the quietest label,
#: reusing the existing theme-token mapping (``text-grey-6`` becomes
#: ``--klea-muted`` in dark mode) rather than introducing a new colour.
PERMISSION_STEP_COVERAGE_CLASSES = "text-xs text-grey-6"


def _permission_request_steps(request: dict[str, Any]) -> list[int]:
    """Return the plan-step numbers a request serves, sorted and de-duplicated.

    :param request: One serialized permission request.
    :returns: The step numbers, ascending; empty when the request carries none.
    """
    steps = request.get("steps")
    if not isinstance(steps, list):
        return []
    numbers: list[int] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        number = step.get("step_number")
        if isinstance(number, int) and number not in numbers:
            numbers.append(number)
    return sorted(numbers)


def _permission_step_heading(number: int, summary: str) -> str:
    """Return one bounded ``Step N: summary`` heading line (display only).

    The summary is model-authored, so the caller renders it through
    :func:`nicegui.ui.label` (never ``ui.html``) and it cannot inject markup.
    It is whitespace-collapsed and cut to
    :data:`MAX_PERMISSION_STEP_SUMMARY_CHARS` with an ASCII ``...``, matching
    the top-of-form legacy lines.

    :param number: The plan step number.
    :param summary: The step summary, possibly empty.
    :returns: The heading line, wrapped to at most
        :data:`MAX_PERMISSION_STEP_HEADING_LINES` lines.
    """
    text = " ".join(summary.split())
    if len(text) > MAX_PERMISSION_STEP_SUMMARY_CHARS:
        text = text[:MAX_PERMISSION_STEP_SUMMARY_CHARS].rstrip() + "..."
    heading = f"Step {number}: {text}" if text else f"Step {number}"
    return _wrap_permission_heading(heading)


def _wrap_permission_heading(heading: str) -> str:
    """Return *heading* wrapped to the heading width and capped.

    It breaks on spaces, keeps at most
    :data:`MAX_PERMISSION_STEP_HEADING_LINES` lines, and marks a cut tail with
    an ASCII ``...`` trimmed to stay inside
    :data:`MAX_PERMISSION_STEP_HEADING_LINE_CHARS`, mirroring the TUI panel's
    heading so the bold ``Step N`` prefix stays on the first line.

    :param heading: The single-line ``Step N: summary`` heading.
    :returns: The heading, with newline separators between its lines.
    """
    width = MAX_PERMISSION_STEP_HEADING_LINE_CHARS
    lines: list[str] = []
    current = ""
    for word in heading.split():
        if not current:
            current = word
        elif len(current) + 1 + len(word) <= width:
            current = f"{current} {word}"
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    if len(lines) <= MAX_PERMISSION_STEP_HEADING_LINES:
        return "\n".join(lines)
    kept = lines[:MAX_PERMISSION_STEP_HEADING_LINES]
    ellipsis = "..."
    if len(kept[-1]) + len(ellipsis) > width:
        kept[-1] = kept[-1][: width - len(ellipsis)].rstrip()
    kept[-1] = f"{kept[-1]}{ellipsis}"
    return "\n".join(kept)


def _permission_groups(
    requests: list[Any],
) -> list[tuple[str, list[tuple[int, dict[str, Any], str]]]]:
    """Group the permission requests under their plan-step headings.

    Each request may carry its own display-only ``steps`` context (the plan
    steps the dispatching call was bound for, ADR-0007).  A request is placed
    under the heading of its lowest step number, keeping its payload order and
    its original 1-based display number and path/directory decision key; a
    request that also serves further steps gets a bounded ``Also serves step
    N`` hint.  Requests without step context go to a trailing
    :data:`PERMISSION_OTHER_HEADING` bucket, which is emitted only when at
    least one request carries a step.  When no request carries step context
    the whole payload is returned as one unheaded group, so the previous flat
    layout is unchanged.

    :param requests: The serialized permission requests, in payload order.
    :returns: ``(heading, [(index, request, hint), ...])`` pairs; the heading
        is empty for the flat layout.
    """
    entries = [
        (index, request)
        for index, request in enumerate(requests, start=1)
        if isinstance(request, dict)
    ]
    member_steps: dict[int, list[int]] = {}
    summaries: dict[int, str] = {}
    for index, request in entries:
        member_steps[index] = _permission_request_steps(request)
        steps = request.get("steps")
        if not isinstance(steps, list):
            continue
        for step in steps:
            if not isinstance(step, dict):
                continue
            number = step.get("step_number")
            if not isinstance(number, int) or summaries.get(number):
                continue
            text = str(step.get("summary") or "").strip()
            if text:
                summaries[number] = text

    if not any(member_steps.get(index) for index, _ in entries):
        return [("", [(index, request, "") for index, request in entries])]

    buckets: dict[int, list[tuple[int, dict[str, Any], str]]] = {}
    others: list[tuple[int, dict[str, Any], str]] = []
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
        (_permission_step_heading(number, summaries.get(number, "")), buckets[number])
        for number in sorted(buckets)
    ]
    if others:
        groups.append((PERMISSION_OTHER_HEADING, others))
    return groups


def _permission_group_coverage(
    group: list[tuple[int, dict[str, Any], str]],
) -> str:
    """Return one muted coverage line for a group of permission requests.

    The line counts the requests under a heading by the ``kind`` the payload
    carries (``outside`` / ``sensitive``), so the user sees how many paths a
    step needs: ``3 paths: 2 outside the project, 1 sensitive``.  The noun is
    singular for a single path, a zero-count category is omitted, and a
    request with a missing or unknown ``kind`` is counted in a trailing
    ``unknown`` bucket only when non-zero.

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


def _permission_group_also(group: list[tuple[int, dict[str, Any], str]]) -> str:
    """Return one muted ``Also serves step N`` line for a group of requests.

    A request that serves several plan steps is shown under its lowest number
    (see :func:`_permission_groups`).  The further steps are unioned across the
    whole group, sorted and de-duplicated, so several multi-step requests
    produce one line beneath the heading instead of one hint per row.

    :param group: The ``(index, request, hint)`` rows under one heading.
    :returns: The ``Also serves step ...`` line, or ``""`` when no request
        serves more than one step.
    """
    numbers: set[int] = set()
    for _, request, _ in group:
        numbers.update(_permission_request_steps(request)[1:])
    if not numbers:
        return ""
    return "Also serves step " + ", ".join(str(n) for n in sorted(numbers))


def _render_permission_form(
    ctx: PageContext, ask: dict[str, Any], interrupt_id: Any
) -> None:
    """Render a path-permission ask: one decision per requested path.

    Each request gets an allow-once / allow-session / deny choice; selecting
    one re-renders the rows so it shows its active state.  Submit sends the
    per-key decisions to :meth:`PageContext.submit_interrupt` (ADR-0007 update
    2026-10-08).  In-progress choices live on *ask* (the chat's stored
    interrupt), so they survive a status-region re-render and are cleared with
    the ask on resume.
    """
    requests = ask.get("requests")
    if not isinstance(requests, list) or not requests:
        requests = [{"kind": "outside", "path": ask.get("question", "")}]

    logger.debug(f"Rendering permission form\n{len(requests) = }\n{interrupt_id = }")
    decisions: dict[str, str] = ask.setdefault("choices", {})
    for request in requests:
        if isinstance(request, dict):
            decisions.setdefault(_permission_key(request), "deny")

    with ui.column().classes("w-full gap-2 p-3 border border-primary rounded"):
        count = len(requests)
        noun = "path" if count == 1 else "paths"
        ui.label(f"Permission required ({count} {noun})").classes("text-sm font-bold")
        if question := ask.get("question"):
            ui.label(str(question)).classes("text-xs text-grey-6")
        # Display-only plan-step context: the work each requested path serves.
        # Plain labels (never ``ui.html``), so the model-authored summary cannot
        # inject markup; absent context renders nothing.  Per-request step
        # context is grouped into the rows below, so these top-of-form lines are
        # kept only for a legacy payload whose step context sits on the ask.
        groups = _permission_groups(requests)
        if not any(
            _permission_request_steps(request)
            for request in requests
            if isinstance(request, dict)
        ):
            for step_line in _permission_step_lines(ask):
                ui.label(step_line).classes(PERMISSION_STEP_HEADING_CLASSES)

        # Re-render on select so the chosen option shows its active state
        # (``choice_buttons`` computes it at build time).
        @ui.refreshable
        def _rows() -> None:
            shown_group = False
            for heading, group in groups:
                if heading:
                    if shown_group:
                        # One divider between adjacent step groups, never
                        # before the first or after the last, so it mirrors
                        # the TUI's rule between request blocks.  The column's
                        # existing ``gap-2`` supplies the surrounding spacing.
                        ui.separator()
                    shown_group = True
                    # Plain label (never ``ui.html``): the model-authored step
                    # summary cannot inject markup.
                    ui.label(heading).classes(PERMISSION_STEP_HEADING_CLASSES)
                    group_coverage = _permission_group_coverage(group)
                    if group_coverage:
                        # Also a plain label, and muted via the existing token.
                        ui.label(group_coverage).classes(
                            PERMISSION_STEP_COVERAGE_CLASSES
                        )
                    group_also = _permission_group_also(group)
                    if group_also:
                        # The same muted label, so both notes read alike.
                        ui.label(group_also).classes(PERMISSION_STEP_COVERAGE_CLASSES)
                for index, request, _ in group:
                    path = _permission_path_display(request)
                    if request.get("kind") == "sensitive":
                        title = f"Sensitive file: {path}"
                    else:
                        title = f"Outside project path: {path}"
                    title = f"{index}. {title}"
                    scope = _permission_scope(request)
                    key = _permission_key(request)

                    def _set(value: str, k: str = key) -> None:
                        decisions[k] = value
                        _rows.refresh()

                    choice_buttons(
                        title,
                        _permission_options(request),
                        decisions.get(key, "deny"),
                        _set,
                        colors={"deny": "red-5", "not_a_path": "grey-7"},
                        label_width="20ch",
                        group_width="16rem",
                    )
                    if scope:
                        ui.label(scope).classes("text-xs text-grey-6")
                    _render_permission_hint(request)
                    coverage = _permission_coverage(request)
                    if coverage:
                        ui.label(coverage).classes("text-xs text-grey-6").style(
                            "white-space: pre-line"
                        )

        _rows()

        def _submit() -> None:
            logger.debug(f"Submitting permission decisions\n{decisions = }")
            ctx.submit_interrupt(
                {
                    "decisions": [
                        {"key": key, "decision": value}
                        for key, value in decisions.items()
                    ]
                },
                interrupt_id,
                False,
            )

        with ui.row().classes("gap-2"):
            ui.button("Submit", on_click=_submit).props("unelevated color=primary")
            ui.button(
                "Cancel",
                on_click=lambda: ctx.submit_interrupt(None, interrupt_id, True),
            ).props("flat color=negative")


#: Body classes that render a ``ChatBubble`` collapsed (mirrors the class
#: names set by ``chat_bubble``).
_BUBBLE_COLLAPSED_CLASSES = ("msg-collapsed", "tool-block-collapsed")
#: Body class that renders a ``ChatBubble`` expanded.
_BUBBLE_EXPANDED_CLASS = "msg-expanded"


def _bubble_children(element: Any) -> list[Any]:
    """Return the direct default-slot children of a NiceGUI element."""
    slot = getattr(element, "default_slot", None)
    return list(getattr(slot, "children", []) or [])


def _apply_bubble_collapsed(bubble: Any, role: str, collapsed: bool) -> None:
    """Show one mounted bubble collapsed or expanded, in place.

    Flips the body's line-clamp class and the toggle button's icon directly on
    the mounted widget tree instead of rebuilding the transcript, so the
    reader's scroll position is untouched (the TUI toggles a CSS class in
    place for the same reason, see ``tui/components/transcript.py``).

    Relies on ``ChatBubble``'s layout: the bubble holds one wrapper div whose
    children are ``[header?] body row``, and the row ends with the toggle
    button.

    :param bubble: The mounted ``ChatBubble`` element.
    :param role: The rendered role (a ``tool`` block uses a different
        collapsed class).
    :param collapsed: Whether the block should now be collapsed.
    """
    wrapper_children = _bubble_children(bubble)
    if not wrapper_children:
        return
    children = _bubble_children(wrapper_children[0])
    if len(children) < 2:
        return
    body, row = children[-2], children[-1]
    if collapsed:
        collapse_class = "tool-block-collapsed" if role == "tool" else "msg-collapsed"
        body.classes(remove=_BUBBLE_EXPANDED_CLASS, add=collapse_class)
        icon = "expand_more"
    else:
        body.classes(
            remove=" ".join(_BUBBLE_COLLAPSED_CLASSES), add=_BUBBLE_EXPANDED_CLASS
        )
        icon = "expand_less"
    row_children = _bubble_children(row)
    if row_children:
        row_children[-1].props(f"icon={icon}")


def _expand_handler(holder: dict[str, Any], idx: int, expanded: set[int], role: str):
    """Return the expand/collapse click handler for one bubble.

    The handler records the toggle in the chat's ``expanded`` set (still the
    source of truth for the next rebuild) and flips the mounted bubble in
    place through :func:`_apply_bubble_collapsed`, so no re-render -- and no
    viewport reset -- is triggered.

    :param holder: One-element mapping filled with the mounted bubble once it
        is constructed (the handler is created before the widget exists).
    :param idx: Absolute store index of the message.
    :param expanded: The active chat's expanded-index set (mutated in place).
    :param role: The message role.
    :returns: A click handler that accepts and ignores any event arguments.
    """

    def _toggle(*_args: Any) -> None:
        bubble = holder.get("bubble")
        if bubble is None:
            return
        if idx in expanded:
            expanded.discard(idx)
            collapsed = True
        else:
            expanded.add(idx)
            collapsed = False
        _apply_bubble_collapsed(bubble, role, collapsed)

    return _toggle


def _render_messages(ctx: PageContext) -> None:
    """Rebuild the scroll-area content (welcome or messages) for the page.

    Uses explicit clear+rebuild instead of ``@ui.refreshable``
    to avoid issues with the welcome-to-empty-chat transition.
    """
    current = ctx.chat_id
    logger.debug(
        "current=%s msgs=%d",
        current,
        len(chats.get(f"{ctx.user_id}:{current}", {}).get("messages", []))
        if current
        else 0,
    )
    # Decide the scroll/paging state once, from the store (never mutated): a
    # chat switch or a new message means "show the newest window and scroll
    # down"; any other rebuild keeps the reader's current position.
    render_key = f"{ctx.user_id}:{current}"
    render_count = len(chats.get(render_key, {}).get("messages", [])) if current else 0
    should_scroll = (
        render_key != ctx.last_rendered_chat
        or render_count > ctx.rendered_message_counts.get(render_key, 0)
    )
    ctx.chat_area.clear()
    # The turn status region is rendered from the active chat's turn status,
    # so switching chats restores that chat's own status and no stale
    # marker bleeds across.  A live run re-renders this from state too, so
    # there is a single source of truth for the region.
    render_turn_status(ctx)
    with ctx.chat_area:
        if not current:
            with (
                ui.column()
                .classes("w-full h-full items-center justify-center gap-4")
                .style("flex: 1; display: flex;")
            ):
                ui.label("Start a conversation").classes("text-xl text-grey-5")
                ui.label("Type your message below to begin").classes(
                    "text-sm text-grey-5"
                )
                # First-run setup call-to-action: point the user straight
                # at the model dialog when anything required is missing.
                missing_models = missing_required_roles(ctx.session_model_info)
                missing_keys = missing_credentials(ctx.session_model_info)
                if missing_models or missing_keys:
                    with ui.card().classes("items-center gap-1 mt-2 p-4"):
                        ui.label("Models are not configured").classes(
                            "text-sm font-bold"
                        )
                        if missing_models:
                            ui.label(
                                "Set the required models to start: "
                                + ", ".join(
                                    role.capitalize() for role in missing_models
                                )
                            ).classes("text-xs text-grey-6")
                        elif missing_keys:
                            providers = sorted(
                                {
                                    (
                                        ctx.session_model_info[role].get("credential")
                                        or {}
                                    ).get("provider", "")
                                    for role in missing_keys
                                }
                                - {""}
                            )
                            ui.label(
                                "Add an API key for: " + ", ".join(providers)
                            ).classes("text-xs text-grey-6")
                        ui.button(
                            "Choose models", on_click=ctx.model_config_dialog
                        ).props("unelevated color=primary")
        else:
            current_chat = chats.get(f"{ctx.user_id}:{current}")
            msgs = current_chat["messages"] if current_chat else []
            expanded = current_chat["expanded"] if current_chat else set()
            # Bound the mounted widget set: mount at most
            # ``MOUNTED_MESSAGE_LIMIT`` messages starting at the window's
            # stored first index.  The start is kept per chat on the page
            # context so scroll paging (older/newer batches) survives a
            # re-render; on a chat switch or a new message it resets to the
            # newest window.  ``idx`` stays the absolute index into the full
            # store list so ``expanded`` / ``on_expand`` / ``ChatBubble(...)``
            # keep addressing the right message, and the store is never
            # trimmed (``/export`` and hydration read it whole).
            # ``mounted_start_by_chat`` lives on the real ``PageContext``;
            # tolerate a lighter test/embedding context by creating the
            # mapping when it is absent (the store itself stays untouched).
            mounted_starts = getattr(ctx, "mounted_start_by_chat", None)
            if mounted_starts is None:
                mounted_starts = {}
            ctx.mounted_start_by_chat = mounted_starts
            latest_start = max(0, render_count - MOUNTED_MESSAGE_LIMIT)
            if should_scroll or render_key not in mounted_starts:
                mount_start = latest_start
            else:
                mount_start = mounted_starts[render_key]
            mount_start = max(0, min(mount_start, latest_start))
            mounted_starts[render_key] = mount_start
            mount_end = min(render_count, mount_start + MOUNTED_MESSAGE_LIMIT)
            for idx in range(mount_start, mount_end):
                msg = msgs[idx]
                collapsed = idx not in expanded
                text = msg.get("text", "")
                role = msg.get("role", "agent")
                # Toggle expand/collapse in place rather than re-running the
                # renderer: a full rebuild clears the scroll area's content,
                # which resets the reader's viewport (the TUI flips a CSS
                # class in place for the same reason).  The chat's
                # ``expanded`` set stays the source of truth for the next
                # rebuild, keyed by the absolute store ``idx``.
                bubble_holder: dict[str, Any] = {}
                bubble_holder["bubble"] = ChatBubble(
                    # A user message is rendered verbatim (no markdown), so it
                    # is not linkified into markdown links either.
                    text=text if role == "user" else linkify_md(text),
                    stamp=msg.get("stamp", ""),
                    role=role,
                    header=msg.get("header", ""),
                    mime=msg.get("mime", ""),
                    data=msg.get("data", ""),
                    meta=msg.get("meta", {}),
                    is_error=msg.get("is_error", False),
                    collapsed=collapsed,
                    idx=idx,
                    on_copy=lambda t=text: ui.run_javascript(
                        f"navigator.clipboard.writeText({json.dumps(t)})"
                    ),
                    on_expand=_expand_handler(bubble_holder, idx, expanded, role),
                )
                # Tag the bubble with its absolute store index so scroll
                # paging can anchor on it when older messages are mounted.
                # Display-only tag; a lightweight test/embedding double may not
                # expose ``classes``.
                add_classes = getattr(bubble_holder["bubble"], "classes", None)
                if callable(add_classes):
                    add_classes(f"klea-msg-{idx}")
    # Scroll to the bottom only when the shown chat changed or new messages
    # arrived; any other re-render keeps the reader's viewport where it was
    # (expanding a bubble toggles in place, and scroll paging restores the
    # anchor position, so neither re-renders to a jump).
    ctx.last_rendered_chat = render_key
    ctx.rendered_message_counts[render_key] = render_count
    if current and should_scroll:
        ctx.scroll_chat_bottom()


def _scroll_to_bottom(ctx: PageContext) -> None:
    """Scroll chat area to the bottom.

    Uses Quasar's ``setScrollPosition`` (via NiceGUI's
    ``scroll_to(pixels=99999)``) instead of raw JavaScript because
    NiceGUI batches UI updates and JS into the same WebSocket packet
     ---  by the time a ``setTimeout`` or ``requestAnimationFrame``
    callback fires the new DOM may not be laid out yet, so
    ``scrollTop = scrollHeight`` or ``scrollIntoView`` land at the
    wrong position.

    ``setScrollPosition`` is Quasar's own scroll API on
    ``QScrollArea``; it coordinates with its internal layout cycle
    so the scroll lands correctly after the content updates are
    painted.  The large pixel value is safe  ---  Quasar clamps it to
    the actual scrollable extent.
    """
    logger.debug("attempting scroll for chat=%s", ctx.chat_id)
    ctx.scroll_area.scroll_to(pixels=99999)


def _latest_mount_start(render_count: int) -> int:
    """Return the start index of the newest bounded transcript window."""
    return max(0, render_count - MOUNTED_MESSAGE_LIMIT)


async def _measure_anchor_offset(idx: int) -> float | None:
    """Return a bubble's offset from the top of the scroll viewport, in pixels.

    Used to keep the reader's position stable when older messages are mounted
    above the viewport: the offset of the previously-top bubble is captured
    before the rebuild and restored after it (the web equivalent of the TUI
    transcript anchoring on its previously-top block).

    :param idx: Absolute store index of the anchor message.
    :returns: The pixel offset, or ``None`` when the element is unavailable.
    """
    script = f"""
    return (() => {{
        const container = document.querySelector(
            '.chat-scroll-area .q-scrollarea__container');
        if (!container) return null;
        const anchor = container.querySelector('.klea-msg-{idx}');
        if (!anchor) return null;
        return anchor.getBoundingClientRect().top
            - container.getBoundingClientRect().top;
    }})();
    """
    try:
        return await ui.run_javascript(script, timeout=2.0)
    except Exception:  # noqa: BLE001 - a missing element must not break paging
        return None


async def _restore_anchor_offset(idx: int, offset: float) -> None:
    """Scroll a rebuilt transcript so *idx*'s bubble sits at *offset* again.

    :param idx: Absolute store index of the anchor message.
    :param offset: The pixel offset captured before the rebuild.
    """
    script = f"""
    (() => {{
        const container = document.querySelector(
            '.chat-scroll-area .q-scrollarea__container');
        if (!container) return;
        const anchor = container.querySelector('.klea-msg-{idx}');
        if (!anchor) return;
        const current = anchor.getBoundingClientRect().top
            - container.getBoundingClientRect().top;
        container.scrollTop += current - ({offset});
    }})();
    """
    try:
        await ui.run_javascript(script, timeout=2.0)
    except Exception:
        logger.debug("anchor offset restore failed", exc_info=True)


async def _page_older(ctx: PageContext) -> None:
    """Mount the previous batch of older messages (reached the top).

    The window's start is lowered by :data:`SCROLL_BATCH` (clamped at 0) and
    the transcript is rebuilt; the reader's offset within the previously-top
    bubble is captured and restored, so mounting above the viewport does not
    jump.  Mirrors the TUI transcript's ``_load_older``.  The store itself is
    untouched and the mounted window stays bounded by
    :data:`MOUNTED_MESSAGE_LIMIT`.

    :param ctx: The shared page context.
    """
    if ctx.loading_older or time.monotonic() < ctx.scroll_paging_until:
        return
    current = ctx.chat_id
    if not current:
        return
    render_key = f"{ctx.user_id}:{current}"
    chat = chats.get(render_key)
    msgs = chat["messages"] if chat else []
    start = ctx.mounted_start_by_chat.get(render_key, _latest_mount_start(len(msgs)))
    if start <= 0:
        return
    ctx.loading_older = True
    ctx.scroll_paging_until = time.monotonic() + 0.25
    try:
        offset = await _measure_anchor_offset(start)
        ctx.mounted_start_by_chat[render_key] = max(0, start - SCROLL_BATCH)
        _render_messages(ctx)
        if offset is not None:
            await _restore_anchor_offset(start, offset)
    finally:
        ctx.loading_older = False


async def _page_newer(ctx: PageContext) -> None:
    """Mount the next batch of newer messages (reached the bottom).

    The window's start is raised by :data:`SCROLL_BATCH` (clamped at the newest
    window) and the transcript is rebuilt, then pinned to the bottom: the
    restored blocks land below the viewport, so the bottom is the fixed point
    (the TUI transcript's ``_load_newer`` / ``_finish_load_newer``).  The
    oldest end is left behind by the bounded window and the store is never
    touched.

    :param ctx: The shared page context.
    """
    if ctx.loading_newer or time.monotonic() < ctx.scroll_paging_until:
        return
    current = ctx.chat_id
    if not current:
        return
    render_key = f"{ctx.user_id}:{current}"
    chat = chats.get(render_key)
    msgs = chat["messages"] if chat else []
    latest_start = _latest_mount_start(len(msgs))
    start = ctx.mounted_start_by_chat.get(render_key, latest_start)
    if start >= latest_start:
        return
    ctx.loading_newer = True
    ctx.scroll_paging_until = time.monotonic() + 0.25
    try:
        ctx.mounted_start_by_chat[render_key] = min(latest_start, start + SCROLL_BATCH)
        _render_messages(ctx)
        ctx.scroll_chat_bottom()
    finally:
        ctx.loading_newer = False


async def _install_scroll_listener(ctx: PageContext) -> None:
    """Attach a DOM scroll listener that pages the transcript at either edge.

    NiceGUI's ``ui.scroll_area`` does not surface scroll events, so the Quasar
    scroll container is observed directly in the browser: reaching the top
    emits ``klea_scroll_top`` and reaching the bottom emits
    ``klea_scroll_bottom`` on the element (via NiceGUI's ``emitEvent``), which
    the handlers registered in :func:`attach_chat_area` receive.  The listener
    is installed once and survives transcript re-renders.

    :param ctx: The shared page context (uses ``scroll_area``).
    """
    element_id = ctx.scroll_area.id
    script = f"""
    (() => {{
        const root = document.querySelector('.chat-scroll-area');
        if (!root) return;
        const container = root.querySelector('.q-scrollarea__container') || root;
        if (container.__kleaScrollWired) return;
        container.__kleaScrollWired = true;
        let scheduled = false;
        container.addEventListener('scroll', () => {{
            if (scheduled) return;
            scheduled = true;
            requestAnimationFrame(() => {{
                scheduled = false;
                const atTop = container.scrollTop <= 0;
                const atBottom = container.scrollTop + container.clientHeight
                    >= container.scrollHeight - 2;
                if (atTop) {{
                    emitEvent({element_id}, 'klea_scroll_top');
                }} else if (atBottom) {{
                    emitEvent({element_id}, 'klea_scroll_bottom');
                }}
            }});
        }}, {{ passive: true }});
    }})();
    """
    try:
        await ui.run_javascript(script)
    except Exception:
        logger.debug("scroll listener install failed", exc_info=True)


def attach_chat_area(ctx: PageContext) -> None:
    """Build the chat scroll area and register its render callbacks.

    Must be called with the chat tab panel as the ambient NiceGUI
    context (the surrounding layout decides where the tab panels go).

    :param ctx: The shared page context; ``chat_area``,
        ``scroll_area`` and ``turn_status_container`` are filled in here.
    """
    with ui.scroll_area().classes("w-full grow chat-scroll-area") as scroll_area:
        ctx.scroll_area = scroll_area
        ctx.chat_area = ui.column().classes("w-full")
        ctx.turn_status_container = ui.column().classes("w-full")
    # Pinned between the transcript and the composer: while a HITL ask is
    # active it is rendered here and the input row is hidden, so the ask
    # replaces the composer (mirroring the TUI; ADR-0046).
    ctx.interrupt_container = ui.column().classes("w-full")

    # Scroll paging (mirrors the TUI transcript): reaching the top mounts the
    # previous batch of stored messages and reaching the bottom re-mounts the
    # next one.  The browser-side listener emits the two events; the handlers
    # slide the bounded window without touching the store.
    async def _on_scroll_top() -> None:
        await _page_older(ctx)

    async def _on_scroll_bottom() -> None:
        await _page_newer(ctx)

    async def _install_listener() -> None:
        await _install_scroll_listener(ctx)

    scroll_area.on("klea_scroll_top", _on_scroll_top)
    scroll_area.on("klea_scroll_bottom", _on_scroll_bottom)
    ui.timer(0.1, _install_listener, once=True)

    ctx.render_chat_area = lambda: _render_messages(ctx)
    ctx.refresh_turn_status = lambda: render_turn_status(
        ctx, retry_cb=ctx.retry_cbs.get(f"{ctx.user_id}:{ctx.chat_id}")
    )
    ctx.scroll_chat_bottom = lambda: _scroll_to_bottom(ctx)
    _render_messages(ctx)
