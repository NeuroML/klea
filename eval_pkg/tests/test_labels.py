# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the run label classifier (:func:`klea_eval.labels.classify_case`).

Every branch of the ADR-0059 outcome classifier is exercised directly against
its ``(status, task_success, expects_grade)`` signature, so no workspace, server
or model is involved; the module carries the ``eval`` marker only.

Covered branches:

* a failed run is ``failed`` even when it declared rubrics (``expects_grade``),
* a skipped run is ``skipped``,
* a completed run with ``task_success=True`` is ``passed``,
* a completed run with ``task_success=False`` is ``failed``,
* a completed run without a grade that expects one is ``grade-missing``,
* a completed run without a grade and without rubrics is ``ungraded``.
"""

from __future__ import annotations

import pytest
from klea_eval.labels import classify_case

pytestmark = pytest.mark.eval


def test_failed_status_with_rubrics_stays_failed() -> None:
    """A failed run that declared rubrics is ``failed``, not grade-missing."""
    label = classify_case(status="failed", task_success=None, expects_grade=True)

    assert label == "failed"


def test_skipped_status_is_skipped() -> None:
    """A skipped run is labelled ``skipped``."""
    label = classify_case(status="skipped", task_success=None, expects_grade=False)

    assert label == "skipped"


def test_completed_pass_is_passed() -> None:
    """A completed run whose grade is True is ``passed``."""
    label = classify_case(status="completed", task_success=True, expects_grade=True)

    assert label == "passed"


def test_completed_fail_is_failed() -> None:
    """A completed run whose grade is False is ``failed``."""
    label = classify_case(status="completed", task_success=False, expects_grade=True)

    assert label == "failed"


def test_completed_no_grade_expecting_grade_is_grade_missing() -> None:
    """Completed + no grade + expects a grade is ``grade-missing``."""
    label = classify_case(status="completed", task_success=None, expects_grade=True)

    assert label == "grade-missing"


def test_completed_no_grade_without_rubrics_is_ungraded() -> None:
    """Completed + no grade + no rubrics is ``ungraded``."""
    label = classify_case(status="completed", task_success=None, expects_grade=False)

    assert label == "ungraded"


def test_completed_with_rubrics_and_a_pass_is_not_grade_missing() -> None:
    """A graded completed run never falls through to ``grade-missing``."""
    label = classify_case(status="completed", task_success=True, expects_grade=True)

    assert label != "grade-missing"
    assert label == "passed"
