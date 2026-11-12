# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Read and inspect API over stored evaluation runs, plus retention (P8).

The module is the read/inspect surface of the ADR-0060 Aggregate stage and the
retention point of ADR-0059.  It owns no persistence of its own: every read and
every removal is delegated to the module that already owns that contract:

- :func:`klea_eval.results.read_index` lists and locates runs through
  ``runs/index.jsonl`` (ADR-0059);
- :func:`klea_eval.results.load_stored_run` reads a run's ``run.json`` envelope
  and its ADR-0058 metrics summary;
- :func:`klea_eval.workspace.run_dir` and
  :func:`klea_eval.workspace.is_complete` locate a published run directory and
  check its zero-byte ``COMPLETE`` marker;
- :func:`klea_eval.scoring.aggregate_suite` computes the suite-level aggregate,
  so this module never re-derives a score.

Retention (ADR-0059, D11) keeps the most recent ``DEFAULT_RETENTION`` completed
runs per case, purges the whole directory of an older completed run as one unit
and rewrites ``runs/index.jsonl`` atomically through a fsynced sibling so the
surviving lines keep their file order.  An incomplete run, a non-completed index
line and a leftover ``.tmp`` directory is never counted as a result and never
deleted.

Deliberately deferred, all belonging to later phases of the implementation plan:

- the automatic post-publish retention hook in :mod:`klea_eval.pipeline`;
- partial retention that keeps only ``run.json`` plus ``metrics.jsonl`` for a run
  beyond the window: a purged run loses its whole directory instead;
- a startup index/directory consistency check or a rebuild-index command;
- ``transcript.jsonl`` writing, HITL ``interrupt`` resume, cost derivation, the
  bounded worker pool, MLflow export and the Markdown reporter.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klea_eval import metrics, results, scoring, workspace
from klea_eval.metrics import RunMetrics

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_RETENTION",
    "ApiError",
    "RetentionResult",
    "RunNotFoundError",
    "RunRecord",
    "apply_retention",
    "get_run",
    "list_runs",
    "suite_summary",
]

#: Number of completed runs kept per case by retention (D11, ADR-0059).
DEFAULT_RETENTION = 20


class ApiError(Exception):
    """Base error for the run read/inspect API and retention."""


class RunNotFoundError(ApiError):
    """Raised when a run is not listed, or is listed but not complete on disk."""


@dataclass(frozen=True, slots=True)
class RunRecord:
    """One stored run: its index line, its envelope and its metrics summary."""

    run_id: str
    case_id: str
    status: str
    timestamp: str
    metrics_path: str
    schema_version: str
    metadata: results.RunMetadata | None = None
    metrics: RunMetrics | None = None

    @property
    def passed(self) -> bool:
        """Whether the run's metrics report task success."""
        return self.metrics is not None and self.metrics.task_success is True

    def to_dict(self) -> dict[str, Any]:
        """Return the run record as a plain, JSON-ready mapping."""
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "timestamp": self.timestamp,
            "metrics_path": self.metrics_path,
            "schema_version": self.schema_version,
            "passed": self.passed,
            "metadata": _json_form(self.metadata),
            "metrics": _json_form(self.metrics),
        }


