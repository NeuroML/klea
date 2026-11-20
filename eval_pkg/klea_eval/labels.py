# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Shared read-time labels for a stored case run.

The stored :class:`klea_eval.results.RunMetadata` envelope records what the
harness *observed* -- a ``status`` of ``completed``, ``failed`` or ``skipped``
-- but it does not record an outcome, because a ``completed`` run may still be
ungraded (no rubric was declared) or grade-missing (a rubric was declared but
no deterministic check produced a grade yet).  Every reader that renders a run (the CLI,
reports, ad-hoc scripts) therefore has to map the envelope plus the stored
``task_success`` metric onto one display label.

This module is that single mapping.  :func:`classify_case` is pure and total:
it never raises, never infers a grade and never promotes a status, so two
readers with the same inputs always agree.  The rule order below is
significant and the first match wins:

1. ``status == "failed"``           -> :data:`CASE_FAILED`
2. ``status == "skipped"``          -> :data:`CASE_SKIPPED`
3. ``completed`` and ``True``       -> :data:`CASE_PASSED`
4. ``completed`` and ``False``      -> :data:`CASE_FAILED`
5. ``completed``, ``None``, graded  -> :data:`CASE_GRADE_MISSING`
6. ``completed``, ``None``, plain   -> :data:`CASE_UNGRADED`

A failed run that declares rubrics is a *failed* run: an infrastructure or
execution fault must never be reported as a grading problem (branch 1 is
tested before the ``expects_grade`` branches).
"""

from __future__ import annotations

import logging

LOGGER = logging.getLogger(__name__)

#: Label for a completed run whose ``task_success`` metric is ``True``.
CASE_PASSED = "passed"

#: Label for a failed run, or for a completed run graded ``False``.
CASE_FAILED = "failed"

#: Label for a run whose case was already complete in the workspace.
CASE_SKIPPED = "skipped"

#: Label for a completed, ungraded run whose case declares no rubric.
CASE_UNGRADED = "ungraded"

#: Label for a completed run whose case declares rubrics but has no verdict.
CASE_GRADE_MISSING = "grade-missing"

#: Envelope status of a run that did not finish its turns.
STATUS_FAILED = "failed"

#: Envelope status of a run that was skipped because it already existed.
STATUS_SKIPPED = "skipped"

#: Envelope status of a run that executed every turn.
STATUS_COMPLETED = "completed"


def classify_case(
    *,
    status: str,
    task_success: bool | None,
    expects_grade: bool,
) -> str:
    """Return the display label for one stored case run.

    Args:
        status: Envelope status recorded in ``run.json``.
        task_success: Stored ``task_success`` metric, or ``None`` when the run
            has no graded verdict.
        expects_grade: Whether the case declares rubrics, as recorded in
            ``run.json``.

    Returns:
        One of :data:`CASE_PASSED`, :data:`CASE_FAILED`, :data:`CASE_SKIPPED`,
        :data:`CASE_UNGRADED` or :data:`CASE_GRADE_MISSING`.
    """
    if status == STATUS_FAILED:
        # A failed run is failed even when its case declares rubrics: an
        # execution fault is not a missing grade.
        label = CASE_FAILED
    elif status == STATUS_SKIPPED:
        label = CASE_SKIPPED
    elif task_success is True:
        label = CASE_PASSED
    elif task_success is False:
        label = CASE_FAILED
    elif expects_grade:
        label = CASE_GRADE_MISSING
    else:
        label = CASE_UNGRADED

    LOGGER.debug(
        "classified case (status=%s, task_success=%r, expects_grade=%s) -> %s",
        status,
        task_success,
        expects_grade,
        label,
    )
    return label
