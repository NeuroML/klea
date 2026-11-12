"""Offline tests for the evaluation pipeline and the ``run`` command.

Nothing in this module reaches the network or a live model: suites are written
to ``tmp_path`` and the model-facing collaborators are replaced by fake turn
executors that return plain mappings.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from klea_eval import metrics, pipeline, results, workspace
from klea_eval.errors import CaseValidationError
from klea_eval.pipeline import PipelineConfigError, PipelineError, run_suite
from klea_eval.results import load_stored_run
from klea_eval.run_config import ModelOverrideError
from klea_eval.ui.cli import app
from typer.testing import CliRunner

runner = CliRunner()


# ---------------------------------------------------------------------------
# Suite fixtures
# ---------------------------------------------------------------------------
def _turn(turn_number: int, role: str, content: str) -> dict[str, Any]:
    """Build a minimal valid turn payload."""
    return {"turn_number": turn_number, "role": role, "content": content}


def _case(
    case_id: str, turns: list[dict[str, Any]], **overrides: Any
) -> dict[str, Any]:
    """Build a minimal valid case payload, overridable per test."""
    payload: dict[str, Any] = {
        "case_id": case_id,
        "schema_version": "1.0",
        "description": "pipeline fixture",
        "turns": turns,
    }
    payload.update(overrides)
    return payload


def _write_suite(path: Path, cases: list[dict[str, Any]]) -> Path:
    """Serialise ``cases`` as JSONL to ``path`` and return it."""
    lines = [json.dumps(case) for case in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


class Recorder:
    """Synchronous turn executor that records the case of every turn."""

    def __init__(self, error: BaseException | None = None) -> None:
        self.calls: list[str | None] = []
        self.error = error

    def __call__(self, context: Any, turn: Any) -> dict[str, Any]:
        self.calls.append(getattr(context, "case_id", None))
        if self.error is not None:
            raise self.error
        return {"content": f"reply to turn {turn.turn_number}", "latency_ms": 1.0}


# ---------------------------------------------------------------------------
# Run helpers
# ---------------------------------------------------------------------------
def _run(
    tmp_path: Path,
    cases: list[dict[str, Any]],
    *,
    execute_turn: Any = None,
    overwrite: bool = False,
) -> tuple[Path, Any]:
    """Run ``cases`` through the pipeline and return the suite path and result."""
    suite = _write_suite(tmp_path / "suite.jsonl", cases)
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    result = run_suite(
        str(suite),
        workspace_root=str(workspace),
        execute_turn=execute_turn if execute_turn is not None else Recorder(),
        harness_seed=1234,
        max_attempts=1,
        overwrite=overwrite,
        sleep=lambda _delay: None,
    )
    return suite, result


def _run_json_paths(root: Path) -> list[Path]:
    return sorted(root.rglob("run.json"))


def _single_payload(root: Path) -> dict[str, Any]:
    paths = _run_json_paths(root)
    assert len(paths) == 1, paths
    return json.loads(paths[0].read_text(encoding="utf-8"))


def _index_lines(root: Path) -> list[str]:
    path = results.index_path(root)
    if not path.is_file():
        return []
    return [
        line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def _index_case_ids(root: Path) -> list[str]:
    return [json.loads(line)["case_id"] for line in _index_lines(root)]


def _three_cases() -> list[dict[str, Any]]:
    return [
        _case(f"case-{number}", [_turn(1, "user", f"turn {number}")])
        for number in (1, 2, 3)
    ]


def test_two_model_sets_produce_two_distinct_runs(tmp_path: Path) -> None:
    """Two ``-m`` model sets for one case and seed are two distinct runs."""
    suite = _write_suite(
        tmp_path / "suite.jsonl", [_case("case-1", [_turn(1, "user", "hi")])]
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)

    def applier(
        _existing: Any,
        base_url: str,
        user_id: str,
        chat_id: str,
        overrides: Mapping[str, str],
    ) -> dict[str, str]:
        return dict(overrides)

    def once(model: str) -> Any:
        return run_suite(
            str(suite),
            workspace_root=str(workspace),
            execute_turn=Recorder(),
            harness_seed=1234,
            max_attempts=1,
            model_overrides={"planner": model},
            apply_models=applier,
            base_url="http://backend.test",
            sleep=lambda _delay: None,
        )

    first = once("model-a")
    second = once("model-b")

    assert first.runs[0].status == "completed"
    assert second.runs[0].status == "completed"
    assert first.runs[0].run_id != second.runs[0].run_id
    assert first.runs[0].output_dir != second.runs[0].output_dir
    assert len(_index_lines(workspace)) == 2


# ---------------------------------------------------------------------------
# Pipeline: single case
# ---------------------------------------------------------------------------
def test_single_case_suite_writes_complete_run(tmp_path: Path) -> None:
    """One case produces one stored run and one index line."""
    _suite, result = _run(
        tmp_path,
        [_case("case-1", [_turn(1, "user", "Hello"), _turn(2, "chat", "Hi")])],
    )

    assert result.exit_code == 0
    assert [case_run.case_id for case_run in result.runs] == ["case-1"]
    assert result.runs[0].status == "completed"

    payload = _single_payload(result.workspace_root)
    assert payload["case_id"] == "case-1"
    assert payload["status"] == "completed"
    assert payload["run_id"] == result.runs[0].run_id

    stored = load_stored_run(Path(result.runs[0].output_dir))
    assert stored.metadata.case_id == "case-1"

    assert len(_index_lines(result.workspace_root)) == 1


# ---------------------------------------------------------------------------
# Pipeline: multiple cases
# ---------------------------------------------------------------------------
def test_multi_case_suite_stores_runs_in_suite_order(tmp_path: Path) -> None:
    """Runs are reported and indexed in the order of the suite."""
    _suite, result = _run(tmp_path, _three_cases())

    assert result.exit_code == 0
    assert [case_run.case_id for case_run in result.runs] == [
        "case-1",
        "case-2",
        "case-3",
    ]
    assert _index_case_ids(result.workspace_root) == ["case-1", "case-2", "case-3"]
    assert len(_run_json_paths(result.workspace_root)) == 3


# ---------------------------------------------------------------------------
# Pipeline: resume and overwrite
# ---------------------------------------------------------------------------
def test_complete_run_is_skipped_without_duplicate_index_line(
    tmp_path: Path,
) -> None:
    """Re-running a finished workspace neither reruns nor re-indexes the case."""
    cases = [_case("case-1", [_turn(1, "user", "Hello")])]
    executor = Recorder()

    _suite, first = _run(tmp_path, cases, execute_turn=executor)
    assert first.exit_code == 0
    assert len(_index_lines(first.workspace_root)) == 1
    calls_after_first = list(executor.calls)

    _suite, second = _run(tmp_path, cases, execute_turn=executor)
    assert second.exit_code == 0
    assert second.runs[0].status == "skipped"
    assert len(_index_lines(second.workspace_root)) == 1
    assert executor.calls == calls_after_first


def test_overwrite_reruns_and_appends_index_line(tmp_path: Path) -> None:
    """``overwrite`` reruns a finished case and appends a fresh index line."""
    cases = [_case("case-1", [_turn(1, "user", "Hello")])]
    executor = Recorder()

    _suite, first = _run(tmp_path, cases, execute_turn=executor)
    assert len(_index_lines(first.workspace_root)) == 1

    _suite, result = _run(tmp_path, cases, execute_turn=executor, overwrite=True)
    assert result.exit_code == 0
    assert result.runs[0].status == "completed"
    assert len(_index_lines(result.workspace_root)) == 2
    assert len(executor.calls) == 2


# ---------------------------------------------------------------------------
# Pipeline: rejected suites and configuration faults
# ---------------------------------------------------------------------------
def test_failing_executor_marks_the_case_failed(tmp_path: Path) -> None:
    """A turn that keeps failing fails its case and stores nothing."""
    _suite, result = _run(
        tmp_path,
        [_case("case-1", [_turn(1, "user", "Hello")])],
        execute_turn=Recorder(error=RuntimeError("boom")),
    )

    assert result.runs[0].status == "failed"
    assert result.exit_code == 1
    assert _run_json_paths(result.workspace_root) == []
    assert _index_lines(result.workspace_root) == []


def test_invalid_suite_is_rejected(tmp_path: Path) -> None:
    """An invalid turn role aborts the suite before anything is stored."""
    suite = _write_suite(
        tmp_path / "invalid.jsonl",
        [_case("case-1", [_turn(1, "assistant", "Hi")])],
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(CaseValidationError):
        run_suite(
            str(suite),
            workspace_root=str(workspace),
            execute_turn=Recorder(),
            max_attempts=1,
            sleep=lambda _delay: None,
        )

    assert _run_json_paths(workspace) == []


def test_missing_executor_and_base_url_raises(tmp_path: Path) -> None:
    """With neither an executor nor a base URL the pipeline refuses to start."""
    suite = _write_suite(
        tmp_path / "config.jsonl", [_case("case-1", [_turn(1, "user", "Hello")])]
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    with pytest.raises(PipelineError):
        run_suite(str(suite), workspace_root=str(workspace))

    assert _run_json_paths(workspace) == []


# ---------------------------------------------------------------------------
# Pipeline: stored run payload
# ---------------------------------------------------------------------------
def test_run_json_carries_run_metadata(tmp_path: Path) -> None:
    """run.json records the identity, status and seed of the run."""
    _suite, result = _run(tmp_path, [_case("case-1", [_turn(1, "user", "Hello")])])
    payload = _single_payload(result.workspace_root)

    for key in ("run_id", "case_id", "status", "harness_seed", "models"):
        assert key in payload, key
    assert payload["case_id"] == "case-1"
    assert payload["status"] == "completed"
    assert str(payload["harness_seed"]) == "1234"

    for path in result.workspace_root.rglob("*"):
        if path.is_file():
            assert "pass_at_k" not in path.read_text(encoding="utf-8"), path


def test_run_json_is_written_before_the_complete_marker(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """``run.json`` is in the staging directory before the run is published."""
    cases = [_case("case-1", [_turn(1, "user", "Hello")])]
    seen: dict[str, bool] = {}

    real_publish = workspace.publish_run

    def checking_publish(run_id: str, *args: Any, **kwargs: Any) -> Path:
        root = args[0] if args else kwargs.get("root")
        staged = workspace.run_dir_tmp(run_id, root)
        seen["exists_at_publish"] = results.run_metadata_path(staged).is_file()
        return real_publish(run_id, *args, **kwargs)

    monkeypatch.setattr(workspace, "publish_run", checking_publish)

    _suite, result = _run(tmp_path, cases, execute_turn=Recorder())

    assert seen.get("exists_at_publish") is True
    output_dir = Path(result.runs[0].output_dir)
    assert results.run_metadata_path(output_dir).is_file()
    assert load_stored_run(output_dir).metadata.run_id == result.runs[0].run_id
    assert len(_index_lines(result.workspace_root)) == 1


def test_complete_run_without_an_index_line_is_reindexed(tmp_path: Path) -> None:
    """A stored run whose index line was lost is re-indexed, never re-run."""
    cases = [_case("case-1", [_turn(1, "user", "Hello")])]
    executor = Recorder()
    _suite, first = _run(tmp_path, cases, execute_turn=executor)
    assert first.exit_code == 0
    assert len(_index_lines(first.workspace_root)) == 1
    results.index_path(first.workspace_root).unlink()
    calls_before = list(executor.calls)

    _suite, second = _run(tmp_path, cases, execute_turn=executor)

    assert executor.calls == calls_before
    assert second.runs[0].status == "completed"
    assert second.exit_code == 0
    index_ids = [entry.run_id for entry in results.read_index(second.workspace_root)]
    assert index_ids == [first.runs[0].run_id]


def test_complete_run_that_is_indexed_is_left_untouched(tmp_path: Path) -> None:
    """An indexed complete run is skipped and gains no duplicate index line."""
    cases = [_case("case-1", [_turn(1, "user", "Hello")])]
    executor = Recorder()
    _suite, first = _run(tmp_path, cases, execute_turn=executor)
    assert len(_index_lines(first.workspace_root)) == 1
    calls_before = list(executor.calls)

    _suite, second = _run(tmp_path, cases, execute_turn=executor)

    assert executor.calls == calls_before
    assert second.runs[0].status == "skipped"
    assert len(_index_lines(second.workspace_root)) == 1


# ---------------------------------------------------------------------------
# CLI: stubbed pipeline
# ---------------------------------------------------------------------------
class StubCase:
    """Just enough of a case run for the CLI to render."""

    def __init__(self, case_id: str, passed: bool) -> None:
        self.case_id = case_id
        self.metrics = SimpleNamespace(task_success=passed)


class StubResult:
    """Just enough of a suite result for the CLI to render."""

    def __init__(self, cases: list[StubCase]) -> None:
        self.runs = tuple(cases)

    @property
    def exit_code(self) -> int:
        return 0 if all(case.metrics.task_success for case in self.runs) else 1


def _stub_pipeline(monkeypatch: Any, cases: list[StubCase]) -> dict[str, Any]:
    """Replace ``klea_eval.pipeline.run_suite`` with a recording stub."""
    recorded: dict[str, Any] = {}

    def fake_run_suite(suite: str, **kwargs: Any) -> StubResult:
        recorded["suite"] = suite
        recorded["kwargs"] = kwargs
        return StubResult(cases)

    monkeypatch.setattr(pipeline, "run_suite", fake_run_suite)
    return recorded


def _cli(arguments: list[str]) -> Any:
    return runner.invoke(app, arguments)


def _combined(result: Any) -> str:
    """Return stdout and stderr together, whatever click version is installed."""
    text = result.output or ""
    stderr = getattr(result, "stderr", "") or ""
    return text + stderr


def _valid_suite(tmp_path: Path) -> Path:
    return _write_suite(
        tmp_path / "valid.jsonl", [_case("case-1", [_turn(1, "user", "Hello")])]
    )


def test_cli_run_reports_passing_cases(tmp_path: Path, monkeypatch: Any) -> None:
    """The happy path exits 0 and prints one line per case."""
    recorded = _stub_pipeline(monkeypatch, [StubCase("case-1", True)])
    suite = _valid_suite(tmp_path)
    workspace = tmp_path / "ws"

    result = _cli(
        [
            "run",
            str(suite),
            "--base-url",
            "http://example.test",
            "--workspace",
            str(workspace),
        ]
    )

    assert result.exit_code == 0, _combined(result)
    assert "case-1: pass" in _combined(result)
    assert recorded["suite"] == str(suite)
    assert recorded["kwargs"]["base_url"] == "http://example.test"
    assert recorded["kwargs"]["workspace_root"] == str(workspace)


def test_cli_run_exits_one_when_a_case_fails(tmp_path: Path, monkeypatch: Any) -> None:
    """A failing case makes the command exit 1."""
    _stub_pipeline(monkeypatch, [StubCase("case-1", True), StubCase("case-2", False)])

    result = _cli(
        ["run", str(_valid_suite(tmp_path)), "--base-url", "http://example.test"]
    )

    assert result.exit_code == 1, _combined(result)
    assert "case-2: fail" in _combined(result)


def test_cli_run_exits_two_on_invalid_suite(tmp_path: Path, monkeypatch: Any) -> None:
    """A rejected suite exits 2 with the ``path:line: message`` diagnostic."""
    suite = _valid_suite(tmp_path)

    def fake_run_suite(_suite: str, **_kwargs: Any) -> StubResult:
        raise CaseValidationError(
            "missing required field 'turns'", source=suite, line_number=2
        )

    monkeypatch.setattr(pipeline, "run_suite", fake_run_suite)

    result = _cli(["run", str(suite), "--base-url", "http://example.test"])
    combined = _combined(result)

    assert result.exit_code == 2, combined
    assert f"{suite}:2: missing required field 'turns'" in combined


def test_cli_run_requires_base_url(tmp_path: Path, monkeypatch: Any) -> None:
    """Without a configured base URL the command exits 2 and never runs."""
    monkeypatch.delenv("KLEA_EVAL_BASE_URL", raising=False)
    _stub_pipeline(monkeypatch, [StubCase("case-1", True)])

    result = _cli(["run", str(_valid_suite(tmp_path))])

    assert result.exit_code == 2, _combined(result)
    assert "base URL" in _combined(result)


def test_cli_run_forwards_options(tmp_path: Path, monkeypatch: Any) -> None:
    """Every pipeline option is forwarded unchanged to ``run_suite``."""
    recorded = _stub_pipeline(monkeypatch, [StubCase("case-1", True)])

    result = _cli(
        [
            "run",
            str(_valid_suite(tmp_path)),
            "-u",
            "http://example.test",
            "-w",
            str(tmp_path / "ws"),
            "--endpoint",
            "/custom/chat",
            "--timeout",
            "12.5",
            "-s",
            "99",
            "--max-attempts",
            "4",
            "--overwrite",
        ]
    )

    assert result.exit_code == 0, _combined(result)
    forwarded = recorded["kwargs"]
    assert forwarded["base_url"] == "http://example.test"
    assert forwarded["endpoint"] == "/custom/chat"
    assert forwarded["timeout"] == 12.5
    assert forwarded["harness_seed"] == 99
    assert forwarded["max_attempts"] == 4
    assert forwarded["overwrite"] is True


# ---------------------------------------------------------------------------
# Pipeline: model overrides
# ---------------------------------------------------------------------------
class ModelApplier:
    """Fake ``apply_run_models``: records each call and its inputs."""

    def __init__(
        self,
        events: list[str] | None = None,
        resolved: dict[str, str] | None = None,
        error: BaseException | None = None,
        probe: Callable[[], None] | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self.events: list[str] = events if events is not None else []
        self.resolved: dict[str, str] = resolved if resolved is not None else {}
        self.error = error
        self.probe = probe

    def __call__(
        self,
        client: Any,
        base_url: str,
        user_id: str,
        chat_id: str,
        models: Mapping[str, str],
    ) -> dict[str, str]:
        self.events.append("apply")
        self.calls.append(
            {
                "client": client,
                "base_url": base_url,
                "user_id": user_id,
                "chat_id": chat_id,
                "models": dict(models),
            }
        )
        if self.probe is not None:
            self.probe()
        if self.error is not None:
            raise self.error
        return dict(self.resolved)


def _run_with_applier(
    tmp_path: Path,
    cases: list[dict[str, Any]],
    applier: ModelApplier,
    *,
    model_overrides: Mapping[str, str] | None = None,
    base_url: str | None = "http://example.test",
    execute_turn: Any = None,
) -> Any:
    """Run ``cases`` with a fake model applier instead of a live backend."""
    suite = _write_suite(tmp_path / "suite.jsonl", cases)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(exist_ok=True)
    return run_suite(
        str(suite),
        workspace_root=str(workspace_root),
        base_url=base_url,
        execute_turn=execute_turn if execute_turn is not None else Recorder(),
        harness_seed=1234,
        max_attempts=1,
        sleep=lambda _delay: None,
        model_overrides=model_overrides,
        apply_models=applier,
    )


def test_case_model_overrides_win_over_the_cli_defaults(tmp_path: Path) -> None:
    """The case's own role value wins over the same key of the CLI defaults."""
    applier = ModelApplier()
    result = _run_with_applier(
        tmp_path,
        [
            _case(
                "case-1",
                [_turn(1, "user", "Hello")],
                run_config={"model_overrides": {"chat": "case-model"}},
            )
        ],
        applier,
        model_overrides={"chat": "cli-model", "judge": "cli-judge"},
    )

    assert result.exit_code == 0
    assert len(applier.calls) == 1
    call = applier.calls[0]
    assert call["models"] == {"chat": "case-model", "judge": "cli-judge"}
    assert call["client"] is None
    assert call["base_url"] == "http://example.test"
    assert call["user_id"] and call["chat_id"]


