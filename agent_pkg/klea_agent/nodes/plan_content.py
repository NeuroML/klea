#!/usr/bin/env python3
"""
Structured plan content blocks for the status pane

Turns a :class:`klea_agent.schemas.PlanSchema` into the neutral
:class:`klea_utils.content_blocks.ContentBlock` form, so the Planner and the
Evaluator emit one and the same structured plan section (``key="plan"``)
alongside the preformatted ``display`` fallback.  See
``devdocs/system/streams.md`` and ``utils_pkg/klea_utils/content_blocks.py``.

File: klea_agent/nodes/plan_content.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging

from klea_utils.content_blocks import ContentBlock, Item, ItemsBlock

from klea_agent.schemas import PlanSchema, StepSchema

logger = logging.getLogger(__name__)

#: Item role for a resolved, successful step.
ROLE_OK = "ok"
#: Item role for a failed step.
ROLE_ERROR = "error"
#: Item role for a deliberately skipped step.
ROLE_SKIPPED = "plain"
#: Item role for the plan's current (next runnable) step.
ROLE_CURRENT = "current"
#: Item role for a pending step that is not yet runnable.
ROLE_PENDING = "pending"


def _step_role(step: StepSchema, current_number: int | None) -> str:
    """Return the display role for one plan step.

    Mirrors the precedence of :meth:`klea_agent.schemas.StepSchema.status_label`:
    a resolved step keeps its status role even when its number is the plan's
    current number, and only a ``pending`` step can be ``current``.

    :param step: The plan step to classify.
    :param current_number: The plan's current step number, or ``None`` when no
        step is runnable.
    :returns: One of the ``ROLE_*`` constants.
    """
    if step.status == "done":
        return ROLE_OK
    if step.status == "failed":
        return ROLE_ERROR
    if step.status == "skipped":
        return ROLE_SKIPPED
    if step.status == "pending" and step.step_number == current_number:
        return ROLE_CURRENT
    return ROLE_PENDING


def plan_content_blocks(plan: PlanSchema) -> list[ContentBlock]:
    """Return the plan as structured content blocks for the status pane.

    A single ``items`` block lists the plan's steps in ``step_list`` order, one
    item per step, with the step status mapped to an item role (see
    :func:`_step_role`).  The per-step prompt-only ``details`` field is
    deliberately not rendered here.

    The result is additive: producers keep setting ``display`` (the
    preformatted fallback), ``key`` and ``preformatted``, so a frontend that
    does not understand these blocks still renders the plan unchanged.

    :param plan: The plan to render.
    :returns: The content blocks; a single ``items`` block, possibly empty when
        the plan has no steps.
    """
    current = plan.current_step()
    current_number = current.step_number if current is not None else None
    items = [
        Item(
            text=f"Step {step.step_number}: {step.summary}",
            role=_step_role(step, current_number),
        )
        for step in plan.step_list
    ]
    logger.debug(
        "Built %d plan content item(s) for %d step(s) (status=%s)",
        len(items),
        len(plan.step_list),
        plan.status,
    )
    return [ItemsBlock(items=items)]
