# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the run read/inspect API and retention (P8).

The tests build a throwaway evaluation workspace under ``tmp_path`` and write
synthetic runs into it with the ADR-0059 writer and the run manager's metric
appender, so no network, server or model is involved: a "run" is a prepared
directory, one or more turn records in ``metrics.jsonl``, a ``run.json``
envelope, the ``COMPLETE`` marker and one ``runs/index.jsonl`` line.

Covered here: reading a stored run through :func:`klea_eval.api.get_run`, the
unknown, missing and incomplete run faults, the index-order listing and its
filters, the suite aggregate delegation, the ADR-0059 retention policy (last
:data:`klea_eval.api.DEFAULT_RETENTION` completed runs per case) and the CLI
commands that expose them.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from klea_eval import api, results, run_manager, workspace
from klea_eval.ui.cli import app
from typer.testing import CliRunner

pytestmark = pytest.mark.eval

#: Index status of a completed run, as the run manager records it.
COMPLETED = run_manager.STATUS_COMPLETED

#: Index status of a failed run, as the run manager records it.
FAILED = run_manager.STATUS_FAILED

#: Fixed timestamp for the synthetic index lines, so listing order is stable.
TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _turn_record(
    run_id: str,
    case_id: str,
    *,
    passed: bool,
    turn_number: int = 1,
) -> dict[str, object]:
    """Build one ``metrics.jsonl`` turn record in the run manager's shape."""
    outcome = {
        "task_success": passed,
        "passed": passed,
        "score": 1.0 if passed else 0.0,
    }
    return {
        "case_id": case_id,
        "run_id": run_id,
        "user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:user")),
        "chat_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}:chat")),
        "turn_number": turn_number,
        "role": "user",
        "status": COMPLETED,
        "attempts": 1,
        "task_success": passed,
        "score": outcome,
    }


def _make_run(
    root: Path,
    run_id: str,
    case_id: str,
    *,
    passed: bool = True,
    status: str = COMPLETED,
    write_metrics: bool = True,
) -> Path:
    """Write and publish one synthetic run, then append its index line."""
    staging = workspace.prepare_run(run_id, root)
    if write_metrics:
        run_manager.append_metric(
            workspace.metrics_path(staging),
            _turn_record(run_id, case_id, passed=passed),
        )

    metadata = results.RunMetadata(
        run_id=run_id,
        case_id=case_id,
        status=status,
        harness_seed="0",
        case_version="1.0",
    )
    stamped = results.write_metadata(staging, metadata)
    published = workspace.publish_run(run_id, root)
    results.append_index_entry(results.to_index_entry(stamped), root)
    return published


def _append_entry(
    root: Path,
    run_id: str,
    case_id: str,
    *,
    status: str = COMPLETED,
) -> results.IndexEntry:
    """Append one index line without creating a run directory."""
    entry = results.IndexEntry(
        run_id=run_id,
        case_id=case_id,
        status=status,
        timestamp=TIMESTAMP,
    )
    results.append_index_entry(entry, root)
    return entry


def _index_ids(root: Path) -> list[str]:
    """Return the ``run_id`` of every index line, in file order."""
    return [entry.run_id for entry in results.read_index(root)]


def _index_lines(root: Path) -> list[dict[str, object]]:
    """Parse ``runs/index.jsonl`` line by line."""
    target = results.index_path(root)
    return [
        json.loads(line) for line in target.read_text(encoding="utf-8").splitlines()
    ]


def _populate_case(root: Path, case_id: str, count: int, prefix: str) -> list[str]:
    """Create ``count`` completed runs for one case, oldest first."""
    run_ids: list[str] = []
    for index in range(count):
        run_id = f"{prefix}-{index:02d}"
        _make_run(root, run_id, case_id, passed=index % 2 == 0)
        run_ids.append(run_id)
    return run_ids


def test_run_not_found_is_an_api_error() -> None:
    assert issubclass(api.RunNotFoundError, api.ApiError)
    assert issubclass(api.ApiError, Exception)


