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
    ``overwrite`` is set.  With ``overwrite`` the existing published run is left
    untouched here and is replaced only once the new attempt reaches
    :func:`publish_run`, so a failure mid-run never loses the previous results
    or its index line.  A leftover ``.tmp`` directory is logged as an incomplete
    run and discarded before the skeleton is rebuilt.  ``metrics.jsonl`` and
    ``run.json`` are not created here; the ADR-0059 writer owns them.
    """
    if _overwrite_marker(run_id, root).is_file():
        overwrite = True
    final_dir = run_dir(run_id, root)
    if final_dir.exists():
        if is_complete(final_dir) and not overwrite:
            logger.debug(f"{run_id = }, {final_dir = }")
            raise RunAlreadyCompleteError(
                f"run {run_id!r} is already complete at {final_dir}"
            )
        logger.warning(
            f"keeping existing run directory {final_dir} for {run_id = } "
            "until its replacement is published"
        )

    tmp_dir = run_dir_tmp(run_id, root)
    if tmp_dir.exists():
        logger.warning(f"discarding incomplete run {tmp_dir} for {run_id = }")
        discard_run(run_id, root)

    if final_dir.exists() and overwrite:
        _overwrite_marker(run_id, root).write_text("", encoding="utf-8")

    fixtures_dir = tmp_dir / WORKSPACE_DIR_NAME / FIXTURES_DIR_NAME
    outputs_dir = tmp_dir / WORKSPACE_DIR_NAME / OUTPUTS_DIR_NAME
    fixtures_dir.mkdir(parents=True, exist_ok=True)
    outputs_dir.mkdir(parents=True, exist_ok=True)

    logger.debug(f"{tmp_dir = }, {fixtures_dir = }, {outputs_dir = }")
    return tmp_dir


def run_dir_superseded(run_id: str, root: Path | None = None) -> Path:
    """Return ``runs/<run_id>.superseded/``, the backup of a replaced run.

    The directory only exists while an overwrite is being published: the
    previous run is moved here after the new one is complete and removed once
    the new one is in place, or moved back if the final rename fails.
    """
    final_dir = run_dir(run_id, root)
    path = final_dir.with_name(f"{final_dir.name}.superseded")
    logger.debug(f"{run_id = }, {path = }")
    return path


def _overwrite_marker(run_id: str, root: Path | None = None) -> Path:
    """Return ``runs/<run_id>.overwrite``, the pending-overwrite sentinel.

    :func:`prepare_run` writes it when it is told to overwrite, so that
    :func:`publish_run` knows the staged attempt may replace the published run
    without any caller having to thread a flag through.  It is removed once the
    new run is in place (or when the attempt is discarded), so a plain publish
    still refuses to clobber an existing run.
    """
    final_dir = run_dir(run_id, root)
    path = final_dir.with_name(f"{final_dir.name}.overwrite")
    logger.debug(f"{run_id = }, {path = }")
    return path


def publish_run(run_id: str, root: Path | None = None, overwrite: bool = False) -> Path:
    """Publish ``runs/<run_id>.tmp/`` as ``runs/<run_id>/`` atomically.

    Every regular file is flushed and fsynced, the zero-byte ``COMPLETE`` marker
    is written last, and the directory is renamed in one atomic operation. An
    already published run is never clobbered: it raises
    :class:`RunAlreadyCompleteError` unless ``overwrite`` is set.  With
    ``overwrite`` the previous run is moved aside only once the new run is
    complete, so the old run stays readable (and indexed) if anything before the
    final rename fails; it is restored if that rename fails, and removed after
    it succeeds.
    """
    tmp_dir = run_dir_tmp(run_id, root)
    final_dir = run_dir(run_id, root)

    if not tmp_dir.is_dir():
        logger.debug(f"{run_id = }, {tmp_dir = }")
        raise WorkspaceError(f"no run under construction at {tmp_dir}")

    overwrite_marker = _overwrite_marker(run_id, root)
    overwrite = overwrite or overwrite_marker.is_file()

    if final_dir.exists() and not overwrite:
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

    superseded_dir = run_dir_superseded(run_id, root)
    moved_aside = False
    if final_dir.exists():
        if superseded_dir.exists():
            logger.warning(f"discarding stale backup {superseded_dir} for {run_id = }")
            shutil.rmtree(superseded_dir, ignore_errors=True)
        logger.debug(f"{run_id = }, {final_dir = }, {superseded_dir = }")
        os.replace(final_dir, superseded_dir)
        moved_aside = True

    try:
        os.replace(tmp_dir, final_dir)
    except OSError:
        if moved_aside:
            logger.warning(f"restoring previous run {superseded_dir} for {run_id = }")
            os.replace(superseded_dir, final_dir)
        raise

    if moved_aside:
        shutil.rmtree(superseded_dir, ignore_errors=True)

    overwrite_marker.unlink(missing_ok=True)

    logger.debug(f"{run_id = }, {marker_path = }, {final_dir = }")
    return final_dir


def discard_run(run_id: str, root: Path | None = None) -> None:
    """Remove ``runs/<run_id>.tmp/`` as one unit, if present.

    A pending-overwrite sentinel is dropped with the staging directory so an
    abandoned attempt can never replace an existing run later.
    """
    tmp_dir = run_dir_tmp(run_id, root)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    _overwrite_marker(run_id, root).unlink(missing_ok=True)
    logger.debug(f"{run_id = }, {tmp_dir = }")


def _fsync_file(path: Path) -> None:
    """Flush one regular file to disk.

    Windows' ``os.fsync`` is the CRT ``_commit``, which rejects a read-only
    descriptor, so the file is reopened for writing there and a read-only file
    that cannot be reopened is skipped best-effort.  POSIX accepts a read-only
    descriptor, so the strict open/fsync/close path is kept.
    """
    if os.name == "nt":
        try:
            file_fd = os.open(path, os.O_RDWR)
        except OSError as error:
            logger.debug(f"{path = }, {error = }")
            return

        try:
            os.fsync(file_fd)
        except OSError as error:
            logger.debug(f"{path = }, {error = }")
        finally:
            os.close(file_fd)
        return

    file_fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(file_fd)
    finally:
        os.close(file_fd)


def _fsync_dir(path: Path) -> None:
    """Flush a directory entry to disk, where the platform supports it."""
    if os.name == "nt":
        # The Windows CRT cannot open a directory, so there is never a
        # descriptor to flush; the rename is durable without it.
        return

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
