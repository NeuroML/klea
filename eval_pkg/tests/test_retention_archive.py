# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the archived retention tier (ADR-0059, D11).

The default retention pass archives a run beyond the window: only its
``workspace/`` tree is removed, while ``run.json``, ``metrics.jsonl``, the
``COMPLETE`` marker and its ``runs/index.jsonl`` line survive, and an
``ARCHIVED`` marker is written beside them.  ``purge`` removes the whole
directory and drops the index line instead, ``dry_run`` reports the plan
without touching the disk, and a second archive pass is a no-op.

Every fixture is a throwaway workspace under ``tmp_path``: a "run" is a
prepared directory, a ``run.json`` envelope, an optional ``metrics.jsonl``,
the ``COMPLETE`` marker and one index line.  No network, server or model is
involved, and nothing relies on POSIX-only behaviour, so the module runs on
Windows too.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from klea_eval import api, results, run_manager, workspace

pytestmark = pytest.mark.eval

#: Index status of a completed run, as the run manager records it.
COMPLETED = run_manager.STATUS_COMPLETED

#: Fixed timestamp for the synthetic index lines.
TIMESTAMP = "2026-01-01T00:00:00+00:00"


# -- fixtures -------------------------------------------------------------
def _turn_record(run_id: str, case_id: str) -> dict[str, object]:
    """Build one ``metrics.jsonl`` turn record in the run manager's shape."""
    return {
        "case_id": case_id,
        "run_id": run_id,
        "user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:user")),
        "chat_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:chat")),
        "turn_number": 1,
        "role": "user",
        "status": COMPLETED,
        "attempts": 1,
        "task_success": True,
        "expects_grade": True,
        "score": {"task_success": True, "passed": True, "score": 1.0},
    }


def _publish_run(
    root: Path,
    run_id: str,
    case_id: str = "case-a",
    *,
    write_metrics: bool = True,
) -> Path:
    """Publish one run directory with a valid turn record in ``metrics.jsonl``."""
    staging = workspace.prepare_run(run_id, root)
    metadata = results.RunMetadata(
        run_id=run_id,
        case_id=case_id,
        status=COMPLETED,
        harness_seed="0",
        case_version="1.0",
        expects_grade=True,
    )
    results.write_metadata(staging, metadata)
    if write_metrics:
        run_manager.append_metric(
            workspace.metrics_path(staging),
            _turn_record(run_id, case_id),
        )
    return workspace.publish_run(run_id, root)


def _index_run(root: Path, run_id: str, case_id: str = "case-a") -> results.IndexEntry:
    """Append one index line for a published run."""
    entry = results.IndexEntry(
        run_id=run_id,
        case_id=case_id,
        status=COMPLETED,
        timestamp=TIMESTAMP,
    )
    results.append_index_entry(entry, root)
    return entry


def _index_ids(root: Path) -> list[str]:
    """Return the ``run_id`` of every index line, in file order."""
    return [entry.run_id for entry in results.read_index(root)]


def _tree(root: Path) -> list[str]:
    """Return the relative path of every entry under ``root``, sorted."""
    return sorted(str(path.relative_to(root)) for path in root.rglob("*"))


def _two_runs(root: Path, *, write_metrics: bool = True) -> tuple[Path, Path]:
    """Publish and index two completed runs of one case, older first."""
    older = _publish_run(root, "run-older", write_metrics=write_metrics)
    newer = _publish_run(root, "run-newer")
    _index_run(root, "run-older")
    _index_run(root, "run-newer")
    return older, newer


# -- archive keeps the index line -----------------------------------------
def test_archive_keeps_the_index_line(tmp_path: Path) -> None:
    """An archived run keeps its index line and its ``COMPLETE`` result."""
    root = tmp_path / "workspace"
    older, newer = _two_runs(root)

    result = api.apply_retention(root, keep=1)

    assert result.archived_runs == ("run-older",)
    assert result.pruned_runs == ()
    assert _index_ids(root) == ["run-older", "run-newer"]
    assert older.is_dir()
    assert workspace.is_complete(older)
    assert workspace.is_archived(older)
    assert workspace.detect_state("run-older", root) is workspace.RunState.ARCHIVED
    assert workspace.detect_state("run-newer", root) is workspace.RunState.COMPLETE
    assert not workspace.is_archived(newer)


