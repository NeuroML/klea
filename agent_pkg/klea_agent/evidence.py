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

Full renders are admitted under a character budget (:data:`MAX_EVIDENCE_CHARS`,
overridable per call): the first selected step that would push the block past
the budget is demoted to the summary form, together with every selected step
after it, so a lower-priority payload is never admitted once a higher-priority
one was demoted, and no step disappears -- a demoted step keeps its number and
its step reference.  This module is pure: no IO, no prompt mutation, and no
truncation of a single payload (a step is either rendered in full or
summarised).

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

#: Character budget for the Planner evidence block.  Full renders are admitted
#: in priority order until the next one would exceed this; the remaining
#: selected steps (and every unselected recorded step) use the one-line summary
#: form plus a step reference.  A plain constant, like the other prompt caps: a
#: safety margin against a small model window, not a judgement about content.
MAX_EVIDENCE_CHARS = 24_000

#: Separator between rendered evidence blocks.  The budget reserves its length
#: per block, so the joined text stays within :data:`MAX_EVIDENCE_CHARS`.
_BLOCK_SEPARATOR = "\n\n"


class EvidenceRender(NamedTuple):
    """Outcome of one evidence-selection pass."""

    #: Step numbers rendered in full (post-budget), ascending and de-duplicated.
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


def depends_on_closure(
    by_number: Mapping[int, StepSchema], seeds: Iterable[int]
) -> set[int]:
    """Return the transitive ``depends_on`` closure of *seeds*.

    ``depends_on`` holds direct edges only, so the walk expands them with a
    visited set.  Numbers with no matching step are ignored, and a seed is
    included only when it is reachable through an edge or names a known step.

    :param by_number: Steps keyed by step number, supplying the edges.
    :param seeds: Direct ``depends_on`` values to start the walk from.
    :returns: Every step number reachable from *seeds*.
    """
    visited: set[int] = set()
    stack = list(seeds)
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


def _dependency_closure(
    plan: PlannerPlanSchema, by_number: Mapping[int, StepSchema]
) -> set[int]:
    """Return the transitive ``depends_on`` closure of every pending step.

    The pending steps' direct ``depends_on`` edges seed
    :func:`depends_on_closure`, so the behaviour is unchanged.
    """
    pending = [step for step in plan.step_list if step.status == "pending"]
    return depends_on_closure(
        by_number, [dep for step in pending for dep in step.depends_on]
    )


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


