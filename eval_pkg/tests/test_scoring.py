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
    expectation_checks: tuple[Mapping[str, object], ...] = (),
    turn_tokens: tuple[Mapping[str, object], ...] = (),
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

    for offset, tokens in enumerate(turn_tokens, start=1):
        lines.append(
            {
                "run_id": run_id,
                "case_id": case_id,
                "turn_number": len(turn_scores) + offset,
                "role": "chat",
                "status": metrics.STATUS_COMPLETED,
                "token_usage": dict(tokens),
            }
        )

    envelope: dict[str, object] = {}
    if task_success is not None:
        envelope["task_success"] = task_success
    if expectation_checks:
        envelope["expectation_checks"] = [dict(check) for check in expectation_checks]
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


def _write_run_json(
    root: Path,
    run_id: str,
    case_id: str,
    *,
    expects_grade: bool = False,
    status: str = metrics.STATUS_COMPLETED,
) -> Path:
    """Write one run's ``run.json`` envelope, recording whether it has rubrics."""
    run_dir = _runs_dir(root) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "case_id": case_id,
        "status": status,
        "expects_grade": expects_grade,
    }
    target = run_dir / results.RUN_FILE_NAME
    target.write_text(json.dumps(payload), encoding="utf-8")
    return target


def _snapshot(root: Path) -> dict[str, bytes]:
    """Return every entry under ``root`` as a relative path to its bytes."""
    return {
        str(path.relative_to(root)): b"" if path.is_dir() else path.read_bytes()
        for path in sorted(root.rglob("*"))
    }


def _check(
    target: str,
    outcome: str,
    *,
    kind: str = "file_present",
    detail: str | None = None,
) -> dict[str, object]:
    """Build one stored ``expectation_checks`` record for a run envelope."""
    return {
        "turn_number": 1,
        "kind": kind,
        "target": target,
        "outcome": outcome,
        "detail": detail,
    }


def test_score_run_grades_from_the_recorded_checks() -> None:
    # Every recorded verdict passed, so the run is a graded pass even without a
    # task_success of its own.
    run = metrics.RunMetrics(
        run_id="run-4",
        case_id="case-4",
        expectation_checks=(
            metrics.ExpectationCheckRecord(
                name="out.txt", kind="file_present", outcome="pass"
            ),
            metrics.ExpectationCheckRecord(
                name="read_file", kind="tool_name", outcome="pass"
            ),
        ),
    )

    case = scoring.score_run(run)

    assert case.score == 1.0
    assert case.passed is True
    assert case.failed_check is None


def test_score_run_fails_on_a_recorded_failing_check() -> None:
    # A recorded fail wins over a passing task_success and names the check, so
    # the case is a graded fail (exit 1) rather than an unverified pass.
    run = metrics.RunMetrics(
        run_id="run-5",
        case_id="case-5",
        task_success=True,
        expectation_checks=(
            metrics.ExpectationCheckRecord(
                name="out.txt", kind="file_present", outcome="pass"
            ),
            metrics.ExpectationCheckRecord(
                name="summary.txt",
                kind="validation_rule",
                outcome="fail",
                detail="validation_rule 'contains' did not hold for 'summary.txt'",
            ),
        ),
    )

    case = scoring.score_run(run)

    assert case.score == 0.0
    assert case.passed is False
    assert case.failed_check == "summary.txt"
    assert case.to_dict()["failed_check"] == "summary.txt"


def test_aggregate_counts_a_recorded_failing_check_as_a_failed_case(
    tmp_path: Path,
) -> None:
    _write_metrics(
        tmp_path,
        "run-pass",
        "case-pass",
        expectation_checks=(_check("out.txt", "pass"),),
    )
    _write_metrics(
        tmp_path,
        "run-fail",
        "case-fail",
        expectation_checks=(
            _check("out.txt", "pass"),
            _check("summary.txt", "fail", kind="validation_rule"),
        ),
    )
    _append_index(tmp_path, "run-pass", "case-pass")
    _append_index(tmp_path, "run-fail", "case-fail")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.case_count == 2
    assert aggregate.pass_count == 1
    assert aggregate.suite_score == pytest.approx(0.5)
    failed = [case for case in aggregate.cases if case.case_id == "case-fail"]
    assert len(failed) == 1
    assert failed[0].passed is False
    assert failed[0].failed_check == "summary.txt"


