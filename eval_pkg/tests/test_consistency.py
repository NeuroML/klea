# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the index consistency check and its repair (ADR-0059).

Every fixture is a throwaway workspace under ``tmp_path``: a "run" is a
prepared directory, a ``run.json`` envelope, the ``COMPLETE`` marker and one
``runs/index.jsonl`` line.  No network, server or model is involved, and no test
relies on POSIX-only behaviour, so the module runs on Windows too.

Covered here: each drift class that :func:`klea_eval.api.detect_drift` reports
(a complete run the index omits, an index line with no directory, a duplicate
index line, an unmarked directory and a leftover ``.tmp`` staging directory),
the repair :func:`klea_eval.api.check_consistency` applies to each, the
idempotency of a second repair and the exit codes of ``klea-eval check``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from klea_eval import api, results, run_manager, workspace
from klea_eval.ui.cli import app
from typer.testing import CliRunner

pytestmark = pytest.mark.eval

#: Index status of a completed run, as the run manager records it.
COMPLETED = run_manager.STATUS_COMPLETED

#: Fixed timestamp for the synthetic index lines.
TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _publish_run(
    root: Path,
    run_id: str,
    case_id: str = "case-a",
    *,
    write_metadata: bool = True,
) -> Path:
    """Publish one run directory, with or without its ``run.json`` envelope."""
    staging = workspace.prepare_run(run_id, root)
    if write_metadata:
        metadata = results.RunMetadata(
            run_id=run_id,
            case_id=case_id,
            status=COMPLETED,
            harness_seed="0",
            case_version="1.0",
        )
        results.write_metadata(staging, metadata)
    return workspace.publish_run(run_id, root)


def _index_entry(
    root: Path,
    run_id: str,
    case_id: str = "case-a",
    status: str = COMPLETED,
) -> results.IndexEntry:
    """Write one raw index line without requiring a run directory.

    The write bypasses :func:`results.append_index_entry`, which replaces
    an existing line for the same ``run_id``, so a fixture can still build
    the duplicate and orphan index states the consistency checks detect.
    """
    entry = results.IndexEntry(
        run_id=run_id,
        case_id=case_id,
        status=status,
        timestamp=TIMESTAMP,
    )
    path = results.index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        print(json.dumps(entry.to_dict(), sort_keys=True), file=handle)
    return entry


def _index_ids(root: Path) -> list[str]:
    """Return the ``run_id`` of every index line, in file order."""
    return [entry.run_id for entry in results.read_index(root)]


def _invoke(*args: str):
    """Invoke the ``klea-eval check`` command with ``args``."""
    return CliRunner().invoke(app, ["check", *args])


def _json_payload(output: str) -> dict[str, object]:
    """Return the JSON document printed by ``check --json``.

    Any log record that the runner mixed into the captured output is skipped, so
    the helper works whether or not stderr is merged with stdout.
    """
    return json.loads(
        next(line for line in output.splitlines() if line.startswith("{"))
    )


