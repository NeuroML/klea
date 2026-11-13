# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the in-process scheduler (P9, ADR-0060 Schedule stage).

The tests write small JSONL suites under ``tmp_path`` and drive them through
:func:`klea_eval.scheduler.run_suites` with an injected fake turn executor, so
they need no network, server, LLM or API key: a suite is a file of case
documents, and a "run" is whatever the pipeline stores through the ADR-0059
writer.

Covered here: suite discovery (single file, sorted directory, missing path),
unattended single- and multi-suite runs and their ordering, per-suite failure
isolation, the per-suite aggregate shape, the skip of an already complete run on
a second pass, the ADR-0060 exit codes 0/1/2/3, the crash-recovery report of a
leftover ``.tmp`` directory and the refinement of the checked-in sample suites.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from klea_eval import cases, pipeline, run_manager, scheduler, workspace
from klea_eval.run_manager import RunContext

pytestmark = pytest.mark.eval

#: Directory of the sample suites shipped with the package (D7).
SAMPLES_DIR = Path(__file__).resolve().parents[1] / "samples"

#: Suite fixtures only exist once the sample suites land in their own change.
requires_samples = pytest.mark.skipif(
    not SAMPLES_DIR.is_dir(),
    reason="the sample suites are added in a later atomic change",
)


def _turn(turn_number: int = 1, role: str = "user", text: str = "hello") -> dict:
    """Build one ADR-0056 turn document."""
    return {"turn_number": turn_number, "role": role, "content": text}


def _case(case_id: str, turns: list[dict] | None = None) -> dict:
    """Build one minimal ADR-0056 case document."""
    return {
        "schema_version": "1.0",
        "case_id": case_id,
        "turns": turns if turns is not None else [_turn()],
    }