def test_a_case_declaring_expectations_with_no_recorded_checks_is_grade_missing(
    tmp_path: Path,
) -> None:
    # The case expects a grade but no verdict was recorded, so it stays
    # grade-missing (exit 4) instead of passing or failing by default, and it is
    # kept out of the score and pass@k denominators.
    _write_metrics(tmp_path, "run-gm", "case-gm")
    _append_index(tmp_path, "run-gm", "case-gm")
    _write_run_json(tmp_path, "run-gm", "case-gm", expects_grade=True)

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.grade_missing_count == 1
    assert aggregate.ungraded_count == 0
    assert aggregate.case_count == 0
    assert aggregate.pass_count == 0
    assert aggregate.pass_at_k == 0.0
    assert aggregate.missing_runs == ()


def test_a_case_without_expectations_is_ungraded(tmp_path: Path) -> None:
    # A case declaring nothing is ungraded (exit 0), counted apart from a
    # grade-missing one.
    _write_metrics(tmp_path, "run-un", "case-un")
    _append_index(tmp_path, "run-un", "case-un")
    _write_run_json(tmp_path, "run-un", "case-un", expects_grade=False)

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.ungraded_count == 1
    assert aggregate.grade_missing_count == 0
    assert aggregate.case_count == 0


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


def test_aggregate_suite_excludes_ungraded_cases_from_the_denominator(
    tmp_path: Path,
) -> None:
    _write_metrics(tmp_path, "run-graded", "case-graded", task_success=True)
    _write_metrics(tmp_path, "run-ungraded", "case-ungraded", task_success=None)
    _append_index(tmp_path, "run-graded", "case-graded")
    _append_index(tmp_path, "run-ungraded", "case-ungraded")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.case_count == 1
    assert aggregate.pass_count == 1
    assert aggregate.ungraded_count == 1
    assert aggregate.grade_missing_count == 0
    assert aggregate.suite_score == pytest.approx(1.0)
    assert aggregate.pass_at_k == pytest.approx(1.0)
    assert {case.case_id for case in aggregate.cases} == {
        "case-graded",
        "case-ungraded",
    }


def test_aggregate_suite_of_an_all_ungraded_suite(tmp_path: Path) -> None:
    _write_metrics(tmp_path, "run-u1", "case-u1", task_success=None)
    _write_metrics(tmp_path, "run-u2", "case-u2", task_success=None)
    _append_index(tmp_path, "run-u1", "case-u1")
    _append_index(tmp_path, "run-u2", "case-u2")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.case_count == 0
    assert aggregate.pass_count == 0
    assert aggregate.ungraded_count == 2
    assert aggregate.grade_missing_count == 0
    assert aggregate.suite_score == 0.0
    assert aggregate.pass_at_k == 0.0
    assert len(aggregate.cases) == 2


def test_aggregate_suite_counts_grade_missing_without_moving_the_score(
    tmp_path: Path,
) -> None:
    _write_metrics(tmp_path, "run-graded", "case-graded", task_success=True)
    _write_metrics(tmp_path, "run-missing", "case-missing", task_success=None)
    _write_run_json(tmp_path, "run-missing", "case-missing", expects_grade=True)
    _append_index(tmp_path, "run-graded", "case-graded")
    _append_index(tmp_path, "run-missing", "case-missing")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.case_count == 1
    assert aggregate.pass_count == 1
    assert aggregate.ungraded_count == 0
    assert aggregate.grade_missing_count == 1
    assert aggregate.suite_score == pytest.approx(1.0)
    assert aggregate.pass_at_k == pytest.approx(1.0)


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