# -- archive keeps the retained file set ----------------------------------
def test_archive_retains_run_json_metrics_and_markers(tmp_path: Path) -> None:
    """Only the ``workspace/`` tree is removed; the record files survive."""
    root = tmp_path / "workspace"
    older, _newer = _two_runs(root, write_metrics=True)
    assert (older / workspace.WORKSPACE_DIR_NAME).is_dir()

    api.apply_retention(root, keep=1)

    assert not (older / workspace.WORKSPACE_DIR_NAME).exists()
    assert results.run_metadata_path(older).is_file()
    assert workspace.metrics_path(older).is_file()
    assert (older / workspace.COMPLETE_MARKER_NAME).is_file()
    assert (older / workspace.ARCHIVED_MARKER_NAME).is_file()


# -- archive keeps the run readable ---------------------------------------
def test_an_archived_run_is_still_readable(tmp_path: Path) -> None:
    """An archived run still resolves through the API and the suite aggregate."""
    root = tmp_path / "workspace"
    _two_runs(root)

    api.apply_retention(root, keep=1)

    record = api.get_run("run-older", root)
    assert record.run_id == "run-older"
    assert record.case_id == "case-a"
    assert record.status == COMPLETED

    assert [entry.run_id for entry in api.list_runs(root)] == [
        "run-older",
        "run-newer",
    ]

    summary = api.suite_summary(root)
    assert isinstance(summary.suite_score, float)
    assert isinstance(summary.pass_at_k, float)


# -- purge removes the directory and the index line -----------------------
def test_purge_removes_the_directory_and_the_index_line(tmp_path: Path) -> None:
    """``purge`` deletes the whole run directory and drops its index line."""
    root = tmp_path / "workspace"
    older, newer = _two_runs(root)

    result = api.apply_retention(root, keep=1, purge=True)

    assert result.pruned_runs == ("run-older",)
    assert result.archived_runs == ()
    assert not older.exists()
    assert newer.is_dir()
    assert _index_ids(root) == ["run-newer"]
    assert workspace.detect_state("run-older", root) is workspace.RunState.NEW


# -- dry run mutates nothing ----------------------------------------------
def test_dry_run_reports_without_mutating(tmp_path: Path) -> None:
    """``dry_run`` names the run it would archive and leaves the disk untouched."""
    root = tmp_path / "workspace"
    older, _newer = _two_runs(root, write_metrics=True)
    before = _tree(root)

    result = api.apply_retention(root, keep=1, dry_run=True)

    assert result.archived_runs == ("run-older",)
    assert result.pruned_runs == ()
    assert result.kept_runs == ("run-older", "run-newer")
    assert result.removed_entries == 0
    assert _tree(root) == before
    assert (older / workspace.WORKSPACE_DIR_NAME).is_dir()
    assert not workspace.is_archived(older)
    assert workspace.detect_state("run-older", root) is workspace.RunState.COMPLETE
    assert _index_ids(root) == ["run-older", "run-newer"]


# -- a second archive pass is a no-op -------------------------------------
def test_a_second_archive_pass_is_a_noop(tmp_path: Path) -> None:
    """Archiving an already archived run changes nothing and reports nothing."""
    root = tmp_path / "workspace"
    older, _newer = _two_runs(root)

    first = api.apply_retention(root, keep=1)
    assert first.archived_runs == ("run-older",)
    after_first = _tree(root)

    second = api.apply_retention(root, keep=1)

    assert second.archived_runs == ()
    assert second.pruned_runs == ()
    assert second.kept_runs == ("run-older", "run-newer")
    assert _tree(root) == after_first
    assert workspace.is_archived(older)
    assert _index_ids(root) == ["run-older", "run-newer"]
