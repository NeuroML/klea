# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Suite scoring and aggregation for the eval harness (ADR-0060 Aggregate).

The module is the read side of the ADR-0060 Aggregate stage.  It turns the
per-run evidence that ADR-0058 defines into two suite-level numbers:

- ``suite_score``: the mean of the per-case scores;
- ``pass_at_k``: passed cases over considered cases, ``k`` being how many of a
  case's stored attempts are inspected (``k=1`` by default, D10).

Aggregation reads ``runs/index.jsonl`` only, through
:func:`klea_eval.results.read_index`, and never scans the run directories: the
index is a lookup and listing entry point (ADR-0059), and the suite aggregate
is derived from the runs it names.  A named run's metrics are read through
:func:`klea_eval.metrics.load_run`, so the per-run envelope schema of ADR-0058
is reused rather than redefined.

Scoring is read-only: this module writes no file, no run directory and no index
line.  :meth:`SuiteAggregate.to_dict` is the in-memory, JSON-ready form that a
later API or reporter renders.

Per-case score: the mean of the numeric rubric scores recorded in the run's
turn records, else ``1.0`` when the run's ``task_success`` is true and ``0.0``
otherwise.  A case is passed when any of its selected runs reports
``task_success`` true; a case whose metrics cannot be read counts as failed and
is listed in ``missing_runs`` without aborting the aggregate.

Deliberately deferred, all belonging to later phases of the implementation plan:

- LLM-judge rubric scoring (D9) and evaluation of the rubric ``criteria`` text:
  :class:`klea_eval.cases.Rubric` carries only a type and free-text criteria, so
  no deterministic check can score them yet;
- cost derivation: no price table exists, so ``cost_estimate`` is only relayed
  from the per-run metrics and summed here;
- ``transcript.jsonl`` writing, HITL ``interrupt`` resume, retention (P8), the
  in-process scheduler (P9), a persisted suite report artifact and the Markdown
  reporter, and the CLI/API exposure of :func:`aggregate_suite` (P8).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klea_eval import metrics, results, workspace

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_K",
    "CaseScore",
    "ScoringError",
    "SuiteAggregate",
    "aggregate_suite",
    "score_run",
]

#: Default number of attempts per case inspected by ``pass_at_k`` (D10).
DEFAULT_K = 1


class ScoringError(Exception):
    """Raised for an unusable scoring or aggregation request."""