def test_the_applier_runs_after_preparation_and_before_the_first_turn(
    tmp_path: Path,
) -> None:
    """The applier sees a prepared run and runs before any turn executes."""
    events: list[str] = []
    prepared: list[Path] = []

    def probe() -> None:
        root = tmp_path / "workspace"
        prepared.extend(path for path in root.rglob("*") if path.is_dir())

    applier = ModelApplier(events=events, probe=probe)

    def execute_turn(_context: Any, turn: Any) -> dict[str, Any]:
        events.append("turn")
        return {"content": f"reply to turn {turn.turn_number}", "latency_ms": 1.0}

    result = _run_with_applier(
        tmp_path,
        [_case("case-1", [_turn(1, "user", "Hello")])],
        applier,
        model_overrides={"chat": "model-a"},
        execute_turn=execute_turn,
    )

    assert result.exit_code == 0
    assert len(applier.calls) == 1
    assert events == ["apply", "turn"]
    assert prepared


def test_resolved_models_are_persisted_in_run_json_and_metrics(
    tmp_path: Path,
) -> None:
    """The resolved map lands in run.json and in the run's own metrics."""
    models = {"chat": "model-a", "judge": "model-b"}
    applier = ModelApplier(resolved=models)
    result = _run_with_applier(
        tmp_path,
        [_case("case-1", [_turn(1, "user", "Hello")])],
        applier,
        model_overrides={"chat": "model-a", "judge": "model-b"},
    )

    output_dir = Path(result.runs[0].output_dir)
    stored = load_stored_run(output_dir)
    summary = metrics.load_run(output_dir)

    assert stored.metadata.resolved_models == models
    assert summary.resolved_models == models