@dataclass(frozen=True, slots=True)
class RetentionResult:
    """What a retention pass kept and pruned (ADR-0059, D11)."""

    keep: int = DEFAULT_RETENTION
    kept_runs: tuple[str, ...] = ()
    pruned_runs: tuple[str, ...] = ()
    kept_entries: int = 0
    removed_entries: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Return the retention result as a plain, JSON-ready mapping."""
        return {
            "keep": self.keep,
            "kept_runs": list(self.kept_runs),
            "pruned_runs": list(self.pruned_runs),
            "kept_entries": self.kept_entries,
            "removed_entries": self.removed_entries,
        }


def get_run(run_id: str, root: str | Path | None = None) -> RunRecord:
    """Return the stored run named ``run_id``.

    The identifier is looked up through ``runs/index.jsonl`` first, so a run that
    is not listed is never read from disk.  A listed run whose directory lacks
    the ``COMPLETE`` marker is an interrupted run, not a result, and raises
    :class:`RunNotFoundError`.  The envelope and the metrics summary are read by
    :func:`klea_eval.results.load_stored_run`, which enforces the ADR-0059 schema
    version rather than reinterpreting an unknown one.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ApiError("run_id must be a non-empty string")

    identifier = run_id.strip()
    root_path = Path(root) if root is not None else None
    entry = _find_entry(identifier, root_path)
    directory = workspace.run_dir(identifier, root_path)
    logger.debug(f"{identifier = }, {entry.case_id = }, {directory = }")

    if not workspace.is_complete(directory):
        raise RunNotFoundError(
            f"run {identifier!r} is listed but not complete at {directory}"
        )

    try:
        stored = results.load_stored_run(directory)
    except results.ResultsSchemaError:
        raise
    except results.ResultsError as exc:
        raise ApiError(f"could not read run {identifier!r}: {exc}") from exc

    record = RunRecord(
        run_id=entry.run_id,
        case_id=entry.case_id,
        status=entry.status,
        timestamp=entry.timestamp,
        metrics_path=entry.metrics_path,
        schema_version=entry.schema_version,
        metadata=stored.metadata,
        metrics=stored.metrics,
    )
    logger.debug(f"{record.run_id = }, {record.passed = }")
    return record


def list_runs(
    root: str | Path | None = None,
    *,
    case_id: str | None = None,
    status: str | None = None,
) -> tuple[results.IndexEntry, ...]:
    """List the index entries, in file order, optionally filtered.

    Listing reads ``runs/index.jsonl`` only and never opens a run directory, so
    it answers a cheap suite query without touching per-run detail (ADR-0059).
    ``case_id`` matches exactly; ``status`` matches case-insensitively.
    """
    wanted_case = _optional_filter(case_id, "case_id")
    wanted_status = _optional_filter(status, "status", lower=True)

    entries = [
        entry
        for entry in results.read_index(root)
        if _matches(entry, wanted_case, wanted_status)
    ]
    logger.debug(f"{root = }, {wanted_case = }, {wanted_status = }, {len(entries) = }")
    return tuple(entries)


def suite_summary(
    root: str | Path | None = None,
    *,
    k: int = scoring.DEFAULT_K,
    case_ids: Sequence[str] | None = None,
) -> scoring.SuiteAggregate:
    """Return the suite-level aggregate of the stored runs (ADR-0060).

    This is a thin delegation to :func:`klea_eval.scoring.aggregate_suite`, so
    ``pass_at_k`` and ``suite_score`` keep exactly the semantics the scoring
    module owns; this module only exposes them to the API and the CLI.
    """
    logger.debug(f"{root = }, {k = }, {case_ids = }")
    return scoring.aggregate_suite(root, k=k, case_ids=case_ids)


def apply_retention(
    root: str | Path | None = None,
    *,
    keep: int = DEFAULT_RETENTION,
) -> RetentionResult:
    """Keep the most recent ``keep`` completed runs per case, prune the rest.

    Only completed index entries are considered; they are grouped by ``case_id``
    in first-index-occurrence order and the most recent ``keep`` of each case are
    kept (ADR-0059).  Every pruned run loses its whole directory in one removal
    and its index line is dropped by an atomic rewrite of
    ``runs/index.jsonl``, so a run directory is never edited in place.  An absent
    or empty index is a no-op.
    """
    if isinstance(keep, bool) or not isinstance(keep, int):
        raise ApiError(f"keep must be an integer, got {type(keep).__name__}")
    if keep < 1:
        raise ApiError(f"keep must be at least 1, got {keep}")

    entries = results.read_index(root)
    if not entries:
        logger.debug(f"{root = }, {keep = }, no index entries")
        return RetentionResult(keep=keep)

    dropped = _prune_positions(entries, keep)
    if not dropped:
        logger.debug(f"{root = }, {keep = }, nothing to prune")
        return RetentionResult(
            keep=keep,
            kept_runs=tuple(entry.run_id for entry in entries),
            kept_entries=len(entries),
        )

    root_path = Path(root) if root is not None else None
    pruned_runs: list[str] = []
    for position in sorted(dropped):
        entry = entries[position]
        directory = workspace.run_dir(entry.run_id, root_path)
        try:
            if directory.is_dir():
                shutil.rmtree(directory)
                pruned_runs.append(entry.run_id)
        except OSError as exc:
            logger.warning(
                "api: could not purge the run directory %s (%s)", directory, exc
            )
            dropped = dropped - {position}

    survivors = [
        entry for position, entry in enumerate(entries) if position not in dropped
    ]
    _rewrite_index(results.index_path(root), survivors)

    result = RetentionResult(
        keep=keep,
        kept_runs=tuple(entry.run_id for entry in survivors),
        pruned_runs=tuple(pruned_runs),
        kept_entries=len(survivors),
        removed_entries=len(dropped),
    )
    logger.debug(
        f"{result.kept_entries = }, {result.removed_entries = }, "
        f"{len(result.pruned_runs) = }"
    )
    return result