@dataclass(frozen=True, slots=True)
class CaseScore:
    """The score of one stored run, as the suite aggregate sees it."""

    run_id: str
    case_id: str
    score: float
    passed: bool
    status: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    cost_estimate: float | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the case score as a plain, JSON-ready mapping."""
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "score": self.score,
            "passed": self.passed,
            "status": self.status,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "cost_estimate": self.cost_estimate,
            "error": self.error,
        }


@dataclass(frozen=True, slots=True)
class SuiteAggregate:
    """The ADR-0060 suite-level aggregate, computed from ``runs/index.jsonl``."""

    root: str | None
    k: int = DEFAULT_K
    cases: tuple[CaseScore, ...] = ()
    case_count: int = 0
    pass_count: int = 0
    suite_score: float = 0.0
    pass_at_k: float = 0.0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    cost_estimate: float = 0.0
    missing_runs: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """Return the suite aggregate as a plain, JSON-ready mapping."""
        return {
            "root": self.root,
            "k": self.k,
            "cases": [case.to_dict() for case in self.cases],
            "case_count": self.case_count,
            "pass_count": self.pass_count,
            "suite_score": self.suite_score,
            "pass_at_k": self.pass_at_k,
            "total_input_tokens": self.total_input_tokens,
            "total_output_tokens": self.total_output_tokens,
            "total_cached_tokens": self.total_cached_tokens,
            "cost_estimate": self.cost_estimate,
            "missing_runs": list(self.missing_runs),
        }


def score_run(run: metrics.RunMetrics) -> CaseScore:
    """Score one run from the per-run metrics summary.

    The score is the mean of the numeric rubric scores recorded on its turns;
    when no numeric score exists it falls back to ``1.0`` for a run whose
    ``task_success`` is true and ``0.0`` otherwise.  ``passed`` follows
    ``task_success`` alone, so a run with unknown success contributes a score
    but never a pass.
    """
    if not isinstance(run, metrics.RunMetrics):
        raise ScoringError(f"expected a RunMetrics, got {type(run).__name__}")

    numeric = _numeric_scores(run)
    passed = run.task_success is True
    if numeric:
        score = sum(numeric) / len(numeric)
    elif passed:
        score = 1.0
    else:
        score = 0.0

    logger.debug(
        f"{run.run_id = }, {run.case_id = }, {score = }, {passed = }, {len(numeric) = }"
    )
    return CaseScore(
        run_id=run.run_id,
        case_id=run.case_id,
        score=score,
        passed=passed,
        status=run.status,
        total_input_tokens=run.total_input_tokens,
        total_output_tokens=run.total_output_tokens,
        total_cached_tokens=run.total_cached_tokens,
        cost_estimate=run.cost_estimate,
        error=run.error,
    )


def aggregate_suite(
    root: str | Path | None = None,
    *,
    k: int = DEFAULT_K,
    case_ids: Sequence[str] | None = None,
) -> SuiteAggregate:
    """Aggregate the runs named by ``runs/index.jsonl`` into a suite summary.

    Entries are grouped by ``case_id`` in first-index-occurrence order, deduped
    by ``run_id`` and restricted to completed runs; only the first ``k``
    attempts of a case are inspected.  ``case_ids`` optionally narrows the
    aggregate to the named cases.  A run whose metrics cannot be read is
    recorded in ``missing_runs`` with score ``0.0`` and does not abort the
    aggregate.  An empty index yields an empty aggregate.
    """
    logger.debug(f"{root = }, {k = }, {case_ids = }")

    if isinstance(k, bool) or not isinstance(k, int):
        raise ScoringError(f"k must be an integer, got {type(k).__name__}")
    if k < 1:
        raise ScoringError(f"k must be at least 1, got {k}")

    selected = _selected_ids(case_ids)
    entries = results.read_index(root)
    logger.debug(f"{len(entries) = }")

    groups = _group_entries(entries, selected, k)
    root_path = Path(root) if root is not None else None

    cases: list[CaseScore] = []
    missing: list[str] = []
    case_score_values: list[float] = []
    pass_count = 0

    for case_id, chosen in groups.items():
        scores: list[float] = []
        passed_any = False
        for entry in chosen:
            summary = _load_summary(root_path, entry.run_id, case_id, missing)
            if summary is None:
                scores.append(0.0)
                continue
            case = score_run(summary)
            cases.append(case)
            scores.append(case.score)
            if case.passed:
                passed_any = True
        if scores:
            case_score_values.append(sum(scores) / len(scores))
        if passed_any:
            pass_count += 1

    case_count = len(case_score_values)
    suite_score = sum(case_score_values) / case_count if case_count else 0.0
    pass_at_k = pass_count / case_count if case_count else 0.0
    costs = [case.cost_estimate for case in cases if case.cost_estimate is not None]

    aggregate = SuiteAggregate(
        root=str(root_path) if root_path is not None else None,
        k=k,
        cases=tuple(cases),
        case_count=case_count,
        pass_count=pass_count,
        suite_score=suite_score,
        pass_at_k=pass_at_k,
        total_input_tokens=sum(case.total_input_tokens for case in cases),
        total_output_tokens=sum(case.total_output_tokens for case in cases),
        total_cached_tokens=sum(case.total_cached_tokens for case in cases),
        cost_estimate=float(sum(costs)) if costs else 0.0,
        missing_runs=tuple(missing),
    )
    logger.debug(
        f"{aggregate.case_count = }, {aggregate.pass_count = }, "
        f"{aggregate.suite_score = }, {aggregate.pass_at_k = }",
    )
    return aggregate


def _selected_ids(case_ids: Sequence[str] | None) -> frozenset[str] | None:
    """Normalise the optional case filter into a set, or ``None``."""
    if case_ids is None:
        return None
    if isinstance(case_ids, (str, bytes)):
        raise ScoringError("case_ids must be a sequence of case identifiers")

    selected = frozenset(str(case_id).strip() for case_id in case_ids)
    return selected or None


def _group_entries(
    entries: Sequence[results.IndexEntry],
    selected: frozenset[str] | None,
    k: int,
) -> dict[str, list[results.IndexEntry]]:
    """Group completed index entries per case, deduped by ``run_id``, first ``k``."""
    seen_runs: set[str] = set()
    groups: dict[str, list[results.IndexEntry]] = {}

    for entry in entries:
        status = entry.status.strip().lower()
        if status != metrics.STATUS_COMPLETED:
            logger.debug(f"skipping run '{entry.run_id}' with status {entry.status!r}")
            continue
        if selected is not None and entry.case_id not in selected:
            continue
        if entry.run_id in seen_runs:
            logger.debug(f"skipping duplicate index entry for run '{entry.run_id}'")
            continue

        bucket = groups.setdefault(entry.case_id, [])
        if len(bucket) >= k:
            continue
        bucket.append(entry)
        seen_runs.add(entry.run_id)

    return groups


def _load_summary(
    root: Path | None,
    run_id: str,
    case_id: str,
    missing: list[str],
) -> metrics.RunMetrics | None:
    """Read one run's metrics summary, recording an unreadable run as missing."""
    run_dir = workspace.runs_dir(root) / run_id
    try:
        summary = metrics.load_run(run_dir)
    except metrics.MetricsError as exc:
        logger.warning(
            "scoring: could not read the metrics of run '%s' for case '%s' (%s)",
            run_id,
            case_id,
            exc,
        )
        missing.append(run_id)
        return None

    logger.debug(f"{run_dir = }, {summary.run_id = }")
    return summary


def _numeric_scores(run: metrics.RunMetrics) -> list[float]:
    """Collect the numeric rubric scores recorded across the run's turns."""
    found: list[float] = []
    for turn in run.turns:
        if not isinstance(turn.score, Mapping):
            continue
        for value in turn.score.values():
            numeric = _as_score(value)
            if numeric is not None:
                found.append(numeric)

    logger.debug(f"{run.run_id = }, {len(found) = }")
    return found


def _as_score(value: Any) -> float | None:
    """Return ``value`` as a score: a number, or a mapping carrying ``score``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, Mapping):
        inner = value.get("score")
        if isinstance(inner, bool):
            return None
        if isinstance(inner, (int, float)):
            return float(inner)
    return None
