"""Offline tests for the evaluation pipeline and the ``run`` command.

Nothing in this module reaches the network or a live model: suites are written
to ``tmp_path`` and the model-facing collaborators are replaced by fake turn
executors that return plain mappings.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from klea_eval import pipeline, results
from klea_eval.errors import CaseValidationError
from klea_eval.pipeline import PipelineError, run_suite
from klea_eval.results import load_stored_run
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