def _find_entry(run_id: str, root: str | Path | None) -> results.IndexEntry:
    """Return the index entry of ``run_id``, or raise :class:`RunNotFoundError`."""
    for entry in results.read_index(root):
        if entry.run_id == run_id:
            logger.debug(f"{run_id = }, {entry.case_id = }")
            return entry

    raise RunNotFoundError(
        f"run {run_id!r} is not listed in {results.index_path(root)}"
    )


def _matches(
    entry: results.IndexEntry,
    case_id: str | None,
    status: str | None,
) -> bool:
    """Whether ``entry`` satisfies the optional case and status filters."""
    if case_id is not None and entry.case_id != case_id:
        return False
    return status is None or entry.status.strip().lower() == status


def _optional_filter(value: Any, label: str, *, lower: bool = False) -> str | None:
    """Normalise an optional string filter, rejecting a non-string value."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ApiError(f"{label} must be a string or None, got {type(value).__name__}")

    text = value.strip()
    if not text:
        return None
    return text.lower() if lower else text


def _is_completed(entry: results.IndexEntry) -> bool:
    """Whether the index line names a completed run."""
    return entry.status.strip().lower() == metrics.STATUS_COMPLETED


def _prune_positions(
    entries: Sequence[results.IndexEntry], keep: int
) -> frozenset[int]:
    """Return the index positions of the completed runs retention would prune."""
    per_case: dict[str, list[int]] = {}
    for position, entry in enumerate(entries):
        if _is_completed(entry):
            per_case.setdefault(entry.case_id, []).append(position)

    dropped: set[int] = set()
    for positions in per_case.values():
        dropped.update(positions[:-keep])

    logger.debug(f"{len(entries) = }, {len(per_case) = }, {len(dropped) = }")
    return frozenset(dropped)


def _rewrite_index(target: Path, entries: Sequence[results.IndexEntry]) -> None:
    """Rewrite ``runs/index.jsonl`` atomically from ``entries``, in order.

    The surviving lines are written to a fsynced sibling temporary file that is
    moved into place with :func:`os.replace`, so a reader sees the old index or
    the new one and never a truncated file.  The index is never edited in place.
    """
    lines = [
        json.dumps(entry.to_dict(), sort_keys=True, ensure_ascii=True)
        for entry in entries
    ]
    payload = "".join(f"{line}\n" for line in lines).encode()
    temporary = target.with_name(f"{target.name}.tmp")

    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(descriptor, payload)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
        # Workspace owns the directory-fsync semantics; reuse them here so the
        # rename is as durable as it is for a published run.
        workspace._fsync_dir(target.parent)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.debug(f"{temporary = }")
        raise ApiError(f"could not rewrite '{target}': {exc}") from exc

    logger.debug(f"{target = }, {len(entries) = }, {len(payload) = }")


def _json_form(value: Any) -> Any:
    """Return the JSON-ready form of a record that exposes ``to_dict``."""
    if value is None:
        return None

    renderer = getattr(value, "to_dict", None)
    if callable(renderer):
        return renderer()
    return value
