# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Minimal report rendering for the stored evaluation runs (ADR-0060).

The module turns the run index that :mod:`klea_eval.results` maintains into a
small, readable report: a header naming the workspace root, one line per stored
run in index order and a closing line of status counters.  It reads nothing but
``runs/index.jsonl``: every :class:`klea_eval.results.IndexEntry` already carries
the ``run_id``, ``case_id`` and ``status`` the report needs, so no run directory
has to be opened.

Nothing here creates directories, scores cases or prunes runs; retention belongs
to ADR-0059 and suite-level aggregation to :mod:`klea_eval.scoring`.  An empty or
missing index is not an error: it renders the header plus an all-zero summary.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

from klea_eval import results

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
        STATUS_UNGRADED,
    )
except ImportError:  # pragma: no cover - run_manager always defines the statuses
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    STATUS_UNGRADED = "ungraded"


def format_report(root: str | Path) -> str:
    """Render the report for the workspace ``root`` as one text blob.

    The run index is read in file order; a missing index yields just the header
    and an all-zero summary line instead of raising.
    """
    logger.debug(f"{root = }")
    entries = results.read_index(root)
    logger.debug(f"{len(entries) = }")

    lines = [f"Evaluation report for {root}"]
    for entry in entries:
        lines.append(f"{entry.run_id} case={entry.case_id} status={entry.status}")

    completed = 0
    failed = 0
    skipped = 0
    ungraded = 0
    for entry in entries:
        status = entry.status
        if status == STATUS_COMPLETED:
            completed += 1
        elif status == STATUS_FAILED:
            failed += 1
        elif status == STATUS_SKIPPED:
            skipped += 1
        elif status == STATUS_UNGRADED:
            ungraded += 1

    lines.append(
        f"completed={completed} failed={failed} skipped={skipped} "
        f"ungraded={ungraded} total={len(entries)}"
    )

    text = "\n".join(lines) + "\n"
    logger.debug(f"{completed = }, {failed = }, {skipped = }, {ungraded = }")
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