def test_aggregate_reports_the_output_to_input_token_ratio(tmp_path: Path) -> None:
    # The ratio is output tokens per input token over the aggregate totals.
    _write_metrics(
        tmp_path,
        "run-ratio",
        "case-ratio",
        task_success=True,
        turn_tokens=(({"input_tokens": 100, "output_tokens": 25},)),
    )
    _append_index(tmp_path, "run-ratio", "case-ratio")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.total_input_tokens == 100
    assert aggregate.total_output_tokens == 25
    assert aggregate.input_output_ratio == pytest.approx(0.25)
    assert aggregate.to_dict()["input_output_ratio"] == pytest.approx(0.25)


def test_aggregate_ratio_is_null_without_input_tokens(tmp_path: Path) -> None:
    # A zero input total leaves the ratio undefined: null, never an exception.
    _write_metrics(
        tmp_path,
        "run-zero",
        "case-zero",
        task_success=True,
        turn_tokens=(({"input_tokens": 0, "output_tokens": 40},)),
    )
    _append_index(tmp_path, "run-zero", "case-zero")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.total_input_tokens == 0
    assert aggregate.total_output_tokens == 40
    assert aggregate.input_output_ratio is None
    assert aggregate.to_dict()["input_output_ratio"] is None


def test_aggregate_ratio_is_null_when_runs_are_missing(tmp_path: Path) -> None:
    # An unpublished run contributes no tokens, so the ratio stays null.
    _write_metrics(tmp_path, "run-present", "case-present", task_success=True)
    _append_index(tmp_path, "run-missing", "case-missing")
    _append_index(tmp_path, "run-present", "case-present")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.missing_runs == ("run-missing",)
    assert aggregate.total_input_tokens == 0
    assert aggregate.input_output_ratio is None


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


def test_aggregate_suite_counts_a_scheduled_case_without_a_run(tmp_path: Path) -> None:
    """A scheduled case that published no run still enters the denominator."""
    _write_metrics(tmp_path, "run-pass", "case-pass", task_success=True)
    _append_index(tmp_path, "run-pass", "case-pass")

    aggregate = scoring.aggregate_suite(tmp_path, case_ids=["case-pass", "case-failed"])

    assert aggregate.case_count == 2
    assert aggregate.pass_count == 1
    assert aggregate.pass_at_k == 0.5


def test_aggregate_suite_propagates_a_metrics_schema_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stored run violating the metrics schema is an error, not a zero score."""
    _append_index(tmp_path, "run-bad", "case-bad")

    def broken_load(run_dir: Path) -> None:
        raise metrics.MetricsSchemaError("bad record")

    monkeypatch.setattr(metrics, "load_run", broken_load)

    with pytest.raises(scoring.ScoringError):
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


def test_score_run_copies_per_call_usage() -> None:
    """The per-call usage of a run is carried onto its case score."""
    run = metrics.RunMetrics(
        run_id="run-calls",
        case_id="case-calls",
        task_success=True,
        llm_calls=3,
        per_call_usage=(
            {"input_tokens": 10, "output_tokens": 5},
            {"input_tokens": 2, "output_tokens": 3},
        ),
        peak_context_tokens=15,
    )

    case = scoring.score_run(run)

    assert case.llm_calls == 3
    assert case.per_call_usage == run.per_call_usage
    assert case.peak_context_tokens == 15
    payload = case.to_dict()
    assert payload["llm_calls"] == 3
    assert payload["per_call_usage"] == [
        {"input_tokens": 10, "output_tokens": 5},
        {"input_tokens": 2, "output_tokens": 3},
    ]
    assert payload["peak_context_tokens"] == 15


def test_aggregate_suite_sums_llm_calls_and_keeps_the_peak(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The suite sums the per-case call counts and keeps the highest peak."""
    summaries = {
        "run-a": metrics.RunMetrics(
            run_id="run-a",
            case_id="case-a",
            task_success=True,
            llm_calls=2,
            peak_context_tokens=15,
        ),
        "run-b": metrics.RunMetrics(
            run_id="run-b",
            case_id="case-b",
            task_success=True,
            llm_calls=3,
            peak_context_tokens=140,
        ),
    }
    for run_id, case_id in (("run-a", "case-a"), ("run-b", "case-b")):
        _append_index(tmp_path, run_id, case_id)
        run_dir = tmp_path / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "metrics.jsonl").write_text("", encoding="utf-8")

    def fake_load(run_dir: Path) -> metrics.RunMetrics:
        for run_id, summary in summaries.items():
            if run_id in str(run_dir):
                return summary
        raise AssertionError(f"unexpected run directory: {run_dir}")

    monkeypatch.setattr(metrics, "load_run", fake_load)

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.llm_calls == 5
    assert aggregate.peak_context_tokens == 140
    payload = aggregate.to_dict()
    assert payload["llm_calls"] == 5
    assert payload["peak_context_tokens"] == 140


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


