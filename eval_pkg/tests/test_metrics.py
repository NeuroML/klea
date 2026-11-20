# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline unit tests for the metrics collector (:mod:`klea_eval.metrics`).

Every fixture is a hand-written ``metrics.jsonl`` written under ``tmp_path``:
no model, no network call and no real evaluation workspace root is used, so the
whole module runs in the default (non-``eval``) selection as well as under
``pytest -m eval -k metrics``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, cast

import pytest
from klea_eval.metrics import (
    SCHEMA_VERSION,
    MetricsError,
    MetricsSchemaError,
    aggregate,
    compare_runs,
    load_run,
    read_records,
)

pytestmark = pytest.mark.eval

#: Logger the collector writes to; warnings are asserted through it.
METRICS_LOGGER = "klea_eval.metrics"

#: Envelope token totals that match the two turns used by the envelope tests.
MATCHING_TOTALS = {
    "total_input_tokens": 70,
    "total_output_tokens": 30,
    "total_cached_tokens": 0,
}


def _write_lines(path: Path, *lines: str) -> Path:
    """Write ``lines`` to ``path``, one per line, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _write_records(path: Path, *records: dict[str, Any]) -> Path:
    """Serialise ``records`` as JSONL and return the written path."""
    lines = tuple(json.dumps(record, sort_keys=True) for record in records)
    return _write_lines(path, *lines)


def _turn(
    number: int,
    run_id: str | None = "run-1",
    case_id: str | None = "case-1",
    **extra: Any,
) -> dict[str, Any]:
    """Build a per-turn record shaped like the run manager's output."""
    record: dict[str, Any] = {
        "case_id": case_id,
        "run_id": run_id,
        "turn_number": number,
        "role": "chat",
        "status": "completed",
    }
    record.update(extra)
    return record


