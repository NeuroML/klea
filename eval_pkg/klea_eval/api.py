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

:func:`check_consistency` and :func:`detect_drift` are the explicit
check-and-repair pass over the same files (``klea-eval check``): a complete run
the index omits is re-indexed, a duplicate or orphan index line is dropped, and
an incomplete (``.tmp`` or unmarked) directory is only flagged, never deleted.

Deliberately deferred, all belonging to later phases of the implementation plan:

- the automatic post-publish retention hook in :mod:`klea_eval.pipeline`;
- partial retention that keeps only ``run.json`` plus ``metrics.jsonl`` for a run
  beyond the window: a purged run loses its whole directory instead;
- a startup index/directory consistency check or an automatic rebuild-index
  command: :func:`check_consistency` is the explicit, operator-invoked pass
  instead;
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
    "ConsistencyResult",
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


#: Suffix of the transient backup directory created while a run is replaced.
_SUPERSEDED_SUFFIX = ".superseded"


@dataclass(frozen=True, slots=True)
class ConsistencyResult:
    """The drift :func:`detect_drift` found, and what a repair applied."""

    repaired: bool = False
    missing_entries: tuple[str, ...] = ()
    orphan_entries: tuple[str, ...] = ()
    duplicate_entries: tuple[str, ...] = ()
    unmarked_directories: tuple[str, ...] = ()
    staging_directories: tuple[str, ...] = ()
    unrepairable_entries: tuple[str, ...] = ()

    @property
    def has_drift(self) -> bool:
        """Whether the index disagrees with the run directories."""
        return bool(
            self.missing_entries
            or self.orphan_entries
            or self.duplicate_entries
            or self.unmarked_directories
        )

    @property
    def unrepairable(self) -> bool:
        """Whether drift remains that a repair cannot fix."""
        return bool(self.unmarked_directories or self.unrepairable_entries)

    def to_dict(self) -> dict[str, Any]:
        """Return the result as a plain, JSON-ready mapping."""
        return {
            "repaired": self.repaired,
            "missing_entries": list(self.missing_entries),
            "orphan_entries": list(self.orphan_entries),
            "duplicate_entries": list(self.duplicate_entries),
            "unmarked_directories": list(self.unmarked_directories),
            "staging_directories": list(self.staging_directories),
            "unrepairable_entries": list(self.unrepairable_entries),
        }


def detect_drift(root: str | Path | None = None) -> ConsistencyResult:
    """Detect drift between ``runs/index.jsonl`` and the run directories.

    The pass is read-only: it never edits the index or a run directory.  It
    reuses :func:`klea_eval.results.read_index` for the index and
    :func:`klea_eval.workspace.is_complete` for a directory, so no second index
    format is introduced.  The reported classes are:

    - a complete run directory that the index does not name;
    - an index line whose run directory is absent (an orphan);
    - a ``run_id`` named by more than one index line (a duplicate);
    - a final run directory without the ``COMPLETE`` marker (unmarked).

    A leftover ``<run_id>.tmp`` staging directory is counted separately and is
    never drift: an interrupted run is never a result and never carries an index
    line.
    """
    root_path = Path(root) if root is not None else None
    entries = results.read_index(root_path)

    complete_dirs: list[str] = []
    unmarked: list[str] = []
    staging: list[str] = []
    runs_directory = workspace.runs_dir(root_path)
    if runs_directory.is_dir():
        for item in sorted(runs_directory.iterdir(), key=lambda path: path.name):
            if not item.is_dir():
                continue
            name = item.name
            if name.endswith(workspace.TMP_SUFFIX):
                staging.append(name[: -len(workspace.TMP_SUFFIX)])
                logger.debug(f"api: ignoring incomplete staging directory {item}")
                continue
            if name.endswith(_SUPERSEDED_SUFFIX):
                logger.debug(f"api: ignoring superseded backup {item}")
                continue
            if workspace.is_complete(item):
                complete_dirs.append(name)
            else:
                unmarked.append(name)
                logger.warning("api: run directory %s has no COMPLETE marker", item)

    indexed: dict[str, int] = {}
    duplicates: list[str] = []
    for entry in entries:
        if entry.run_id in indexed:
            indexed[entry.run_id] += 1
            if entry.run_id not in duplicates:
                duplicates.append(entry.run_id)
            continue
        indexed[entry.run_id] = 1

    complete_ids = set(complete_dirs)
    unmarked_ids = set(unmarked)
    missing: list[str] = []
    for run_id in complete_dirs:
        if run_id not in indexed:
            missing.append(run_id)
            logger.warning("api: complete run %s is missing from the index", run_id)

    orphans: list[str] = []
    for run_id in indexed:
        if run_id in complete_ids or run_id in unmarked_ids:
            continue
        orphans.append(run_id)
        logger.warning("api: index line for run %s has no run directory", run_id)

    for run_id in duplicates:
        logger.warning("api: run %s has duplicate index lines", run_id)

    result = ConsistencyResult(
        missing_entries=tuple(missing),
        orphan_entries=tuple(orphans),
        duplicate_entries=tuple(duplicates),
        unmarked_directories=tuple(unmarked),
        staging_directories=tuple(staging),
    )
    logger.debug(f"{len(entries) = }, {result.has_drift = }, {len(staging) = }")
    return result


