"""Offline tests for the ``klea-eval run --model`` option (ADR-0056, ADR-0060).

Nothing here touches the network or a live model: ``klea_eval.pipeline.run_suite``
is replaced by a recording stub, so only the CLI's option parsing and the
arguments it forwards are exercised.  Every invocation passes ``--base-url`` so
the base-URL check is never the fault under test.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from klea_eval import pipeline
from klea_eval.ui.cli import app
from typer.testing import CliRunner

pytestmark = pytest.mark.eval

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
        "description": "cli --model fixture",
        "turns": turns,
    }
    payload.update(overrides)
    return payload


def _write_suite(path: Path, cases: list[dict[str, Any]]) -> Path:
    """Serialise ``cases`` as JSONL to ``path`` and return it."""
    lines = [json.dumps(case) for case in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _valid_suite(tmp_path: Path) -> Path:
    """Return a one-case suite that the loader accepts."""
    return _write_suite(
        tmp_path / "valid.jsonl", [_case("case-1", [_turn(1, "user", "Hello")])]
    )


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


# ---------------------------------------------------------------------------
# CLI: --model parsing
# ---------------------------------------------------------------------------
def test_cli_run_accumulates_repeated_model_pairs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Two repeated ``ROLE=MODEL`` pairs accumulate into one mapping."""
    recorded = _stub_pipeline(monkeypatch, [StubCase("case-1", True)])

    result = _cli(
        [
            "run",
            str(_valid_suite(tmp_path)),
            "-u",
            "http://example.test",
            "--model",
            "chat=model-a",
            "-m",
            "judge=model-b",
        ]
    )

    assert result.exit_code == 0, _combined(result)
    assert recorded["kwargs"]["model_overrides"] == {
        "chat": "model-a",
        "judge": "model-b",
    }


@pytest.mark.parametrize("entry", ["chat", "=model-a", "chat=", "= "])
def test_cli_run_rejects_a_malformed_model_pair(
    tmp_path: Path, monkeypatch: Any, entry: str
) -> None:
    """A pair without '=', an empty role or an empty model exits 2."""
    recorded = _stub_pipeline(monkeypatch, [StubCase("case-1", True)])

    result = _cli(
        [
            "run",
            str(_valid_suite(tmp_path)),
            "-u",
            "http://example.test",
            "-m",
            entry,
        ]
    )
    combined = _combined(result)

    assert result.exit_code == 2, combined
    assert f"error: --model expects ROLE=MODEL, got '{entry}'" in combined
    assert recorded == {}


def test_cli_run_without_model_leaves_the_overrides_unset(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """No ``-m`` forwards no override mapping at all."""
    recorded = _stub_pipeline(monkeypatch, [StubCase("case-1", True)])

    result = _cli(["run", str(_valid_suite(tmp_path)), "-u", "http://example.test"])

    assert result.exit_code == 0, _combined(result)
    assert recorded["kwargs"]["model_overrides"] is None
