"""Run workspace layout, creation and publication for the eval harness.

The module owns the filesystem surface of a run workspace as fixed by ADR-0057,
ADR-0058 and ADR-0059:

- published runs live at ``<root>/runs/<run_id>/``;
- a run under construction lives at ``<root>/runs/<run_id>.tmp/``;
- ``metrics.jsonl`` sits at the run root, never under ``workspace/``;
- ``COMPLETE`` is written last, and the final directory only ever appears by an
  atomic rename, so a reader sees no run or a complete run.

Run identity derivation belongs to :mod:`klea_eval.identity`, and record writing
belongs to the ADR-0059 writer; this module only derives paths, creates the run
skeleton and publishes or discards it.

# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import enum
import logging
import os
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

WORKSPACE_ENV_VAR = "KLEA_EVAL_WORKSPACE"
DEFAULT_WORKSPACE_DIR = Path(".local") / "share" / "klea-eval"
RUNS_DIR_NAME = "runs"
WORKSPACE_DIR_NAME = "workspace"
FIXTURES_DIR_NAME = "fixtures"
OUTPUTS_DIR_NAME = "outputs"
METRICS_FILE_NAME = "metrics.jsonl"
COMPLETE_MARKER_NAME = "COMPLETE"
TMP_SUFFIX = ".tmp"


class WorkspaceError(Exception):
    """Base error for run workspace operations."""


class RunAlreadyCompleteError(WorkspaceError):
    """Raised when an operation would clobber an already published run."""


class RunState(enum.Enum):
    """On-disk state of a run, derived from the run directories alone."""

    NEW = "new"
    INCOMPLETE = "incomplete"
    COMPLETE = "complete"


def workspace_root(root: Path | None = None) -> Path:
    """Return the evaluation workspace root, without creating it.

    An explicit ``root`` wins, then ``$KLEA_EVAL_WORKSPACE``, then the default
    outside the source tree at ``~/.local/share/klea-eval`` (D3).
    """
    if root is not None:
        logger.debug(f"{root = }")
        return Path(root)

    env_value = os.environ.get(WORKSPACE_ENV_VAR, "").strip()
    if env_value:
        logger.debug(f"{env_value = }")
        return Path(env_value).expanduser()

    default_root = Path.home() / DEFAULT_WORKSPACE_DIR
    logger.debug(f"{default_root = }")
    return default_root


def runs_dir(root: Path | None = None) -> Path:
    """Return ``<workspace_root>/runs``, the parent of every run directory."""
    path = workspace_root(root) / RUNS_DIR_NAME
    logger.debug(f"{path = }")
    return path


def run_dir(run_id: str, root: Path | None = None) -> Path:
    """Return the published run directory ``runs/<run_id>/``."""
    path = runs_dir(root) / run_id
    logger.debug(f"{path = }")
    return path


def run_dir_tmp(run_id: str, root: Path | None = None) -> Path:
    """Return the run-under-construction directory ``runs/<run_id>.tmp/``."""
    path = runs_dir(root) / f"{run_id}{TMP_SUFFIX}"
    logger.debug(f"{path = }")
    return path


def metrics_path(run_dir: Path) -> Path:
    """Return ``metrics.jsonl`` at the run root, never under ``workspace/``."""
    path = Path(run_dir) / METRICS_FILE_NAME
    logger.debug(f"{path = }")
    return path


def is_complete(run_dir: Path) -> bool:
    """Return whether ``run_dir`` carries the zero-byte ``COMPLETE`` marker."""
    complete = (Path(run_dir) / COMPLETE_MARKER_NAME).is_file()
    logger.debug(f"{run_dir = }, {complete = }")
    return complete


def detect_state(run_id: str, root: Path | None = None) -> RunState:
    """Classify a run as ``NEW``, ``INCOMPLETE`` or ``COMPLETE``.

    A leftover ``.tmp`` directory, or a final directory without ``COMPLETE``,
    is unambiguously an incomplete run and is never treated as a result.
    """
    final_dir = run_dir(run_id, root)
    if is_complete(final_dir):
        state = RunState.COMPLETE
    elif final_dir.exists() or run_dir_tmp(run_id, root).exists():
        state = RunState.INCOMPLETE
    else:
        state = RunState.NEW

    logger.debug(f"{run_id = }, {state = }")
    return state


def prepare_run(run_id: str, root: Path | None = None, overwrite: bool = False) -> Path:
    """Create a fresh ``runs/<run_id>.tmp/`` skeleton and return it.

    A completed run is rejected with :class:`RunAlreadyCompleteError` unless
    ``overwrite`` is set; a leftover ``.tmp`` (or an unpublished final) directory
    is logged as an incomplete run and discarded before the skeleton is rebuilt.
    ``metrics.jsonl`` and ``run.json`` are not created here; the ADR-0059 writer
    owns them.
    """
    final_dir = run_dir(run_id, root)
    if final_dir.exists():
        if is_complete(final_dir) and not overwrite:
            logger.debug(f"{run_id = }, {final_dir = }")
            raise RunAlreadyCompleteError(
                f"run {run_id!r} is already complete at {final_dir}"
            )
        logger.warning(f"discarding existing run directory {final_dir} for {run_id = }")
        shutil.rmtree(final_dir)

    tmp_dir = run_dir_tmp(run_id, root)
    if tmp_dir.exists():
        logger.warning(f"discarding incomplete run {tmp_dir} for {run_id = }")
        discard_run(run_id, root)

    fixtures_dir = tmp_dir / WORKSPACE_DIR_NAME / FIXTURES_DIR_NAME
    outputs_dir = tmp_dir / WORKSPACE_DIR_NAME / OUTPUTS_DIR_NAME
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    logger.debug(f"{tmp_dir = }, {fixtures_dir = }, {outputs_dir = }")
    return tmp_dir


def publish_run(run_id: str, root: Path | None = None) -> Path:
    """Publish ``runs/<run_id>.tmp/`` as ``runs/<run_id>/`` atomically.

    Every regular file is flushed and fsynced, the zero-byte ``COMPLETE`` marker
    is written last, and the directory is renamed in one atomic operation. An
    already published run is never clobbered.
    """
    tmp_dir = run_dir_tmp(run_id, root)
    final_dir = run_dir(run_id, root)

    if not tmp_dir.is_dir():
        logger.debug(f"{run_id = }, {tmp_dir = }")
        raise WorkspaceError(f"no run under construction at {tmp_dir}")

    if final_dir.exists():
        logger.debug(f"{run_id = }, {final_dir = }")
        raise RunAlreadyCompleteError(f"run {run_id!r} already exists at {final_dir}")

    for path in sorted(tmp_dir.rglob("*")):
        if path.is_file():
            _fsync_file(path)

    marker_path = tmp_dir / COMPLETE_MARKER_NAME
    marker_fd = os.open(marker_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
    try:
        os.fsync(marker_fd)
    finally:
        os.close(marker_fd)

    _fsync_dir(tmp_dir)
    os.replace(tmp_dir, final_dir)

    logger.debug(f"{run_id = }, {marker_path = }, {final_dir = }")
    return final_dir


def discard_run(run_id: str, root: Path | None = None) -> None:
    """Remove ``runs/<run_id>.tmp/`` as one unit, if present."""
    tmp_dir = run_dir_tmp(run_id, root)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    logger.debug(f"{run_id = }, {tmp_dir = }")


def _fsync_file(path: Path) -> None:
    """Flush one regular file to disk."""
    file_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry to disk, where the platform supports it."""
    try:
        dir_fd = os.open(path, os.O_RDONLY)
    except OSError as error:
        logger.debug(f"{path = }, {error = }")
        return

    try:
        os.fsync(dir_fd)
    except OSError as error:
        logger.debug(f"{path = }, {error = }")
    finally:
        os.close(dir_fd)