def check_consistency(
    root: str | Path | None = None,
    *,
    repair: bool = False,
) -> ConsistencyResult:
    """Check the run index, optionally repairing the drift it finds.

    Without ``repair`` this is exactly :func:`detect_drift`, so a caller can
    inspect the drift first.  With ``repair`` each class is fixed in order:

    1. a complete, unindexed run is re-indexed through
       :func:`klea_eval.results.append_index_entry`; a run whose ``run.json``
       cannot be read is reported as unrepairable and left untouched;
    2. duplicate and orphan index lines are dropped by one atomic rewrite of
       ``runs/index.jsonl``, keeping the first line of a duplicated ``run_id``.

    An unmarked final directory and a leftover ``.tmp`` directory are only
    flagged: neither is ever deleted and neither gets an index line.  The repair
    is idempotent, so a second pass finds no repairable drift.
    """
    root_path = Path(root) if root is not None else None
    detected = detect_drift(root_path)
    if not repair or not detected.has_drift:
        logger.debug(f"{root_path = }, {repair = }, nothing to repair")
        return detected

    unrepairable: list[str] = []
    for run_id in detected.missing_entries:
        directory = workspace.run_dir(run_id, root_path)
        try:
            metadata = results.read_metadata(directory)
            results.append_index_entry(results.to_index_entry(metadata), root_path)
        except (results.ResultsError, OSError) as exc:
            logger.warning("api: could not re-index run %s: %s", run_id, exc)
            unrepairable.append(run_id)
            continue
        logger.info("api: re-indexed run %s", run_id)

    if detected.orphan_entries or detected.duplicate_entries:
        survivors = _surviving_entries(
            results.read_index(root_path),
            set(detected.orphan_entries),
            set(detected.duplicate_entries),
        )
        _rewrite_index(results.index_path(root_path), survivors)
        for run_id in detected.orphan_entries:
            logger.warning("api: dropped orphan index line for run %s", run_id)
        for run_id in detected.duplicate_entries:
            logger.warning("api: dropped duplicate index lines for run %s", run_id)

    return ConsistencyResult(
        repaired=True,
        missing_entries=detected.missing_entries,
        orphan_entries=detected.orphan_entries,
        duplicate_entries=detected.duplicate_entries,
        unmarked_directories=detected.unmarked_directories,
        staging_directories=detected.staging_directories,
        unrepairable_entries=tuple(unrepairable),
    )


def _surviving_entries(
    entries: Sequence[results.IndexEntry],
    orphans: set[str],
    duplicates: set[str],
) -> list[results.IndexEntry]:
    """Return the index lines left after dropping orphans and duplicates."""
    seen: set[str] = set()
    survivors: list[results.IndexEntry] = []
    for entry in entries:
        if entry.run_id in orphans:
            continue
        if entry.run_id in duplicates and entry.run_id in seen:
            continue
        seen.add(entry.run_id)
        survivors.append(entry)

    logger.debug(f"{len(entries) = }, {len(survivors) = }")
    return survivors


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