def _write_suite(path: Path, documents: list[dict]) -> Path:
    """Write ``documents`` as a JSONL suite and return its path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(document, sort_keys=True) for document in documents]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _passing_executor(passed: bool = True):
    """Return a fake turn executor that reports a fixed task outcome."""

    def execute(context: RunContext, turn: Any) -> dict[str, Any]:
        return {
            "status": run_manager.STATUS_COMPLETED,
            "task_success": passed,
            "passed": passed,
            "score": {
                "task_success": passed,
                "passed": passed,
                "score": 1.0 if passed else 0.0,
            },
        }

    return execute


def _failing_executor(context: RunContext, turn: Any) -> dict[str, Any]:
    """Fake turn executor that always raises, so its case fails its rubrics."""
    raise RuntimeError("boom")


def test_scheduler_error_hierarchy() -> None:
    assert issubclass(scheduler.SchedulerConfigError, scheduler.SchedulerError)
    assert issubclass(scheduler.SchedulerError, Exception)


def test_default_max_workers_is_sequential() -> None:
    assert scheduler.DEFAULT_MAX_WORKERS == 1


def test_discover_suites_returns_a_single_file(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-1")])

    discovered = scheduler.discover_suites(suite)

    assert discovered == (suite,)


def test_discover_suites_sorts_a_directory(tmp_path: Path) -> None:
    second = _write_suite(tmp_path / "b_suite.jsonl", [_case("case-b")])
    first = _write_suite(tmp_path / "a_suite.jsonl", [_case("case-a")])
    _write_suite(tmp_path / "ignored.txt", [_case("case-c")])

    discovered = scheduler.discover_suites(tmp_path)

    assert discovered == (first, second)


def test_discover_suites_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.discover_suites(tmp_path / "absent")


def test_discover_suites_empty_directory_raises(tmp_path: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()

    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.discover_suites(empty)


def test_discover_suites_rejects_a_non_path() -> None:
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.discover_suites(1)  # ty: ignore[invalid-argument-type]


def test_run_suites_returns_one_suite_run_per_suite(tmp_path: Path) -> None:
    first = _write_suite(tmp_path / "one.jsonl", [_case("case-a1"), _case("case-a2")])
    second = _write_suite(tmp_path / "two.jsonl", [_case("case-b1")])
    root = tmp_path / "workspace"

    report = scheduler.run_suites(
        [first, second],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    assert [suite.suite_path for suite in report.suites] == [first, second]
    assert report.exit_code == scheduler.EXIT_OK == 0
    assert report.ok is True
    assert all(suite.result is not None for suite in report.suites)
    assert [suite.completed for suite in report.suites] == [2, 1]
    assert [suite.failed for suite in report.suites] == [0, 0]
    assert report.workspace_root == root


def test_run_suites_aggregates_only_its_own_cases(tmp_path: Path) -> None:
    first = _write_suite(tmp_path / "one.jsonl", [_case("case-a1"), _case("case-a2")])
    second = _write_suite(tmp_path / "two.jsonl", [_case("case-b1")])
    root = tmp_path / "workspace"

    report = scheduler.run_suites(
        [first, second],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    first_aggregate = report.suites[0].aggregate
    second_aggregate = report.suites[1].aggregate

    assert first_aggregate is not None
    assert second_aggregate is not None
    assert first_aggregate.case_count == 2
    assert first_aggregate.pass_count == 2
    assert {case.case_id for case in first_aggregate.cases} == {"case-a1", "case-a2"}
    assert second_aggregate.case_count == 1
    assert [case.case_id for case in second_aggregate.cases] == ["case-b1"]
    assert first_aggregate.pass_at_k == 1.0
    assert second_aggregate.suite_score == 1.0


def test_run_suites_isolates_a_broken_suite(tmp_path: Path) -> None:
    broken = tmp_path / "broken.jsonl"
    broken.write_text("not json\n", encoding="utf-8")
    healthy = _write_suite(tmp_path / "healthy.jsonl", [_case("case-ok")])
    root = tmp_path / "workspace"

    report = scheduler.run_suites(
        [broken, healthy],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    assert report.suites[0].suite_path == broken
    assert report.suites[0].error is not None
    assert report.suites[0].result is None
    assert report.suites[0].completed == 0
    assert report.suites[0].exit_code == scheduler.EXIT_CONFIGURATION_ERROR

    assert report.suites[1].error is None
    assert report.suites[1].completed == 1

    assert report.exit_code == scheduler.EXIT_CONFIGURATION_ERROR == 2
    healthy_result = report.suites[1].result
    assert healthy_result is not None
    stored_run_id = healthy_result.runs[0].run_id
    assert stored_run_id is not None
    assert workspace.run_dir(stored_run_id, root).is_dir()


def test_run_suites_reports_a_failing_case(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "fails.jsonl", [_case("case-fail")])

    report = scheduler.run_suites(
        [suite],
        workspace_root=tmp_path / "workspace",
        execute_turn=_failing_executor,
        harness_seed=0,
        max_attempts=1,
        sleep=lambda seconds: None,
    )

    assert report.suites[0].result is not None
    assert report.suites[0].failed == 1
    assert report.suites[0].completed == 0
    assert report.exit_code == scheduler.EXIT_CASE_FAILURE == 1
    assert report.ok is False


def test_run_suites_skips_completed_runs_on_a_second_pass(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-a1"), _case("case-a2")])
    root = tmp_path / "workspace"

    first = scheduler.run_suites(
        [suite],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )
    second = scheduler.run_suites(
        [suite],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    assert first.suites[0].completed == 2
    assert second.suites[0].completed == 0
    assert second.suites[0].skipped == 2
    assert second.suites[0].failed == 0
    assert second.exit_code == scheduler.EXIT_OK


def test_run_suites_without_an_executor_reports_a_configuration_error(
    tmp_path: Path,
) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-a1")])

    report = scheduler.run_suites([suite], workspace_root=tmp_path / "workspace")

    assert report.suites[0].error is not None
    assert report.suites[0].result is None
    assert report.exit_code == scheduler.EXIT_CONFIGURATION_ERROR


def test_run_suites_rejects_a_bare_string(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-a1")])

    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites(str(suite), execute_turn=_passing_executor())


def test_run_suites_rejects_an_empty_sequence() -> None:
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([], execute_turn=_passing_executor())


def test_run_suites_rejects_a_non_path_entry() -> None:
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([1], execute_turn=_passing_executor())  # ty: ignore[invalid-argument-type]


def test_run_suites_rejects_bad_arguments(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-a1")])
    executor = _passing_executor()

    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([suite], execute_turn=executor, max_attempts=0)
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([suite], execute_turn=executor, max_attempts=True)
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([suite], execute_turn=executor, k=0)
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([suite], execute_turn=executor, k=True)
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([suite], execute_turn=executor, overwrite="yes")  # ty: ignore[invalid-argument-type]
    with pytest.raises(scheduler.SchedulerConfigError):
        scheduler.run_suites([suite], execute_turn=executor, sleep="no")  # ty: ignore[invalid-argument-type]


def test_find_incomplete_runs_without_a_runs_directory(tmp_path: Path) -> None:
    assert scheduler.find_incomplete_runs(tmp_path / "empty") == ()


def test_find_incomplete_runs_reports_a_leftover_tmp(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    leftover = workspace.run_dir_tmp("run-stuck", root)
    leftover.mkdir(parents=True)
    complete = _write_suite(tmp_path / "one.jsonl", [_case("case-a1")])
    scheduler.run_suites(
        [complete],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    incomplete = scheduler.find_incomplete_runs(root)

    assert incomplete == ("run-stuck",)
    assert leftover.is_dir()


def test_run_suites_reports_incomplete_runs_with_exit_code_3(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-a1")])
    root = tmp_path / "workspace"
    scheduler.run_suites(
        [suite],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )
    workspace.run_dir_tmp("run-stuck", root).mkdir(parents=True)

    report = scheduler.run_suites(
        [suite],
        workspace_root=root,
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    assert report.incomplete_runs == ("run-stuck",)
    assert report.exit_code == scheduler.EXIT_INCOMPLETE_RUNS == 3


def test_schedule_report_exit_code_prefers_a_broken_suite(tmp_path: Path) -> None:
    broken = scheduler.SuiteRun(suite_path=tmp_path / "a.jsonl", error="boom")
    report = scheduler.ScheduleReport(
        workspace_root=tmp_path,
        suites=(broken,),
        incomplete_runs=("run-stuck",),
    )

    assert broken.exit_code == scheduler.EXIT_CONFIGURATION_ERROR
    assert report.exit_code == scheduler.EXIT_CONFIGURATION_ERROR
    assert report.ok is False


def test_schedule_report_exit_code_for_a_failed_case(tmp_path: Path) -> None:
    case_run = pipeline.CaseRun(case_id="case-a", status=run_manager.STATUS_FAILED)
    result = pipeline.SuiteResult(
        suite_path=tmp_path / "a.jsonl",
        workspace_root=tmp_path,
        runs=(case_run,),
    )
    suite_run = scheduler.SuiteRun(suite_path=tmp_path / "a.jsonl", result=result)
    report = scheduler.ScheduleReport(workspace_root=tmp_path, suites=(suite_run,))

    assert suite_run.failed == 1
    assert report.exit_code == scheduler.EXIT_CASE_FAILURE


def test_schedule_report_to_dict_is_json_ready(tmp_path: Path) -> None:
    suite = _write_suite(tmp_path / "one.jsonl", [_case("case-a1")])
    report = scheduler.run_suites(
        [suite],
        workspace_root=tmp_path / "workspace",
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    payload = report.to_dict()
    serialised = json.dumps(payload)

    assert payload["exit_code"] == 0
    assert payload["incomplete_runs"] == []
    assert len(payload["suites"]) == 1
    assert payload["suites"][0]["completed"] == 1
    assert payload["suites"][0]["aggregate"] is not None
    assert json.loads(serialised)["workspace_root"] == str(report.workspace_root)


@requires_samples
def test_sample_suites_load_and_are_ascii() -> None:
    discovered = scheduler.discover_suites(SAMPLES_DIR)

    assert len(discovered) >= 2
    for suite_path in discovered:
        text = suite_path.read_text(encoding="utf-8")
        assert text.isascii()
        loaded = cases.load_suite(suite_path)
        assert loaded.cases


@requires_samples
def test_run_suites_runs_the_sample_suites_unattended(tmp_path: Path) -> None:
    discovered = scheduler.discover_suites(SAMPLES_DIR)

    report = scheduler.run_suites(
        discovered,
        workspace_root=tmp_path / "workspace",
        execute_turn=_passing_executor(),
        harness_seed=0,
    )

    assert len(report.suites) == len(discovered)
    assert report.exit_code == scheduler.EXIT_OK
    for suite in report.suites:
        assert suite.error is None
        assert suite.result is not None
        assert suite.aggregate is not None
        assert suite.completed >= 1