def test_get_run_returns_envelope_and_metrics(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a", passed=True)

    record = api.get_run("run-1", root)

    assert record.run_id == "run-1"
    assert record.case_id == "case-a"
    assert record.status == COMPLETED
    assert record.metrics_path == results.INDEX_METRICS_PATH
    assert record.metadata is not None
    assert record.metadata.run_id == "run-1"
    assert record.metrics is not None
    assert record.passed is True

    payload = record.to_dict()
    assert payload["run_id"] == "run-1"
    assert payload["case_id"] == "case-a"
    assert payload["passed"] is True
    assert payload["metadata"] is not None


def test_get_run_unknown_identifier_raises(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a")

    with pytest.raises(api.RunNotFoundError):
        api.get_run("nope", root)


def test_get_run_rejects_an_empty_identifier(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a")

    with pytest.raises(api.ApiError):
        api.get_run("", root)
    with pytest.raises(api.ApiError):
        api.get_run("   ", root)


def test_get_run_missing_directory_raises(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _append_entry(root, "run-gone", "case-a", status=COMPLETED)

    with pytest.raises(api.RunNotFoundError):
        api.get_run("run-gone", root)


def test_get_run_incomplete_directory_raises(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    directory = workspace.run_dir("run-open", root)
    directory.mkdir(parents=True)
    _append_entry(root, "run-open", "case-a", status=COMPLETED)

    with pytest.raises(api.RunNotFoundError):
        api.get_run("run-open", root)


def test_get_run_rejects_an_unknown_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    staging = workspace.prepare_run("run-future", root)
    (staging / results.RUN_FILE_NAME).write_text(
        json.dumps(
            {
                "run_id": "run-future",
                "case_id": "case-a",
                "status": COMPLETED,
                "schema_version": "9.9",
            }
        ),
        encoding="utf-8",
    )
    workspace.publish_run("run-future", root)
    _append_entry(root, "run-future", "case-a", status=COMPLETED)

    with pytest.raises(results.ResultsSchemaError):
        api.get_run("run-future", root)


def test_run_record_passed_follows_task_success(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-pass", "case-a", passed=True)
    _make_run(root, "run-fail", "case-a", passed=False)

    assert api.get_run("run-pass", root).passed is True
    assert api.get_run("run-fail", root).passed is False

    assert api.get_run("run-fail", root).to_dict()["passed"] is False


def test_list_runs_keeps_index_order_and_filters(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-a1", "case-a", passed=True)
    _make_run(root, "run-a2", "case-a", passed=False)
    _make_run(root, "run-b1", "case-b", passed=True)
    _append_entry(root, "run-fail", "case-a", status=FAILED)

    everything = api.list_runs(root)
    assert [entry.run_id for entry in everything] == [
        "run-a1",
        "run-a2",
        "run-b1",
        "run-fail",
    ]

    case_a = api.list_runs(root, case_id="case-a")
    assert [entry.run_id for entry in case_a] == ["run-a1", "run-a2", "run-fail"]

    completed = api.list_runs(root, status="completed")
    assert [entry.run_id for entry in completed] == ["run-a1", "run-a2", "run-b1"]

    failed = api.list_runs(root, status="FAILED")
    assert [entry.run_id for entry in failed] == ["run-fail"]

    combined = api.list_runs(root, case_id="case-b", status="completed")
    assert [entry.run_id for entry in combined] == ["run-b1"]


def test_list_runs_without_an_index_is_empty(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    assert api.list_runs(root) == ()


def test_list_runs_rejects_a_non_string_filter(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a")

    with pytest.raises(api.ApiError):
        api.list_runs(root, case_id=1)  # ty: ignore[invalid-argument-type]


def test_suite_summary_delegates_to_scoring(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-pass", "case-a", passed=True)
    _make_run(root, "run-fail", "case-b", passed=False)

    aggregate = api.suite_summary(root, k=1)

    assert aggregate.case_count == 2
    assert aggregate.pass_count == 1
    assert aggregate.pass_at_k == 0.5
    assert aggregate.suite_score == 0.5
    assert aggregate.to_dict()["k"] == 1


def test_apply_retention_keeps_the_last_20_per_case(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    run_ids = _populate_case(root, "case-a", 22, "run")

    result = api.apply_retention(root, keep=20)

    assert result.keep == 20
    assert result.removed_entries == 2
    assert result.kept_entries == 20
    assert result.pruned_runs == tuple(run_ids[:2])
    assert result.kept_runs == tuple(run_ids[2:])

    assert not workspace.run_dir(run_ids[0], root).exists()
    assert not workspace.run_dir(run_ids[1], root).exists()
    assert workspace.run_dir(run_ids[2], root).is_dir()
    assert workspace.run_dir(run_ids[-1], root).is_dir()

    assert _index_ids(root) == run_ids[2:]
    assert len(_index_lines(root)) == 20

    temporary = results.index_path(root).with_name("index.jsonl.tmp")
    assert not temporary.exists()


def test_apply_retention_defaults_to_20(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    run_ids = _populate_case(root, "case-a", 23, "run")

    result = api.apply_retention(root)

    assert api.DEFAULT_RETENTION == 20
    assert result.keep == 20
    assert result.removed_entries == 3
    assert result.pruned_runs == tuple(run_ids[:3])
    assert _index_ids(root) == run_ids[3:]


def test_apply_retention_is_per_case(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    case_a = _populate_case(root, "case-a", 22, "a")
    case_b = _populate_case(root, "case-b", 3, "b")

    result = api.apply_retention(root, keep=20)

    assert result.removed_entries == 2
    assert result.pruned_runs == tuple(case_a[:2])
    assert _index_ids(root) == case_a[2:] + case_b

    for run_id in case_b:
        assert workspace.run_dir(run_id, root).is_dir()


def test_apply_retention_within_the_window_is_a_noop(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    run_ids = _populate_case(root, "case-a", 3, "run")

    result = api.apply_retention(root, keep=20)

    assert result.removed_entries == 0
    assert result.kept_entries == 3
    assert result.pruned_runs == ()
    assert result.kept_runs == tuple(run_ids)
    assert _index_ids(root) == run_ids


def test_apply_retention_ignores_incomplete_runs_and_tmp(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    run_ids = _populate_case(root, "case-a", 2, "run")
    _append_entry(root, "run-failed", "case-a", status=FAILED)
    leftover = workspace.run_dir_tmp("run-failed", root)
    leftover.mkdir(parents=True)

    result = api.apply_retention(root, keep=1)

    assert result.removed_entries == 1
    assert result.pruned_runs == (run_ids[0],)
    assert not workspace.run_dir(run_ids[0], root).exists()
    assert workspace.run_dir(run_ids[1], root).is_dir()
    assert leftover.is_dir()
    assert _index_ids(root) == [run_ids[1], "run-failed"]


def test_apply_retention_without_an_index_is_a_noop(tmp_path: Path) -> None:
    root = tmp_path / "empty"

    result = api.apply_retention(root, keep=5)

    assert result.keep == 5
    assert result.kept_runs == ()
    assert result.pruned_runs == ()
    assert result.kept_entries == 0
    assert result.removed_entries == 0


def test_apply_retention_rejects_a_bad_keep(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    with pytest.raises(api.ApiError):
        api.apply_retention(root, keep=0)
    with pytest.raises(api.ApiError):
        api.apply_retention(root, keep=-1)
    with pytest.raises(api.ApiError):
        api.apply_retention(root, keep=True)


def test_retention_result_to_dict(tmp_path: Path) -> None:
    result = api.RetentionResult(
        keep=1,
        kept_runs=("run-b",),
        pruned_runs=("run-a",),
        kept_entries=1,
        removed_entries=1,
    )

    assert result.to_dict() == {
        "keep": 1,
        "kept_runs": ["run-b"],
        "pruned_runs": ["run-a"],
        "kept_entries": 1,
        "removed_entries": 1,
    }


def test_cli_show_a_passing_run(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a", passed=True)
    runner = CliRunner()

    result = runner.invoke(app, ["show", "run-1", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "run-1" in result.output
    assert "case-a" in result.output
    assert "pass" in result.output


def test_cli_show_a_failing_run(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a", passed=False)
    runner = CliRunner()

    result = runner.invoke(app, ["show", "run-1", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "fail" in result.output


def test_cli_show_an_unknown_run_exits_2(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a")
    runner = CliRunner()

    result = runner.invoke(app, ["show", "nope", "--workspace", str(root)])

    assert result.exit_code == 2


def test_cli_runs_lists_the_index(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-a", "case-a", passed=True)
    _make_run(root, "run-b", "case-b", passed=False)
    runner = CliRunner()

    result = runner.invoke(app, ["runs", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "run-a" in result.output
    assert "run-b" in result.output

    filtered = runner.invoke(
        app, ["runs", "--workspace", str(root), "--case-id", "case-a"]
    )

    assert filtered.exit_code == 0
    assert "run-a" in filtered.output
    assert "run-b" not in filtered.output


def test_cli_prune_reports_the_counts(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _populate_case(root, "case-a", 22, "run")
    runner = CliRunner()

    result = runner.invoke(app, ["prune", "--workspace", str(root), "--keep", "20"])

    assert result.exit_code == 0
    assert "kept 20 runs" in result.output
    assert "pruned 2 runs" in result.output
    assert len(_index_ids(root)) == 20


def test_cli_prune_rejects_a_bad_keep(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-1", "case-a")
    runner = CliRunner()

    result = runner.invoke(app, ["prune", "--workspace", str(root), "--keep", "0"])

    assert result.exit_code == 2


def test_cli_summary_prints_the_aggregate(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    _make_run(root, "run-pass", "case-a", passed=True)
    _make_run(root, "run-fail", "case-b", passed=False)
    runner = CliRunner()

    result = runner.invoke(app, ["summary", "--workspace", str(root)])

    assert result.exit_code == 0
    assert "suite_score" in result.output
    assert "pass_at_k" in result.output
