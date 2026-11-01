#!/usr/bin/env python3
"""
Relevance-scoped evidence selection for the Planner prompt.

Decides which recorded ``StepOutput`` payloads the Planner sees in full and
which are compressed to a one-line summary plus a step reference.  No field is
added to any schema: the ``depends_on`` edge is the consumption edge, read
transitively.

Rule order (highest priority first).  Selected steps are rendered in full with
``StepOutput.render()``; every other recorded step is rendered with
``StepOutput.summary()`` plus a step reference:

1. Steps whose latest attempt failed -- status ``failed``, or the most recent
   recorded result is an error (superseded failures are not selected).
2. The transitive ``depends_on`` closure of every pending step.
3. Steps the Evaluator flagged (``step_incomplete`` verdicts).
4. The last completed (``done``) step that recorded a result.
5. The steps of the latest abandoned plan revision.

A ``review`` step records no output -- its "execution" is the user's decision --
so it is never a payload source, and a step number with no recorded output
contributes nothing.  The rule applies to the ``observations`` prompt variable;
``previous_work`` keeps its clipped continuation summaries.

This module is pure: no IO, no prompt mutation, and no length cap of its own.

File: agent_pkg/klea_agent/evidence.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from typing import NamedTuple

from fastmcp.client.client import CallToolResult

from klea_agent.schemas import (
    PlannerPlanSchema,
    PlanRevision,
    StepOutput,
    StepSchema,
)

logger = logging.getLogger(__name__)

#: Sentinel rendered when the run recorded no step output at all.
NO_EVIDENCE = "(no observations)"


class EvidenceRender(NamedTuple):
    """Outcome of one evidence-selection pass."""

    #: Step numbers rendered in full, ascending and de-duplicated.
    full_steps: tuple[int, ...]
    #: Ready-to-use block for the ``observations`` prompt variable.
    text: str


def _entries(
    step_outputs: Mapping[int, Sequence[StepOutput]], number: int
) -> tuple[StepOutput, ...]:
    """Return the recorded outputs for *number* as a tuple."""
    return tuple(step_outputs.get(number, ()))


def _latest_is_error(entries: Sequence[StepOutput]) -> bool:
    """Return whether the most recent recorded result is an error."""
    if not entries:
        return False
    latest = entries[-1]
    return isinstance(latest.result, CallToolResult) and latest.result.is_error


def _dependency_closure(
    plan: PlannerPlanSchema, by_number: Mapping[int, StepSchema]
) -> set[int]:
    """Return the transitive ``depends_on`` closure of every pending step.

    ``depends_on`` holds direct edges only, so the walk expands them with a
    visited set.  Numbers with no matching step are ignored.
    """
    pending = [step for step in plan.step_list if step.status == "pending"]
    visited: set[int] = set()
    stack = [dep for step in pending for dep in step.depends_on]
    while stack:
        number = stack.pop()
        if number in visited:
            continue
        step = by_number.get(number)
        if step is None:
            continue
        visited.add(number)
        stack.extend(step.depends_on)
    return visited


def _last_completed(
    plan: PlannerPlanSchema, step_outputs: Mapping[int, Sequence[StepOutput]]
) -> int | None:
    """Return the highest ``done`` step number that recorded a result."""
    completed = [
        step.step_number
        for step in plan.step_list
        if step.status == "done" and _entries(step_outputs, step.step_number)
    ]
    return max(completed, default=None)


def _review_numbers(
    plan: PlannerPlanSchema, abandoned: PlanRevision | None
) -> set[int]:
    """Return review-step numbers, which can never be payload sources."""
    numbers = {step.step_number for step in plan.step_list if step.kind == "review"}
    if abandoned is not None:
        numbers |= {
            step.step_number for step in abandoned.steps if step.kind == "review"
        }
    return numbers


def _render_full(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one selected step's entries in full."""
    body = "\n\n".join(entry.render() for entry in entries)
    return f"### Step {number}\n{body}"


def _render_summary(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one unselected step as summaries plus a step reference."""
    lines = [f"- {entry.summary()}" for entry in entries]
    lines.append(
        f"- Step {number}: full payload omitted; retrieve it by planning a step "
        f"that depends on {number}"
    )
    return f"### Step {number} (summary)\n" + "\n".join(lines)


def render_plan_evidence(
    plan: PlannerPlanSchema,
    step_outputs: Mapping[int, Sequence[StepOutput]],
    *,
    flagged: Iterable[int] = (),
    abandoned: PlanRevision | None = None,
) -> EvidenceRender:
    """Select the plan evidence for the Planner prompt and render it.

    Selection is the union of the ordered tiers described in the module
    docstring, rendered in ascending step order:

    1. failed steps (latest attempt);
    2. the transitive ``depends_on`` closure of every pending step;
    3. *flagged* steps (Evaluator ``step_incomplete`` verdicts);
    4. the last completed step;
    5. the steps of *abandoned* (the latest superseded plan revision).

    Selected steps are rendered in full; every other recorded step is rendered
    as ``summary()`` plus a step reference.  Review steps and step numbers with
    no recorded output contribute nothing.

    :param plan: The active plan whose steps supply the ``depends_on`` edges.
    :param step_outputs: Recorded outputs keyed by plan step number.
    :param flagged: Step numbers the Evaluator judged incomplete.
    :param abandoned: The latest abandoned plan revision, if any.
    :returns: The full-rendered step numbers and the evidence text block.
    """
    by_number: dict[int, StepSchema] = {
        step.step_number: step for step in plan.step_list
    }
    if abandoned is not None:
        for step in abandoned.steps:
            by_number.setdefault(step.step_number, step)

    recorded = {number for number in step_outputs if _entries(step_outputs, number)}
    selected: set[int] = set()

    for number in recorded:
        step = by_number.get(number)
        failed = step is not None and step.status == "failed"
        if failed or _latest_is_error(_entries(step_outputs, number)):
            selected.add(number)

    selected |= _dependency_closure(plan, by_number)
    selected |= set(flagged)

    last_completed = _last_completed(plan, step_outputs)
    if last_completed is not None:
        selected.add(last_completed)

    if abandoned is not None:
        selected |= {step.step_number for step in abandoned.steps}

    review = _review_numbers(plan, abandoned)
    full = tuple(
        sorted(number for number in selected & recorded if number not in review)
    )

    blocks = [_render_full(number, _entries(step_outputs, number)) for number in full]
    for number in sorted(recorded - set(full) - review):
        blocks.append(_render_summary(number, _entries(step_outputs, number)))

    text = "\n\n".join(blocks) if blocks else NO_EVIDENCE
    logger.debug(
        "plan evidence: full_steps=%s full=%d summarized=%d chars=%d",
        full,
        len(full),
        len(recorded - set(full) - review),
        len(text),
    )
    return EvidenceRender(full_steps=full, text=text)