def test_a_model_override_failure_fails_the_case_without_a_turn(
    tmp_path: Path,
) -> None:
    """An applier failure fails the case, runs no turn and publishes nothing."""
    applier = ModelApplier(error=ModelOverrideError("unknown role 'chat'"))
    executor = Recorder()
    result = _run_with_applier(
        tmp_path,
        [_case("case-1", [_turn(1, "user", "Hello")])],
        applier,
        model_overrides={"chat": "model-a"},
        execute_turn=executor,
    )

    assert executor.calls == []
    assert result.runs[0].status == "failed"
    assert "ModelOverrideError" in (result.runs[0].error or "")
    assert result.exit_code == 1
    assert _run_json_paths(result.workspace_root) == []
    assert _index_lines(result.workspace_root) == []


def test_without_overrides_the_applier_is_never_called(tmp_path: Path) -> None:
    """No merged override skips the applier and needs no base URL at all."""
    applier = ModelApplier()
    result = _run_with_applier(
        tmp_path,
        [_case("case-1", [_turn(1, "user", "Hello")])],
        applier,
        base_url=None,
    )

    assert result.exit_code == 0
    assert applier.calls == []


def test_overrides_without_a_base_url_are_a_configuration_error(
    tmp_path: Path,
) -> None:
    """A non-empty override map without a base URL refuses to run the suite."""
    applier = ModelApplier()

    with pytest.raises(PipelineConfigError):
        _run_with_applier(
            tmp_path,
            [_case("case-1", [_turn(1, "user", "Hello")])],
            applier,
            model_overrides={"chat": "model-a"},
            base_url=None,
        )

    second = tmp_path / "second"
    second.mkdir()
    with pytest.raises(PipelineConfigError):
        _run_with_applier(
            second,
            [
                _case(
                    "case-2",
                    [_turn(1, "user", "Hello")],
                    run_config={"model_overrides": {"chat": "model-a"}},
                )
            ],
            applier,
            base_url=None,
        )

    assert applier.calls == []


