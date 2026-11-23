# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Minimal report rendering for the stored evaluation runs (ADR-0060).

Renders from stored records only, the same records the inspection commands
read, so a report can be regenerated without re-executing a case and never
depends on live process state.


The module turns the run index that :mod:`klea_eval.results` maintains into a
small, readable report: a header naming the workspace root, one line per stored
run in index order and a closing line of status counters.  Each entry's run
directory is resolved through :mod:`klea_eval.workspace`: ``run.json`` supplies
``expects_grade`` and ``metrics.jsonl`` supplies the stored ``task_success``, so
the counters describe what the writer actually stored.

Nothing here creates directories, scores cases or prunes runs; retention belongs
to ADR-0059 and suite-level aggregation to :mod:`klea_eval.scoring`.  An empty or
missing index is not an error: it renders the header plus an all-zero summary.
Published run directories under ``runs/`` that the index omits are rendered as
extra lines marked ``index=missing`` -- ``drift`` when the run is complete,
``archived`` when it is retained, and an unmarked residue otherwise -- so the
``unindexed`` counter in the summary reflects them.
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path
from typing import Any

from klea_eval import metrics, results, workspace

logger = logging.getLogger(__name__)

__all__ = [
    "format_report",
    "write_report",
]

# The status vocabulary lives in the run manager; fall back to the documented
# strings so this module stays importable on its own.
try:
    from klea_eval.run_manager import (
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_SKIPPED,
    )
except ImportError:  # pragma: no cover - run_manager always defines the statuses
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"


_UNREADABLE: Any = object()