def _envelope(**metrics: Any) -> dict[str, Any]:
    """Build a run envelope shaped like the ADR-0058 contract."""
    return {
        "case_id": "case-1",
        "run_id": "run-1",
        "schema_version": SCHEMA_VERSION,
        "record_type": "run",
        "metrics": metrics,
    }


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the collector warnings captured during a test."""
    return [
        record
        for record in caplog.records
        if record.name == METRICS_LOGGER and record.levelno >= logging.WARNING
    ]


def test_empty_file_aggregates_to_zero_turns(tmp_path: Path) -> None:
    path = _write_lines(tmp_path / "metrics.jsonl", "")

    assert read_records(path) == ()

    summary = aggregate(read_records(path), run_id="run-1", case_id="case-1")

    assert summary.turn_count == 0
    assert summary.turns == ()
    assert summary.task_success is None
    assert summary.total_input_tokens == 0
    assert summary.total_output_tokens == 0
    assert summary.total_cached_tokens == 0
    assert summary.total_latency_ms is None
    assert summary.tool_error_count == 0
    assert summary.status == "completed"
    assert summary.envelope_present is False


def test_single_flat_turn_totals(tmp_path: Path) -> None:
    record = _turn(1, input_tokens=10, output_tokens=5, cached_tokens=2)
    record["latency_ms"] = 120.5
    path = _write_records(tmp_path / "metrics.jsonl", record)

    summary = load_run(path)

    assert summary.run_id == "run-1"
    assert summary.case_id == "case-1"
    assert summary.schema_version == SCHEMA_VERSION
    assert summary.turn_count == 1
    assert summary.total_input_tokens == 10
    assert summary.total_output_tokens == 5
    assert summary.total_cached_tokens == 2
    assert summary.total_latency_ms == pytest.approx(120.5)
    assert summary.envelope_present is False
    assert summary.turns[0].role == "chat"
    assert summary.turns[0].status == "completed"


def test_multiple_turns_are_summed(tmp_path: Path) -> None:
    path = _write_records(
        tmp_path / "metrics.jsonl",
        _turn(1, input_tokens=20, latency_ms=50.0),
        _turn(2, input_tokens=50, output_tokens=30, latency_ms=150.0),
        _turn(3, input_tokens=40, output_tokens=10, latency_ms=80.0),
    )

    summary = load_run(path)

    assert summary.turn_count == 3
    assert summary.total_input_tokens == 110
    assert summary.total_output_tokens == 40
    assert summary.total_latency_ms == pytest.approx(280.0)
    assert [turn.turn_number for turn in summary.turns] == [1, 2, 3]


def test_line_order_does_not_change_the_summary() -> None:
    records = [
        _turn(1, input_tokens=20, latency_ms=50.0),
        _turn(2, input_tokens=50, output_tokens=30, latency_ms=150.0),
        _turn(3, input_tokens=40, latency_ms=80.0),
    ]

    forward = aggregate(records)
    backward = aggregate(list(reversed(records)))

    assert forward == backward
    assert [turn.turn_number for turn in forward.turns] == [1, 2, 3]


def test_unparsable_line_is_skipped_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    good_one = json.dumps(_turn(1, input_tokens=10))
    good_two = json.dumps(_turn(2, input_tokens=20))
    path = _write_lines(tmp_path / "metrics.jsonl", good_one, "{not json", good_two)

    with caplog.at_level(logging.WARNING, logger=METRICS_LOGGER):
        records = read_records(path)

    assert len(records) == 2
    assert _warnings(caplog)

    summary = aggregate(records)
    assert summary.turn_count == 2
    assert summary.total_input_tokens == 30


def test_non_object_lines_are_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = _write_lines(
        tmp_path / "metrics.jsonl",
        json.dumps([1, 2, 3]),
        "42",
        json.dumps(_turn(1, input_tokens=7)),
    )

    with caplog.at_level(logging.WARNING, logger=METRICS_LOGGER):
        records = read_records(path)

    assert len(records) == 1
    assert _warnings(caplog)
    assert aggregate(records).total_input_tokens == 7


def test_truncated_final_line_is_skipped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    path = tmp_path / "metrics.jsonl"
    truncated = '{"turn_number": 2, "run_id"'
    path.write_text(
        json.dumps(_turn(1, input_tokens=4)) + "\n" + truncated,
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger=METRICS_LOGGER):
        records = read_records(path)

    assert len(records) == 1
    assert _warnings(caplog)


def test_run_envelope_values_are_read_when_totals_match(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    envelope = _envelope(
        token_usage=dict(MATCHING_TOTALS),
        task_success=True,
        cost_estimate=0.0015,
    )
    path = _write_records(
        tmp_path / "metrics.jsonl",
        _turn(1, input_tokens=20),
        _turn(2, input_tokens=50, output_tokens=30),
        envelope,
    )

    with caplog.at_level(logging.WARNING, logger=METRICS_LOGGER):
        summary = load_run(path)

    assert summary.envelope_present is True
    assert summary.task_success is True
    assert summary.cost_estimate == pytest.approx(0.0015)
    assert summary.total_input_tokens == 70
    assert summary.total_output_tokens == 30
    assert _warnings(caplog) == []


def test_envelope_mismatch_warns_and_recomputed_totals_win(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    envelope = _envelope(
        token_usage={
            "total_input_tokens": 999,
            "total_output_tokens": 999,
            "total_cached_tokens": 0,
        },
        task_success=True,
    )
    path = _write_records(
        tmp_path / "metrics.jsonl",
        _turn(1, input_tokens=20),
        _turn(2, input_tokens=50, output_tokens=30),
        envelope,
    )

    with caplog.at_level(logging.WARNING, logger=METRICS_LOGGER):
        summary = load_run(path)

    assert summary.total_input_tokens == 70
    assert summary.total_output_tokens == 30
    assert _warnings(caplog)


def test_unknown_schema_version_is_rejected(tmp_path: Path) -> None:
    envelope = _envelope(token_usage=dict(MATCHING_TOTALS))
    envelope["schema_version"] = "9.9"
    path = _write_records(tmp_path / "metrics.jsonl", envelope)

    with pytest.raises(MetricsSchemaError):
        load_run(path)


def test_failure_line_sets_the_run_status(tmp_path: Path) -> None:
    path = _write_records(
        tmp_path / "metrics.jsonl",
        {
            "case_id": "case-1",
            "run_id": "run-1",
            "status": "failed",
            "error": "RuntimeError: boom",
        },
    )

    summary = load_run(path)

    assert summary.status == "failed"
    assert summary.error is not None
    assert "boom" in summary.error
    assert summary.turns == ()
    assert summary.turn_count == 0


def test_nested_token_usage_is_extracted(tmp_path: Path) -> None:
    record = _turn(
        1,
        token_usage={
            "input_tokens": 20,
            "output_tokens": 10,
            "cached_tokens": 5,
            "model": "model-x",
        },
    )
    path = _write_records(tmp_path / "metrics.jsonl", record)

    summary = load_run(path)

    assert summary.total_input_tokens == 20
    assert summary.total_output_tokens == 10
    assert summary.total_cached_tokens == 5
    assert summary.turns[0].model == "model-x"


def test_all_passing_scores_mark_the_run_successful(tmp_path: Path) -> None:
    path = _write_records(
        tmp_path / "metrics.jsonl",
        _turn(1, score={"pass_fail": True}),
        _turn(2, score={"pass_fail": True}),
    )

    assert load_run(path).task_success is True


def test_one_failing_score_marks_the_run_unsuccessful(tmp_path: Path) -> None:
    path = _write_records(
        tmp_path / "metrics.jsonl",
        _turn(1, score={"pass_fail": True}),
        _turn(2, score={"pass_fail": False}),
    )

    assert load_run(path).task_success is False


def test_missing_scores_leave_task_success_unknown(tmp_path: Path) -> None:
    path = _write_records(tmp_path / "metrics.jsonl", _turn(1), _turn(2))

    assert load_run(path).task_success is None


def test_tool_errors_are_counted(tmp_path: Path) -> None:
    with_calls = _turn(
        1,
        tool_calls=[
            {"tool_name": "read_file", "error": None},
            {"tool_name": "write_file", "error": "boom"},
            {"tool_name": "run", "error": "nope"},
        ],
    )
    with_count = _turn(2, tool_error_count=1)
    path = _write_records(tmp_path / "metrics.jsonl", with_calls, with_count)

    summary = load_run(path)

    assert summary.tool_error_count == 3


def test_missing_metrics_file_raises_metrics_error(tmp_path: Path) -> None:
    missing = tmp_path / "absent.jsonl"

    with pytest.raises(MetricsError):
        read_records(missing)
    with pytest.raises(MetricsError):
        load_run(missing)


def test_read_records_rejects_a_non_path() -> None:
    with pytest.raises(MetricsError):
        read_records(cast(Any, 42))


def test_records_without_identifiers_are_rejected() -> None:
    with pytest.raises(MetricsError):
        aggregate([_turn(1, run_id=None, case_id=None)])
    with pytest.raises(MetricsError):
        aggregate([_turn(1, case_id=None)])


def test_explicit_identifiers_rescue_records_without_them() -> None:
    records = [_turn(1, run_id=None, case_id=None)]
    summary = aggregate(records, run_id="r", case_id="c")

    assert summary.run_id == "r"
    assert summary.case_id == "c"


def test_aggregate_rejects_non_iterable_input() -> None:
    with pytest.raises(MetricsError):
        aggregate(cast(Any, 42))
    with pytest.raises(MetricsError):
        aggregate(cast(Any, {"run_id": "run-1"}))


def test_aggregate_skips_non_mapping_rows(
    caplog: pytest.LogCaptureFixture,
) -> None:
    records = [_turn(1, input_tokens=5), cast(Any, ["not", "a", "mapping"])]

    with caplog.at_level(logging.WARNING, logger=METRICS_LOGGER):
        summary = aggregate(records)

    assert summary.turn_count == 1
    assert summary.total_input_tokens == 5
    assert _warnings(caplog)


def test_read_records_preserves_order_and_unknown_keys(tmp_path: Path) -> None:
    path = _write_records(
        tmp_path / "metrics.jsonl",
        _turn(2, custom="kept"),
        _turn(1),
        _turn(3),
    )

    records = read_records(path)

    assert [record["turn_number"] for record in records] == [2, 1, 3]
    assert records[0]["custom"] == "kept"


def test_load_run_accepts_a_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    _write_records(run_dir / "metrics.jsonl", _turn(1, input_tokens=3))

    summary = load_run(run_dir)

    assert summary.total_input_tokens == 3
    assert summary.run_id == "run-1"


def test_compare_runs_builds_one_row_per_run() -> None:
    first = aggregate([_turn(1, run_id="run-a", case_id="case-a")])
    second = aggregate([_turn(1, run_id="run-b", case_id="case-b")])

    rows = compare_runs(first, second)

    expected = {
        "run_id",
        "case_id",
        "status",
        "task_success",
        "turn_count",
        "total_input_tokens",
        "total_output_tokens",
        "total_cached_tokens",
        "cost_estimate",
        "resolved_models",
    }
    assert len(rows) == 2
    assert set(rows[0]) == expected
    assert "total_latency_ms" not in rows[0]
    assert [row["run_id"] for row in rows] == ["run-a", "run-b"]


def test_compare_runs_rejects_non_run_metrics() -> None:
    with pytest.raises(MetricsError):
        compare_runs(cast(Any, "not a run"))


def test_run_manager_shaped_turn_line_is_accepted(tmp_path: Path) -> None:
    record = {
        "case_id": "case-1",
        "run_id": "run-1",
        "user_id": "user-1",
        "chat_id": "chat-1",
        "turn_number": 1,
        "role": "chat",
        "status": "completed",
        "attempts": 1,
        "score": {"pass_fail": True},
    }
    path = _write_records(tmp_path / "metrics.jsonl", record)

    summary = load_run(path)

    assert summary.turn_count == 1
    turn = summary.turns[0]
    assert turn.role == "chat"
    assert turn.status == "completed"
    assert turn.attempts == 1
    assert turn.passed is True
    assert summary.task_success is True
    assert summary.envelope_present is False


def test_aggregate_lifts_resolved_models_from_the_run_envelope() -> None:
    """A run envelope carrying resolved_models is lifted onto the summary."""
    models = {"chat": "model-a", "judge": "model-b"}
    records = [
        {
            "run_id": "run-1",
            "case_id": "case-1",
            "schema_version": "1.0",
            "record_type": "run",
            "status": "completed",
            "metrics": {"task_success": True},
            "resolved_models": models,
        }
    ]

    summary = aggregate(records)

    assert summary.resolved_models == models


def test_aggregate_defaults_resolved_models_to_an_empty_mapping() -> None:
    """A legacy record without resolved_models aggregates to an empty map."""
    records = [
        {
            "run_id": "run-1",
            "case_id": "case-1",
            "turn_number": 1,
            "role": "chat",
            "status": "completed",
        }
    ]

    summary = aggregate(records)

    assert summary.resolved_models == {}


def test_compare_runs_carries_resolved_models() -> None:
    """The compare row reports the resolved model map of each run."""
    models = {"chat": "model-a"}
    with_models = aggregate(
        [
            {
                "run_id": "run-a",
                "case_id": "case-a",
                "schema_version": "1.0",
                "record_type": "run",
                "status": "completed",
                "metrics": {"task_success": True},
                "resolved_models": models,
            }
        ]
    )
    without_models = aggregate(
        [
            {
                "run_id": "run-b",
                "case_id": "case-b",
                "turn_number": 1,
                "role": "chat",
                "status": "completed",
            }
        ]
    )

    rows = compare_runs(with_models, without_models)

    assert rows[0]["resolved_models"] == models
    assert rows[1]["resolved_models"] == {}
