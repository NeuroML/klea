# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the ADR-0060 Aggregate stage (:mod:`klea_eval.scoring`).

Every fixture is a temporary workspace built from synthetic ``metrics.jsonl``
records and ``runs/index.jsonl`` lines, so the suite needs no server, no
network access and no LLM; it carries the ``eval`` marker only.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest
from klea_eval import metrics, results, scoring, workspace

pytestmark = pytest.mark.eval

_TIMESTAMP = "2026-01-01T00:00:00+00:00"


def _runs_dir(root: Path) -> Path:
    """Return the runs directory of the evaluation workspace ``root``."""
    return Path(workspace.runs_dir(root))


def _write_metrics(
    root: Path,
    run_id: str,
    case_id: str,
    *,
    task_success: bool | None = None,
    turn_scores: tuple[Mapping[str, object], ...] = (),
    cost: float | None = None,
    resolved_models: Mapping[str, str] | None = None,
    status: str = metrics.STATUS_COMPLETED,
) -> Path:
    """Write one run's ``metrics.jsonl``: its turn lines and its envelope."""
    run_dir = _runs_dir(root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    lines: list[dict[str, object]] = []
    for number, score in enumerate(turn_scores, start=1):
        line: dict[str, object] = {
            "run_id": run_id,
            "case_id": case_id,
            "turn_number": number,
            "role": "chat",
            "status": metrics.STATUS_COMPLETED,
        }
        if score is not None:
            line["score"] = dict(score)
        lines.append(line)

    envelope: dict[str, object] = {}
    if task_success is not None:
        envelope["task_success"] = task_success
    if cost is not None:
        envelope["cost_estimate"] = cost
    record: dict[str, object] = {
        "run_id": run_id,
        "case_id": case_id,
        "schema_version": metrics.SCHEMA_VERSION,
        "record_type": metrics.RECORD_TYPE_RUN,
        "status": status,
        "metrics": envelope,
    }
    if resolved_models is not None:
        record["resolved_models"] = dict(resolved_models)
    lines.append(record)

    target = run_dir / workspace.METRICS_FILE_NAME
    target.write_text(
        "".join(f"{json.dumps(line)}\n" for line in lines), encoding="utf-8"
    )
    return target


def _append_index(
    root: Path,
    run_id: str,
    case_id: str,
    *,
    status: str = metrics.STATUS_COMPLETED,
) -> None:
    """Append one completed-run line to the workspace index."""
    results.append_index_entry(
        results.IndexEntry(
            run_id=run_id,
            case_id=case_id,
            status=status,
            timestamp=_TIMESTAMP,
        ),
        root=root,
    )


def _snapshot(root: Path) -> dict[str, bytes]:
    """Return every entry under ``root`` as a relative path to its bytes."""
    return {
        str(path.relative_to(root)): b"" if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def test_score_run_marks_a_successful_run_passed() -> None:
    run = metrics.RunMetrics(run_id="run-1", case_id="case-1", task_success=True)

    case = scoring.score_run(run)

    assert case.score == 1.0
    assert case.passed is True
    assert case.status == metrics.STATUS_COMPLETED


def test_score_run_marks_a_failed_run_unpassed() -> None:
    run = metrics.RunMetrics(
        run_id="run-2",
        case_id="case-2",
        status=metrics.STATUS_FAILED,
        task_success=False,
        error="runner timed out",
    )

    case = scoring.score_run(run)

    assert case.score == 0.0
    assert case.passed is False
    assert case.error == "runner timed out"


def test_score_run_averages_the_numeric_rubric_scores() -> None:
    turns = (
        metrics.TurnMetrics(turn_number=1, score={"accuracy": 0.9}),
        metrics.TurnMetrics(turn_number=2, score={"accuracy": {"score": 0.5}}),
    )
    run = metrics.RunMetrics(
        run_id="run-3", case_id="case-3", task_success=True, turns=turns
    )

    case = scoring.score_run(run)

    assert case.score == pytest.approx(0.7)
    assert case.passed is True

    unpassed = metrics.RunMetrics(
        run_id="run-3", case_id="case-3", task_success=False, turns=turns
    )
    unpassed_case = scoring.score_run(unpassed)

    assert unpassed_case.score == pytest.approx(0.7)
    assert unpassed_case.passed is False


def test_aggregate_suite_score_is_the_mean_of_case_scores(tmp_path: Path) -> None:
    _write_metrics(tmp_path, "run-a", "case-a", task_success=True, cost=0.02)
    _write_metrics(
        tmp_path,
        "run-b",
        "case-b",
        task_success=True,
        turn_scores=({"accuracy": 0.5},),
    )
    _write_metrics(tmp_path, "run-c", "case-c", task_success=False)
    for run_id, case_id in (
        ("run-a", "case-a"),
        ("run-b", "case-b"),
        ("run-c", "case-c"),
    ):
        _append_index(tmp_path, run_id, case_id)

    aggregate = scoring.aggregate_suite(tmp_path)

    assert [case.case_id for case in aggregate.cases] == [
        "case-a",
        "case-b",
        "case-c",
    ]
    assert [case.score for case in aggregate.cases] == [1.0, 0.5, 0.0]
    assert aggregate.case_count == 3
    assert aggregate.pass_count == 2
    assert aggregate.suite_score == pytest.approx(0.5)
    assert aggregate.pass_at_k == pytest.approx(2 / 3)
    assert aggregate.cost_estimate == pytest.approx(0.02)
    assert aggregate.root == str(tmp_path)
    assert aggregate.k == scoring.DEFAULT_K == 1
    assert aggregate.missing_runs == ()

    payload = json.loads(json.dumps(aggregate.to_dict()))
    assert payload["case_count"] == 3
    assert payload["suite_score"] == pytest.approx(0.5)


def test_pass_at_k_matches_a_hand_computed_value(tmp_path: Path) -> None:
    _write_metrics(tmp_path, "run-x1", "case-x", task_success=False)
    _write_metrics(tmp_path, "run-x2", "case-x", task_success=True)
    _write_metrics(tmp_path, "run-y1", "case-y", task_success=True)
    _append_index(tmp_path, "run-x1", "case-x")
    _append_index(tmp_path, "run-x2", "case-x")
    _append_index(tmp_path, "run-y1", "case-y")

    first = scoring.aggregate_suite(tmp_path)

    assert first.k == 1
    assert first.case_count == 2
    assert first.pass_count == 1
    assert first.pass_at_k == pytest.approx(0.5)
    assert first.suite_score == pytest.approx(0.5)

    second = scoring.aggregate_suite(tmp_path, k=2)

    assert second.k == 2
    assert second.case_count == 2
    assert second.pass_count == 2
    assert second.pass_at_k == pytest.approx(1.0)
    assert second.suite_score == pytest.approx(0.75)
    assert len(second.cases) == 3


def test_missing_metrics_are_reported_not_raised(tmp_path: Path) -> None:
    _write_metrics(tmp_path, "run-ok", "case-ok", task_success=True)
    _write_metrics(tmp_path, "run-tmp.tmp", "case-tmp", task_success=True)
    _append_index(tmp_path, "run-missing", "case-missing")
    _append_index(tmp_path, "run-ok", "case-ok")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.missing_runs == ("run-missing",)
    assert aggregate.case_count == 2
    assert aggregate.pass_count == 1
    assert aggregate.pass_at_k == pytest.approx(0.5)
    assert aggregate.suite_score == pytest.approx(0.5)
    assert [case.run_id for case in aggregate.cases] == ["run-ok"]
    assert "run-tmp.tmp" not in {case.run_id for case in aggregate.cases}


def test_aggregate_suite_follows_index_order_and_dedupes_runs(tmp_path: Path) -> None:
    _write_metrics(tmp_path, "run-b", "case-b", task_success=True)
    _write_metrics(tmp_path, "run-a", "case-a", task_success=False)
    _append_index(tmp_path, "run-b", "case-b")
    _append_index(tmp_path, "run-a", "case-a")
    _append_index(tmp_path, "run-a", "case-a")

    first = scoring.aggregate_suite(tmp_path)
    second = scoring.aggregate_suite(tmp_path)

    assert [case.case_id for case in first.cases] == ["case-b", "case-a"]
    assert len(first.cases) == 2
    assert first.to_dict() == second.to_dict()


def test_aggregate_suite_rejects_k_below_one(tmp_path: Path) -> None:
    with pytest.raises(scoring.ScoringError):
        scoring.aggregate_suite(tmp_path, k=0)


def test_aggregate_suite_of_an_empty_index(tmp_path: Path) -> None:
    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.cases == ()
    assert aggregate.case_count == 0
    assert aggregate.pass_count == 0
    assert aggregate.suite_score == 0.0
    assert aggregate.pass_at_k == 0.0
    assert aggregate.missing_runs == ()


def test_unsupported_index_schema_is_propagated(tmp_path: Path) -> None:
    target = results.index_path(tmp_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    line = {
        "run_id": "run-1",
        "case_id": "case-1",
        "status": metrics.STATUS_COMPLETED,
        "timestamp": _TIMESTAMP,
        "metrics_path": results.INDEX_METRICS_PATH,
        "schema_version": "2.0",
    }
    target.write_text(f"{json.dumps(line)}\n", encoding="utf-8")

    with pytest.raises(results.ResultsSchemaError):
        scoring.aggregate_suite(tmp_path)


def test_aggregate_suite_writes_nothing(tmp_path: Path) -> None:
    _write_metrics(tmp_path, "run-a", "case-a", task_success=True)
    _append_index(tmp_path, "run-a", "case-a")
    before = _snapshot(tmp_path)

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.case_count == 1
    assert _snapshot(tmp_path) == before


def test_score_run_copies_resolved_models() -> None:
    models = {"chat": "model-a", "judge": "model-b"}
    run = metrics.RunMetrics(
        run_id="run-m",
        case_id="case-m",
        task_success=True,
        resolved_models=models,
    )

    case = scoring.score_run(run)

    assert case.resolved_models == models
    assert case.to_dict()["resolved_models"] == models


def test_aggregate_suite_carries_resolved_models_per_case(tmp_path: Path) -> None:
    models = {"chat": "model-a"}
    _write_metrics(
        tmp_path,
        "run-m",
        "case-m",
        task_success=True,
        resolved_models=models,
    )
    _append_index(tmp_path, "run-m", "case-m")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.cases[0].resolved_models == models
    assert aggregate.cases[0].to_dict()["resolved_models"] == models