def test_detect_drift_reports_every_class_and_is_read_only(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-missing")
    _index_entry(root, "run-orphan")
    _index_entry(root, "run-dup", case_id="case-a")
    _index_entry(root, "run-dup", case_id="case-b")
    workspace.run_dir("run-unmarked", root).mkdir(parents=True)
    workspace.run_dir_tmp("run-staging", root).mkdir(parents=True)

    before = _index_ids(root)
    drift = api.detect_drift(root)

    assert drift.missing_entries == ("run-missing",)
    # Both the directory-less line and the duplicate name a run with no directory.
    assert set(drift.orphan_entries) == {"run-orphan", "run-dup"}
    assert drift.duplicate_entries == ("run-dup",)
    assert drift.unmarked_directories == ("run-unmarked",)
    assert drift.staging_directories == ("run-staging",)
    assert not drift.repaired
    assert drift.has_drift
    assert drift.unrepairable

    # Detection only: the index and every directory are untouched.
    assert _index_ids(root) == before
    assert workspace.run_dir("run-missing", root).is_dir()
    assert workspace.run_dir("run-unmarked", root).is_dir()
    assert workspace.run_dir_tmp("run-staging", root).is_dir()


def test_repair_reindexes_a_complete_run_missing_from_the_index(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-complete", case_id="case-a")
    assert _index_ids(root) == []

    result = api.check_consistency(root, repair=True)

    assert result.repaired
    assert result.missing_entries == ("run-complete",)
    assert result.unrepairable_entries == ()
    assert _index_ids(root) == ["run-complete"]

    entry = results.read_index(root)[0]
    assert entry.case_id == "case-a"
    assert entry.status == COMPLETED


def test_repair_drops_an_index_line_with_no_directory(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-kept")
    _index_entry(root, "run-kept")
    _index_entry(root, "run-orphan")

    result = api.check_consistency(root, repair=True)

    assert result.orphan_entries == ("run-orphan",)
    assert _index_ids(root) == ["run-kept"]
    assert workspace.run_dir("run-kept", root).is_dir()


def test_repair_keeps_the_first_of_duplicate_index_lines(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-dup", case_id="case-first")
    _index_entry(root, "run-dup", case_id="case-first")
    _index_entry(root, "run-dup", case_id="case-second")

    result = api.check_consistency(root, repair=True)

    assert result.duplicate_entries == ("run-dup",)
    entries = results.read_index(root)
    assert [entry.run_id for entry in entries] == ["run-dup"]
    assert entries[0].case_id == "case-first"


def test_a_staging_directory_is_flagged_but_never_repaired(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    staging = workspace.run_dir_tmp("run-staging", root)
    staging.mkdir(parents=True)
    (staging / "partial.txt").write_text("partial", encoding="utf-8")

    drift = api.detect_drift(root)

    assert drift.staging_directories == ("run-staging",)
    # A leftover .tmp directory is not drift: an interrupted run is no result.
    assert not drift.has_drift
    assert not drift.unrepairable

    result = api.check_consistency(root, repair=True)

    assert result.staging_directories == ("run-staging",)
    assert staging.is_dir()
    assert (staging / "partial.txt").is_file()
    assert results.read_index(root) == ()


def test_an_unmarked_run_directory_is_never_deleted_or_indexed(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    unmarked = workspace.run_dir("run-unmarked", root)
    unmarked.mkdir(parents=True)
    (unmarked / "run.json").write_text("{}\n", encoding="utf-8")

    result = api.check_consistency(root, repair=True)

    assert result.unmarked_directories == ("run-unmarked",)
    assert result.unrepairable
    assert unmarked.is_dir()
    assert (unmarked / "run.json").is_file()
    assert results.read_index(root) == ()


def test_repair_cannot_reindex_a_complete_run_without_metadata(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-broken", write_metadata=False)

    result = api.check_consistency(root, repair=True)

    assert result.repaired
    assert result.missing_entries == ("run-broken",)
    assert result.unrepairable_entries == ("run-broken",)
    assert result.unrepairable
    assert results.read_index(root) == ()


def test_a_second_repair_is_a_noop(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-complete")
    _index_entry(root, "run-orphan")
    _index_entry(root, "run-dup")
    _index_entry(root, "run-dup")

    first = api.check_consistency(root, repair=True)
    assert first.has_drift
    assert _index_ids(root) == ["run-complete"]

    settled = results.read_index(root)
    second = api.check_consistency(root, repair=True)

    assert not second.has_drift
    assert not second.repaired
    assert second.missing_entries == ()
    assert second.orphan_entries == ()
    assert second.duplicate_entries == ()
    assert results.read_index(root) == settled


def test_cli_check_reports_drift_and_exits_1(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-complete")

    result = _invoke("--workspace", str(root))

    assert result.exit_code == 1
    assert "missing: run-complete" in result.output
    assert _index_ids(root) == []


def test_cli_check_repair_fixes_drift_and_exits_0(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-complete")
    _index_entry(root, "run-orphan")

    result = _invoke("--workspace", str(root), "--repair")

    assert result.exit_code == 0
    assert "missing: run-complete" in result.output
    assert "orphan: run-orphan" in result.output
    assert _index_ids(root) == ["run-complete"]

    again = _invoke("--workspace", str(root), "--repair")

    assert again.exit_code == 0
    assert "index consistent" in again.output


def test_cli_check_exits_3_when_drift_cannot_be_repaired(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    workspace.run_dir("run-unmarked", root).mkdir(parents=True)

    result = _invoke("--workspace", str(root), "--repair")

    assert result.exit_code == 3
    assert "unmarked: run-unmarked" in result.output
    assert workspace.run_dir("run-unmarked", root).is_dir()


def test_cli_check_exits_2_on_an_unreadable_index(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    target = results.index_path(root)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "run_id": "run-future",
        "case_id": "case-a",
        "status": COMPLETED,
        "timestamp": TIMESTAMP,
        "metrics_path": results.INDEX_METRICS_PATH,
        "schema_version": "99.0",
    }
    target.write_text(json.dumps(line) + "\n", encoding="utf-8")

    result = _invoke("--workspace", str(root))

    assert result.exit_code == 2


def test_cli_check_json_output_names_the_drift(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _publish_run(root, "run-complete")

    result = _invoke("--workspace", str(root), "--json")

    assert result.exit_code == 1
    payload = _json_payload(result.output)
    assert payload["missing_entries"] == ["run-complete"]
    assert payload["orphan_entries"] == []
    assert payload["duplicate_entries"] == []
    assert payload["staging_directories"] == []
    assert payload["repaired"] is False


def test_an_archived_run_with_an_index_line_is_consistent(tmp_path: Path) -> None:
    """A complete run carrying ``ARCHIVED`` while indexed is neither missing nor orphan."""
    root = tmp_path / "workspace"
    directory = _publish_run(root, "run-archived")
    (directory / workspace.ARCHIVED_MARKER_NAME).write_text("", encoding="utf-8")
    _index_entry(root, "run-archived")
    assert workspace.is_archived(directory)

    drift = api.detect_drift(root)

    assert drift.archived_directories == ("run-archived",)
    assert drift.missing_entries == ()
    assert drift.orphan_entries == ()
    assert not drift.has_drift
    assert not drift.unrepairable

    result = _invoke("--workspace", str(root))

    assert result.exit_code == 0
    assert "index consistent" in result.output


def test_repair_reindexes_an_archived_run_missing_from_the_index(
    tmp_path: Path,
) -> None:
    """An archived run with no index line is a missing entry, repaired from run.json."""
    root = tmp_path / "workspace"
    directory = _publish_run(root, "run-archived")
    (directory / workspace.ARCHIVED_MARKER_NAME).write_text("", encoding="utf-8")
    assert _index_ids(root) == []

    drift = api.detect_drift(root)

    assert drift.archived_directories == ("run-archived",)
    assert drift.missing_entries == ("run-archived",)
    assert drift.has_drift

    result = api.check_consistency(root, repair=True)

    assert result.repaired
    assert result.missing_entries == ("run-archived",)
    assert result.unrepairable_entries == ()
    assert _index_ids(root) == ["run-archived"]

    entry = results.read_index(root)[0]
    assert entry.case_id == "case-a"
    assert entry.status == COMPLETED


def test_an_archived_directory_without_complete_is_unmarked(tmp_path: Path) -> None:
    """An ``ARCHIVED`` marker without ``COMPLETE`` is unmarked and never repaired."""
    root = tmp_path / "workspace"
    directory = workspace.run_dir("run-half-archived", root)
    directory.mkdir(parents=True)
    (directory / "run.json").write_text("{}\n", encoding="utf-8")
    (directory / workspace.ARCHIVED_MARKER_NAME).write_text("", encoding="utf-8")

    drift = api.detect_drift(root)

    assert drift.unmarked_directories == ("run-half-archived",)
    assert drift.archived_directories == ()
    assert drift.unrepairable

    result = _invoke("--workspace", str(root), "--repair")

    assert result.exit_code == 3
    assert "unmarked: run-half-archived" in result.output
    assert directory.is_dir()


def test_an_index_line_without_a_directory_is_still_orphan_drift(
    tmp_path: Path,
) -> None:
    """An index line with no run directory stays orphan drift after archiving support."""
    root = tmp_path / "workspace"
    _index_entry(root, "run-orphan")

    drift = api.detect_drift(root)

    assert drift.orphan_entries == ("run-orphan",)
    assert drift.archived_directories == ()
    assert drift.has_drift

    result = api.check_consistency(root, repair=True)

    assert result.orphan_entries == ("run-orphan",)
    assert _index_ids(root) == []