def _render_observations_full(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one step exactly as ``observations_text`` groups it."""
    rendered = "".join(
        f"\n\n{ind}: {entry.render()}" for ind, entry in enumerate(entries)
    )
    return f"Step {number}:\n{rendered}"


def _render_observations_reduced(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one demoted step as entry summaries plus a neutral note.

    No tool-specific wording is used here: unlike the Planner's summary line,
    the terminal answer node cannot plan a follow-up step, so the omitted
    payload is simply noted as over budget.
    """
    rendered = "".join(
        f"\n\n{ind}: {entry.summary()}" for ind, entry in enumerate(entries)
    )
    rendered += "\n\n(full payload omitted: over the evidence budget)"
    return f"Step {number}:\n{rendered}"


def render_step_observations(
    step_outputs: Mapping[int, Sequence[StepOutput]],
    *,
    numbers: Iterable[int] | None = None,
    max_chars: int = MAX_EVIDENCE_CHARS,
) -> str:
    """Render recorded step outputs as the ``observations_text`` block.

    Renders the same ``Step N:`` grouping and ``{i}: {entry.render()}`` entries
    as :meth:`KleaAgentState.observations_text`, but admits full renders under
    *max_chars* in priority order: a step whose latest recorded result is an
    error first, then the remaining steps newest-first.  The first step whose
    full render would push the running length plus a separator past *max_chars*
    is demoted to its entry summaries plus a neutral omission line, together
    with every later step, so no single payload is clipped mid-text and no step
    disappears.

    :param step_outputs: Recorded outputs keyed by plan step number.
    :param numbers: Step numbers to render; ``None`` means every recorded step.
    :param max_chars: Character budget for the rendered block.
    :returns: The ready-to-use ``observations`` prompt block, or
        :data:`NO_EVIDENCE` when nothing is selected.
    """
    recorded = sorted(n for n in step_outputs if _entries(step_outputs, n))
    if numbers is not None:
        wanted = set(numbers)
        recorded = [n for n in recorded if n in wanted]
    if not recorded:
        return NO_EVIDENCE

    errored = [n for n in recorded if _latest_is_error(_entries(step_outputs, n))]
    errored_set = set(errored)
    ordered = errored + [n for n in reversed(recorded) if n not in errored_set]

    full: list[int] = []
    budget = 0
    for number in ordered:
        block = _render_observations_full(number, _entries(step_outputs, number))
        cost = len(block) + len(_BLOCK_SEPARATOR)
        if budget + cost > max_chars:
            break
        budget += cost
        full.append(number)

    full_set = set(full)
    blocks = [
        _render_observations_full(n, _entries(step_outputs, n))
        for n in sorted(full_set)
    ]
    for number in recorded:
        if number not in full_set:
            blocks.append(
                _render_observations_reduced(number, _entries(step_outputs, number))
            )

    text = _BLOCK_SEPARATOR.join(blocks)
    logger.debug(
        "step observations: full=%d summarised=%d chars=%d",
        len(full_set),
        len(recorded) - len(full_set),
        len(text),
    )
    return text


def render_plan_evidence(
    plan: PlannerPlanSchema,
    step_outputs: Mapping[int, Sequence[StepOutput]],
    *,
    flagged: Iterable[int] = (),
    abandoned: PlanRevision | None = None,
    max_chars: int = MAX_EVIDENCE_CHARS,
) -> EvidenceRender:
    """Select the plan evidence for the Planner prompt and render it.

    Selection is the union of the ordered tiers described in the module
    docstring:

    1. failed steps (latest attempt);
    2. the transitive ``depends_on`` closure of every pending step;
    3. *flagged* steps (Evaluator ``step_incomplete`` verdicts);
    4. the last completed step;
    5. the steps of *abandoned* (the latest superseded plan revision).

    Selected steps are rendered in full and every other recorded step as
    ``summary()`` plus a step reference; both sections are emitted in ascending
    step order.  Review steps and step numbers with no recorded output
    contribute nothing.

    The full renders are admitted in tier order under *max_chars*: the first
    selected step whose full render would push the block past the budget is
    demoted to the summary form, together with every selected step after it, so
    a lower-priority payload is never admitted once a higher-priority one was
    demoted.  A demoted step keeps its number and its step reference, so the
    budget changes a step's presentation, never its visibility.

    :param plan: The active plan whose steps supply the ``depends_on`` edges.
    :param step_outputs: Recorded outputs keyed by plan step number.
    :param flagged: Step numbers the Evaluator judged incomplete.
    :param abandoned: The latest abandoned plan revision, if any.
    :param max_chars: Character budget for the rendered evidence block.
    :returns: The post-budget full-rendered step numbers and the evidence text.
    """
    by_number: dict[int, StepSchema] = {
        step.step_number: step for step in plan.step_list
    }
    if abandoned is not None:
        for step in abandoned.steps:
            by_number.setdefault(step.step_number, step)

    recorded = {number for number in step_outputs if _entries(step_outputs, number)}
    candidates = recorded - _review_numbers(plan, abandoned)

    # Tier 1: ``failed`` status, or an error as the most recent recorded result
    # (a superseded failure is not selected).
    failed: list[int] = []
    for number in sorted(candidates):
        step = by_number.get(number)
        if (step is not None and step.status == "failed") or _latest_is_error(
            _entries(step_outputs, number)
        ):
            failed.append(number)

    # The tiers are a list, not a set union: the budget below admits full
    # renders in exactly this priority order.
    tiers: list[list[int]] = [
        failed,
        sorted(_dependency_closure(plan, by_number) & candidates),
        sorted(set(flagged) & candidates),
    ]
    last_completed = _last_completed(plan, step_outputs)
    if last_completed is not None and last_completed in candidates:
        tiers.append([last_completed])
    if abandoned is not None:
        tiers.append(
            sorted(
                step.step_number
                for step in abandoned.steps
                if step.step_number in candidates
            )
        )

    # Flatten the tiers, keeping the first (highest-priority) occurrence.
    selected: list[int] = []
    seen: set[int] = set()
    for tier in tiers:
        for number in tier:
            if number in seen:
                continue
            seen.add(number)
            selected.append(number)

    # Admit full renders in priority order until the budget would be exceeded;
    # that step and every later one fall back to the summary form.
    full_list: list[int] = []
    budget = 0
    for number in selected:
        block = _render_full(number, _entries(step_outputs, number))
        cost = len(block) + len(_BLOCK_SEPARATOR)
        if budget + cost > max_chars:
            break
        budget += cost
        full_list.append(number)

    full = tuple(sorted(full_list))
    blocks = [_render_full(number, _entries(step_outputs, number)) for number in full]
    for number in sorted(candidates - set(full_list)):
        blocks.append(_render_summary(number, _entries(step_outputs, number)))

    text = _BLOCK_SEPARATOR.join(blocks) if blocks else NO_EVIDENCE
    logger.debug(
        "plan evidence: full_steps=%s full=%d summarized=%d chars=%d",
        full,
        len(full),
        len(candidates - set(full_list)),
        len(text),
    )
    return EvidenceRender(full_steps=full, text=text)
