#!/usr/bin/env python3
"""
Human-in-the-loop interrupt helpers for the chat endpoints (ADR-0046).

These read a thread's pending interrupt from its checkpoint, render the
question(s) and the user's answer for the transcript, and validate an
incoming request against the thread's paused state (a paused thread may
only be continued by an answer or a cancel).  The endpoint runners in
:mod:`klea_utils.api.chat_core` call :func:`_prepare_chat_request` before
invoking the graph.

File: klea_utils/api/hitl.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException

logger = logging.getLogger(__name__)


def _snapshot_interrupts(snapshot: Any) -> tuple[Any, ...]:
    """Return the interrupt objects carried by a checkpoint state snapshot."""
    tasks = getattr(snapshot, "tasks", None)
    if not isinstance(tasks, (list, tuple)):
        return ()
    interrupts: list[Any] = []
    for task in tasks:
        interrupts.extend(getattr(task, "interrupts", ()) or ())
    return tuple(interrupts)


async def _pending_interrupts(graph: Any, thread_id: str) -> tuple[Any, ...]:
    """Return the interrupt objects a paused thread is waiting on (ADR-0046).

    Reads the thread's checkpoint state and collects every pending task's
    interrupts.  Returns an empty tuple when the graph has no checkpointer,
    the thread has no checkpoint, or the state cannot be read.

    :param graph: The :class:`~klea_utils.graph.base.BaseLangGraph` instance.
    :param thread_id: The checkpoint thread identifier.
    :returns: The pending interrupt objects (empty when none).
    """
    compiled = getattr(graph, "graph", None)
    if compiled is None or getattr(graph, "checkpointer", None) is None:
        return ()
    config = {"configurable": {"thread_id": thread_id}}
    try:
        snapshot = await compiled.aget_state(config)
    except Exception:  # noqa: BLE001 - a state read must never break a request
        logger.debug("Could not read checkpoint state for thread=%s", thread_id)
        return ()
    return _snapshot_interrupts(snapshot)


def _interrupt_question(intr: Any) -> str:
    """Render an ``Interrupt`` payload as the question text for the transcript.

    :param intr: A ``langgraph.types.Interrupt``.
    :returns: The blocked steps' questions, joined by newlines.
    """
    value = getattr(intr, "value", None)
    if isinstance(value, Mapping):
        questions = value.get("questions")
        if isinstance(questions, list):
            return "\n".join(
                str(question.get("question", ""))
                for question in questions
                if isinstance(question, Mapping)
            )
        return str(value.get("question", "")) or json.dumps(dict(value), default=str)
    return str(value)


def _interrupt_event_question(event: Mapping[str, Any]) -> str:
    """Render an ``interrupt`` stream event as the question text."""
    data = event.get("data")
    if not isinstance(data, Mapping):
        return ""
    questions = data.get("questions")
    if isinstance(questions, list):
        return "\n".join(
            str(question.get("question", ""))
            for question in questions
            if isinstance(question, Mapping)
        )
    return str(data.get("question", ""))


#: Human-readable labels for the review decision literals (ADR-0046).  The
#: wire values stay ``approve`` / ``revise``; only the transcript text differs.
_REVIEW_DECISION_LABELS = {
    "approve": "Plan approved",
    "revise": "Revision requested",
}


def render_interrupt_response(value: Mapping[str, Any]) -> str:
    """Render an interrupt resume mapping as the user turn for the transcript.

    Input answers (``answers``) are joined; a review ``decision`` is shown in
    human-readable form (``approve`` -> "Plan approved", ``revise`` ->
    "Revision requested: <feedback>").  This is display only: the wire values
    are unchanged.

    Shared by ``chat_core`` (the persisted user row) and the web UI (the live
    transcript) so the two cannot drift.
    """
    answers = value.get("answers")
    if isinstance(answers, list):
        return "; ".join(str(answer) for answer in answers)
    decision = str(value.get("decision", ""))
    if decision:
        label = _REVIEW_DECISION_LABELS.get(decision, decision)
        feedback = str(value.get("feedback", ""))
        return f"{label}: {feedback}" if feedback else label
    return json.dumps(dict(value), default=str)


def _resolve_request_action(
    *,
    resume: bool,
    interrupt_response: Mapping[str, Any] | None,
    interrupt_cancel: bool,
) -> str:
    """Return the request action: ``query`` | ``resume`` | ``answer`` | ``cancel``.

    Exactly one of ``resume``, ``interrupt_response`` and ``interrupt_cancel``
    may be set.

    :raises HTTPException: 400 when more than one action is set.
    """
    chosen = [
        name
        for name, flag in (
            ("resume", resume),
            ("interrupt_response", interrupt_response is not None),
            ("interrupt_cancel", interrupt_cancel),
        )
        if flag
    ]
    if len(chosen) > 1:
        raise HTTPException(
            status_code=400,
            detail="provide only one of resume, interrupt_response, interrupt_cancel",
        )
    if interrupt_cancel:
        return "cancel"
    if interrupt_response is not None:
        return "answer"
    if resume:
        return "resume"
    return "query"


async def _prepare_chat_request(
    graph: Any,
    *,
    thread_id: str,
    query: str | None,
    resume: bool,
    interrupt_response: Mapping[str, Any] | None,
    interrupt_cancel: bool,
    interrupt_id: str | None,
) -> tuple[str, Any, str | None]:
    """Validate a chat request against the thread's checkpoint state.

    Enforces the HITL invariant: while a thread is paused at an interrupt,
    only an answer or a cancel may continue it (a plain query or a failure
    resume is rejected).  Returns the action, the graph input (a query string,
    ``None`` for a failure resume, or a :class:`~langgraph.types.Command` for an
    answer/cancel), and the user turn to persist (or ``None``).

    :raises HTTPException: 400 on a malformed request; 409 when the request
        conflicts with the thread's paused state.
    """
    # Lazy: only the interrupt path needs ``Command``.
    from langgraph.types import Command

    action = _resolve_request_action(
        resume=resume,
        interrupt_response=interrupt_response,
        interrupt_cancel=interrupt_cancel,
    )
    pending = await _pending_interrupts(graph, thread_id)

    text = (query or "").strip()
    if action == "query":
        if pending:
            raise HTTPException(
                status_code=409, detail="This chat is awaiting your answer."
            )
        if not text:
            raise HTTPException(status_code=400, detail="query is required")
        return "query", text, text

    # resume / answer / cancel carry no query.
    if text:
        raise HTTPException(
            status_code=400,
            detail="query must be empty for resume/interrupt actions",
        )

    if action == "resume":
        if pending:
            raise HTTPException(
                status_code=409, detail="This chat is awaiting your answer."
            )
        return "resume", None, None

    # answer / cancel
    if not pending:
        raise HTTPException(status_code=409, detail="Nothing to answer for this chat.")
    if interrupt_id is not None and interrupt_id not in {
        getattr(intr, "id", None) for intr in pending
    }:
        raise HTTPException(status_code=409, detail="Stale interrupt answer.")

    if action == "cancel":
        return "cancel", Command(resume={"action": "cancel"}), None
    value = {"action": "answer", **dict(interrupt_response or {})}
    return "answer", Command(resume=value), render_interrupt_response(value)