def _read_json(path: Path) -> Any | None:
    """Read ``path`` as JSON, returning ``None`` when it cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _stored_task_success(run_dir: Path) -> Any:
    """Return the stored ``task_success`` for ``run_dir`` or ``_UNREADABLE``.

    The value is read from the run's ``metrics.jsonl`` at the run root: the run
    envelope (``record_type == metrics.RECORD_TYPE_RUN``) wins, and a turn
    record is only consulted when no envelope carries the field.  A missing or
    unreadable file, or a file with no signal at all, yields ``_UNREADABLE`` so
    the caller never sees an exception.
    """
    try:
        text = workspace.metrics_path(run_dir).read_text(encoding="utf-8")
    except (OSError, TypeError, ValueError):
        return _UNREADABLE

    envelope: Any = _UNREADABLE
    turn: Any = _UNREADABLE
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except ValueError:
            continue
        if not isinstance(payload, dict):
            continue

        if payload.get("record_type") == metrics.RECORD_TYPE_RUN:
            nested = payload.get("metrics")
            if isinstance(nested, dict) and "task_success" in nested:
                envelope = nested["task_success"]
            else:
                envelope = payload.get("task_success")
            continue

        if "task_success" in payload and turn is _UNREADABLE:
            turn = payload["task_success"]

    if envelope is not _UNREADABLE:
        return envelope
    return turn


def _entry_signals(
    root: str | Path, entry: results.IndexEntry
) -> tuple[Any, bool, bool]:
    """Return ``(task_success, expects_grade, readable)`` for an index entry."""
    try:
        run_dir = workspace.run_dir(entry.run_id, Path(root))
        metadata = _read_json(results.run_metadata_path(run_dir))
    except (OSError, TypeError, ValueError):
        return None, False, False

    if not isinstance(metadata, dict):
        return None, False, False

    expects_grade = bool(metadata.get("expects_grade", False))
    task_success = _stored_task_success(run_dir)
    if task_success is _UNREADABLE:
        return None, expects_grade, False
    return task_success, expects_grade, True


def _unindexed_line(root: str | Path, run_id: str) -> str:
    """Render one line for a run directory that ``runs/index.jsonl`` omits.

    A directory carrying both ``COMPLETE`` and ``ARCHIVED`` is a retained run
    and is labelled ``archived``; a complete directory without an index line is
    drift, not a failure, and is labelled ``drift``; anything else is an
    unmarked residue whose label comes from its readable ``run.json`` (and, as
    before, its stored ``task_success``), or falls back to ``case=?``,
    ``status=incomplete`` and ``label=unknown`` without inventing a status.
    """
    from klea_eval import labels

    run_path = workspace.run_dir(run_id, Path(root))
    metadata = _read_json(results.run_metadata_path(run_path))
    stored = metadata if isinstance(metadata, dict) else {}

    case_id = stored.get("case_id") or "?"
    status = stored.get("status")
    expects_grade = bool(stored.get("expects_grade", False))

    if workspace.is_archived(run_path):
        label = "archived"
    elif workspace.is_complete(run_path):
        label = "drift"
    else:
        task_success = _stored_task_success(run_path)
        if task_success is _UNREADABLE:
            task_success = None
        if not stored:
            status = "incomplete"
            label = "unknown"
        else:
            status = status or "incomplete"
            label = labels.classify_case(
                status=status,
                task_success=task_success,
                expects_grade=expects_grade,
            )

    logger.debug(f"{run_id = }, {label = }")
    return f"{run_id} case={case_id} status={status or '?'} label={label} index=missing"


def format_report(root: str | Path) -> str:
    """Render the report for the workspace ``root`` as one text blob.

    The run index is read in file order; a missing index yields just the header
    and an all-zero summary line instead of raising.
    """
    logger.debug(f"{root = }")
    entries = results.read_index(root)
    logger.debug(f"{len(entries) = }")

    from klea_eval import labels

    indexed = {entry.run_id for entry in entries}
    lines = [f"Evaluation report for {root}"]
    counters = {
        "completed": 0,
        "failed": 0,
        "skipped": 0,
        "ungraded": 0,
        "grade-missing": 0,
        "unindexed": 0,
    }

    for entry in entries:
        task_success, expects_grade, readable = _entry_signals(root, entry)
        label = labels.classify_case(
            status=entry.status,
            task_success=task_success,
            expects_grade=expects_grade,
        )
        lines.append(
            f"{entry.run_id} case={entry.case_id} status={entry.status} label={label}"
        )

        status = entry.status.strip().lower()
        if status == STATUS_SKIPPED:
            counters["skipped"] += 1
        elif status == STATUS_FAILED:
            counters["failed"] += 1
        elif status != STATUS_COMPLETED or not readable:
            if not readable:
                logger.debug(f"unreadable run {entry.run_id} counted as failed")
            counters["failed"] += 1
        elif task_success is None:
            counters["grade-missing" if expects_grade else "ungraded"] += 1
        else:
            counters["completed"] += 1

    for run_id in workspace.list_run_dirs(Path(root)):
        if run_id in indexed:
            continue
        counters["unindexed"] += 1
        lines.append(_unindexed_line(root, run_id))

    lines.append(
        f"completed={counters['completed']} failed={counters['failed']} "
        f"skipped={counters['skipped']} ungraded={counters['ungraded']} "
        f"grade-missing={counters['grade-missing']} total={len(entries)} "
        f"unindexed={counters['unindexed']}"
    )

    text = "\n".join(lines) + "\n"
    logger.debug(f"{counters = }, {len(entries) = }")
    return text


def write_report(results_path: str | None, output: str | None = None) -> None:
    """Render the report for the workspace ``results_path`` to ``output``.

    ``results_path`` is the workspace root the ``run`` command writes into, i.e.
    the same value its ``--workspace`` option takes.  ``output`` is standard
    output when it is empty or ``'-'``; anything else is a file path whose parent
    directories are created as needed.
    """
    logger.debug(f"{results_path = }, {output = }")
    text = format_report(results_path or ".")

    if not output or output == "-":
        sys.stdout.write(text)
        logger.debug(f"{len(text) = }")
        return

    target = Path(output)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    except OSError:
        logger.debug(f"{target = }")
        raise

    logger.debug(f"{target = }, {len(text) = }")
