#!/usr/bin/env python3
"""
Relevance-scoped evidence selection for the Planner prompt.

Decides which recorded ``StepOutput`` payloads the Planner sees in full and
which are compressed to a one-line summary plus a step reference.  No field is
added to any schema: the ``depends_on`` edge is the consumption edge, read
transitively.

Rule order (highest priority first).  Selected steps are rendered in full with
``StepOutput.render()``; every other recorded step is rendered with
``StepOutput.evidence_summary()`` (the bounded call signature and the failure
reason) plus a step reference:

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

Selected payloads are never length-capped here: the caller controls the scope
(the Evaluator passes only its current batch plus the ``depends_on`` closure),
and tool implementations bound their own output.  This module is pure: no IO,
no prompt mutation, and no truncation of a payload.

File: agent_pkg/klea_agent/evidence.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence

from klea_agent.schemas import (
    PlannerPlanSchema,
    PlanRevision,
    StepOutput,
    StepSchema,
)

logger = logging.getLogger(__name__)

#: Sentinel rendered when the run recorded no step output at all.
NO_EVIDENCE = "(no observations)"

#: Separator between rendered evidence blocks.
_BLOCK_SEPARATOR = "\n\n"


def _entries(
    step_outputs: Mapping[int, Sequence[StepOutput]], number: int
) -> tuple[StepOutput, ...]:
    """Return the recorded outputs for *number* as a tuple."""
    return tuple(step_outputs.get(number, ()))


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


def _render_entries(entries: Sequence[StepOutput], *, indexed: bool) -> str:
    """Render entries in full, optionally with the per-step index labels."""
    if indexed:
        return "".join(
            f"\n\n{ind}: {entry.render()}" for ind, entry in enumerate(entries)
        )
    return "\n\n".join(entry.render() for entry in entries)


def _render_full(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one selected step for the Planner."""
    return f"### Step {number}\n{_render_entries(entries, indexed=False)}"


def _render_summary(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one unselected step as summaries plus a step reference."""
    lines = [f"- {entry.evidence_summary()}" for entry in entries]
    lines.append(
        f"- Step {number}: full payload omitted; retrieve it by planning a step "
        f"that depends on {number}"
    )
    return f"### Step {number} (summary)\n" + "\n".join(lines)


def _render_observations_full(number: int, entries: Sequence[StepOutput]) -> str:
    """Render one step exactly as ``observations_text`` groups it."""
    return f"Step {number}:\n{_render_entries(entries, indexed=True)}"


def render_step_observations(
    step_outputs: Mapping[int, Sequence[StepOutput]],
    *,
    numbers: Iterable[int] | None = None,
) -> str:
    """Render recorded step outputs as the ``observations_text`` block.

    Renders the same ``Step N:`` grouping and ``{i}: {entry.render()}`` entries
    as :meth:`KleaAgentState.observations_text`.  Every selected step is
    rendered in full: the caller decides what to show (for example the
    Evaluator's current batch plus its ``depends_on`` closure), and the model's
    context window -- not a fixed character cap -- is the only real bound.
    Tool implementations already bound their own output (``read_file`` pages,
    ``run_command`` stream caps, ``grep`` result limits).

    :param step_outputs: Recorded outputs keyed by plan step number.
    :param numbers: Step numbers to render; ``None`` means every recorded step.
    :returns: The ready-to-use ``observations`` prompt block, or
        :data:`NO_EVIDENCE` when nothing is selected.
    """
    recorded = sorted(n for n in step_outputs if _entries(step_outputs, n))
    if numbers is not None:
        wanted = set(numbers)
        recorded = [n for n in recorded if n in wanted]
    if not recorded:
        return NO_EVIDENCE

    blocks = [
        _render_observations_full(number, _entries(step_outputs, number))
        for number in recorded
    ]
    text = _BLOCK_SEPARATOR.join(blocks)
    logger.debug(
        "step observations: steps=%d chars=%d",
        len(recorded),
        len(text),
    )
    return text


def render_plan_evidence(
    plan: PlannerPlanSchema,
    step_outputs: Mapping[int, Sequence[StepOutput]],
    *,
    flagged: Iterable[int] = (),
    abandoned: PlanRevision | None = None,
) -> str:
    """Select the plan evidence for the Planner prompt and render it.

    Selection is the union of the ordered tiers described in the module
    docstring:

    1. failed steps (latest attempt);
    2. the transitive ``depends_on`` closure of every pending step;
    3. *flagged* steps (Evaluator ``step_incomplete`` verdicts);
    4. the last completed step;
    5. the steps of *abandoned* (the latest superseded plan revision).

    Selected steps are rendered in full and every other recorded step as
    ``evidence_summary()`` plus a step reference; both sections are emitted in
    ascending step order.  Review steps and step numbers with no recorded
    output contribute nothing.

    :param plan: The active plan whose steps supply the ``depends_on`` edges.
    :param step_outputs: Recorded outputs keyed by plan step number.
    :param flagged: Step numbers the Evaluator judged incomplete.
    :param abandoned: The latest abandoned plan revision, if any.
    :returns: The ready-to-use ``observations`` prompt block.
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
        entries = _entries(step_outputs, number)
        if (step is not None and step.status == "failed") or (
            entries and entries[-1].is_error
        ):
            failed.append(number)

    pending = [step for step in plan.step_list if step.status == "pending"]
    pending_closure = depends_on_closure(
        by_number, [dep for step in pending for dep in step.depends_on]
    )
    tiers: list[list[int]] = [
        failed,
        sorted(pending_closure & candidates),
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

    full = tuple(sorted(selected))
    blocks = [_render_full(number, _entries(step_outputs, number)) for number in full]
    for number in sorted(candidates - set(selected)):
        blocks.append(_render_summary(number, _entries(step_outputs, number)))

    text = _BLOCK_SEPARATOR.join(blocks) if blocks else NO_EVIDENCE
    logger.debug(
        "plan evidence: full_steps=%s summarized=%d chars=%d",
        full,
        len(candidates - set(selected)),
        len(text),
    )
    return text