# ---------------------------------------------------------------------------
# Pipeline: retries and overwrites
# ---------------------------------------------------------------------------
def test_attempt_and_overwrite_are_recorded_in_run_metadata(
    tmp_path: Path,
) -> None:
    """A retry and an overwrite record a fresh chat id in ``run.json``."""
    suite = _write_suite(
        tmp_path / "suite.jsonl", [_case("case-1", [_turn(1, "user", "Hello")])]
    )

    def once(workspace: Path, **options: Any) -> results.RunMetadata:
        workspace.mkdir(exist_ok=True)
        outcome = run_suite(
            str(suite),
            workspace_root=str(workspace),
            execute_turn=Recorder(),
            harness_seed=1234,
            max_attempts=1,
            sleep=lambda _delay: None,
            **options,
        )
        assert outcome.exit_code == 0
        output_dir = outcome.runs[0].output_dir
        assert output_dir is not None
        return load_stored_run(Path(output_dir)).metadata

    first = once(tmp_path / "ws-first")
    retried = once(tmp_path / "ws-retry", attempt=1)
    overwritten = once(tmp_path / "ws-first", overwrite=True)

    assert first.run_id == retried.run_id == overwritten.run_id
    assert len({first.chat_id, retried.chat_id, overwritten.chat_id}) == 3
    assert (first.attempt, retried.attempt, overwritten.attempt) == (0, 1, 0)
    assert (first.revision, retried.revision, overwritten.revision) == (0, 0, 1)