def test_aggregate_suite_sums_normalised_units(tmp_path: Path) -> None:
    _write_metrics(
        tmp_path,
        "run-u",
        "case-u",
        task_success=True,
        turn_tokens=(
            {"input_tokens": 1000, "cached_tokens": 600, "output_tokens": 200},
        ),
    )
    _append_index(tmp_path, "run-u", "case-u")
    _write_metrics(
        tmp_path,
        "run-v",
        "case-v",
        task_success=True,
        turn_tokens=(({"input_tokens": 100, "output_tokens": 40}),),
    )
    _append_index(tmp_path, "run-v", "case-v")

    aggregate = scoring.aggregate_suite(tmp_path)

    by_case = {case.case_id: case for case in aggregate.cases}
    assert by_case["case-u"].total_normalised_units == 660.0
    assert by_case["case-v"].total_normalised_units == 140.0
    assert by_case["case-u"].to_dict()["total_normalised_units"] == 660.0
    assert aggregate.total_normalised_units == 800.0
    assert aggregate.to_dict()["total_normalised_units"] == 800.0


def test_suite_normalised_units_ignore_a_cost_estimate(tmp_path: Path) -> None:
    _write_metrics(
        tmp_path,
        "run-c",
        "case-c",
        task_success=True,
        cost=12.5,
        turn_tokens=(({"input_tokens": 100, "output_tokens": 100}),),
    )
    _append_index(tmp_path, "run-c", "case-c")

    aggregate = scoring.aggregate_suite(tmp_path)

    assert aggregate.cost_estimate == 12.5
    assert aggregate.total_normalised_units == 200.0


def test_summary_command_prints_per_call_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The summary command echoes the per-call usage and peak context lines."""
    from klea_eval import api
    from klea_eval.ui import cli
    from typer.testing import CliRunner

    aggregate = scoring.SuiteAggregate(
        root=None,
        llm_calls=5,
        peak_context_tokens=140,
    )
    monkeypatch.setattr(api, "suite_summary", lambda *args, **kwargs: aggregate)

    result = CliRunner().invoke(cli.app, ["summary"])

    assert result.exit_code == 0
    assert "llm_calls: 5" in result.output
    assert "peak_context_tokens: 140" in result.output


def test_summary_command_prints_na_without_a_peak(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An aggregate without a peak reports the fallback instead of a number."""
    from klea_eval import api
    from klea_eval.ui import cli
    from typer.testing import CliRunner

    aggregate = scoring.SuiteAggregate(root=None)
    monkeypatch.setattr(api, "suite_summary", lambda *args, **kwargs: aggregate)

    result = CliRunner().invoke(cli.app, ["summary"])

    assert result.exit_code == 0
    assert "llm_calls: 0" in result.output
    assert "peak_context_tokens: n/a" in result.output
