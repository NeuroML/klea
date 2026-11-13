"""Tests for the ``klea-eval validate`` CLI command (ADR-0056).

The command must exit 0 and print a ``<source>: N cases valid`` summary for a
good suite, and exit 2 with a ``<source>:<line>: <message>`` diagnostic on
stderr for a rejected one.  Only ``typer``/``click`` and the loader are
exercised: no model client or HTTP stack is imported.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from klea_eval.ui.cli import app
from typer.testing import CliRunner

runner = CliRunner()


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
        "description": "cli validation fixture",
        "turns": turns,
    }
    payload.update(overrides)
    return payload


def _write_suite(path: Path, cases: list[dict[str, Any] | str]) -> Path:
    """Serialise ``cases`` as JSONL to ``path`` and return it."""
    lines = [case if isinstance(case, str) else json.dumps(case) for case in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _combined(result: Any) -> str:
    """Return stdout and stderr together, whatever click version is installed."""
    text = result.output or ""
    stderr = getattr(result, "stderr", "") or ""
    return text + stderr


def test_validate_accepts_valid_suite(tmp_path: Path) -> None:
    """A well-formed suite exits 0 and prints the case-count summary."""
    suite = _write_suite(
        tmp_path / "valid.jsonl",
        [
            _case(
                "case-1",
                [_turn(1, "user", "Hello"), _turn(2, "chat", "Hi there")],
            ),
            _case(
                "case-2",
                [_turn(1, "user", "Bye"), _turn(2, "chat", "See you")],
            ),
        ],
    )

    result = runner.invoke(app, ["validate", str(suite)])

    assert result.exit_code == 0, _combined(result)
    assert f"{suite}: 2 cases valid" in _combined(result)


def test_validate_rejects_invalid_role(tmp_path: Path) -> None:
    """A bad turn role exits 2 with a ``path:line: message`` diagnostic."""
    suite = _write_suite(
        tmp_path / "invalid.jsonl",
        [
            _case("case-1", [_turn(1, "assistant", "Hi")]),
            {"case_id": "case-2", "schema_version": "1.0"},
        ],
    )

    result = runner.invoke(app, ["validate", str(suite)])
    combined = _combined(result)

    assert result.exit_code == 2, combined
    assert re.search(
        rf"{re.escape(str(suite))}:1: .*invalid role 'assistant'", combined
    ), combined


def test_validate_rejects_a_turn_with_an_input_key(tmp_path: Path) -> None:
    """A turn that puts its text in `input` exits 2 with the unknown-key fault."""
    suite = _write_suite(
        tmp_path / "input.jsonl",
        [_case("case-1", [{"turn_number": 1, "role": "user", "input": "Hello"}])],
    )

    result = runner.invoke(app, ["validate", str(suite)])
    combined = _combined(result)

    assert result.exit_code == 2, combined
    assert re.search(
        rf"{re.escape(str(suite))}:1: .*unknown field 'input'", combined
    ), combined


def test_validate_reports_missing_required_field_line(tmp_path: Path) -> None:
    """A missing required field reports the offending line number."""
    suite = _write_suite(
        tmp_path / "missing.jsonl",
        [
            _case("case-1", [_turn(1, "user", "Hello")]),
            {"case_id": "case-2", "schema_version": "1.0"},
        ],
    )

    result = runner.invoke(app, ["validate", str(suite)])
    combined = _combined(result)

    assert result.exit_code == 2, combined
    assert re.search(
        rf"{re.escape(str(suite))}:2: missing required field 'turns'", combined
    ), combined
