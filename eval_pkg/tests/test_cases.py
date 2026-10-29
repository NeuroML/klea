# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Tests for the streaming JSONL test-case loader (ADR-0056).

Each load-time rejection rule from ADR-0056 gets its own test asserting the
raised :class:`~klea_eval.errors.CaseValidationError` message, so the schema
contract documented in the ADR is pinned by the suite.  Only the standard
library and ``pytest`` are used: the loader must not depend on the model
client or the HTTP stack.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from klea_eval.cases import (
    SUPPORTED_SCHEMA_VERSIONS,
    VALID_OUTPUT_FORMATS,
    VALID_ROLES,
    VALID_RUBRIC_TYPES,
    iter_cases,
    load_suite,
)
from klea_eval.errors import CaseValidationError


def _turn(turn_number: int = 1, role: str = "user", **overrides: Any) -> dict[str, Any]:
    """Build a minimal valid turn payload."""
    payload: dict[str, Any] = {
        "turn_number": turn_number,
        "role": role,
        "content": "do the task",
    }
    payload.update(overrides)
    return payload


def _case(case_id: str = "simple-001", **overrides: Any) -> dict[str, Any]:
    """Build a minimal valid case payload, overridable per test."""
    payload: dict[str, Any] = {
        "case_id": case_id,
        "schema_version": "1.0",
        "description": "test case",
        "turns": [_turn()],
    }
    payload.update(overrides)
    return payload


def _write_suite(path: Path, cases: list[dict[str, Any] | str]) -> Path:
    """Serialise ``cases`` as JSONL to ``path`` and return it."""
    lines = [case if isinstance(case, str) else json.dumps(case) for case in cases]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _write_raw(path: Path, text: str) -> Path:
    """Write verbatim ``text`` to ``path`` and return it."""
    path.write_text(text, encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_load_suite_parses_complex_case(tmp_path: Path) -> None:
    """A full multi-turn case loads and exposes its parsed structure."""
    case = {
        "case_id": "complex-001",
        "schema_version": "1.0",
        "description": "Multi-turn conversation with file operations",
        "fixtures": {
            "input_dir": "fixtures/complex-001",
            "files": ["data.json", "config.yaml"],
        },
        "run_config": {"timeout": 30, "seed": 42},
        "turns": [
            _turn(
                1,
                tool_expectations=[
                    {
                        "tool_name": "read_file",
                        "call_count": [1, 1],
                        "argument_constraints": {"path": "data.json"},
                    }
                ],
            ),
            _turn(
                2,
                role="assistant",
                content="The data contains...",
                expected_outputs=[
                    {
                        "file_path": "summary.txt",
                        "output_format": "text",
                        "validation_rule": "contains",
                    }
                ],
            ),
            _turn(
                3,
                tool_expectations=[
                    {
                        "tool_name": "write_file",
                        "call_count": [1, 1],
                        "argument_constraints": {"path": "summary.txt"},
                    }
                ],
            ),
        ],
        "rubrics": [
            {
                "rubric_id": "accuracy",
                "rubric_type": "scale",
                "criteria": [
                    "Summary accurately reflects data contents",
                    "No hallucinations",
                ],
            },
            {
                "rubric_id": "tool_usage",
                "rubric_type": "binary",
                "criteria": ["Correct tools called with correct arguments"],
            },
        ],
    }
    path = _write_suite(tmp_path / "suite.jsonl", [case])

    suite = load_suite(path)

    assert suite.source == path
    assert len(suite.cases) == 1

    loaded = suite.cases[0]
    assert loaded.case_id == "complex-001"
    assert loaded.schema_version == "1.0"
    assert loaded.description == "Multi-turn conversation with file operations"
    assert loaded.fixtures == {
        "input_dir": "fixtures/complex-001",
        "files": ["data.json", "config.yaml"],
    }
    assert loaded.run_config == {"timeout": 30, "seed": 42}

    assert len(loaded.turns) == 3
    first, second, third = loaded.turns
    assert (first.turn_number, first.role) == (1, "user")
    assert first.expected_outputs == ()
    assert len(first.tool_expectations) == 1
    assert first.tool_expectations[0].tool_name == "read_file"
    assert first.tool_expectations[0].call_count == (1, 1)
    assert first.tool_expectations[0].argument_constraints == {"path": "data.json"}

    assert second.role == "assistant"
    assert second.content == "The data contains..."
    assert len(second.expected_outputs) == 1
    assert second.expected_outputs[0].file_path == "summary.txt"
    assert second.expected_outputs[0].output_format == "text"
    assert second.expected_outputs[0].validation_rule == "contains"

    assert third.tool_expectations[0].tool_name == "write_file"

    assert len(loaded.rubrics) == 2
    assert loaded.rubrics[0].rubric_id == "accuracy"
    assert loaded.rubrics[0].rubric_type == "scale"
    assert loaded.rubrics[0].criteria == (
        "Summary accurately reflects data contents",
        "No hallucinations",
    )
    assert loaded.rubrics[1].rubric_id == "tool_usage"
    assert loaded.rubrics[1].rubric_type == "binary"


def test_load_suite_applies_defaults_for_optional_fields(tmp_path: Path) -> None:
    """Optional fields default to empty/None when omitted."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [{"case_id": "minimal-001", "schema_version": "1.0", "turns": [_turn()]}],
    )

    loaded = load_suite(path).cases[0]

    assert loaded.description == ""
    assert loaded.fixtures is None
    assert loaded.run_config is None
    assert loaded.rubrics == ()
    assert loaded.turns[0].expected_outputs == ()
    assert loaded.turns[0].tool_expectations == ()


def test_load_suite_ignores_unknown_top_level_fields(tmp_path: Path) -> None:
    """Extra optional top-level keys do not fail validation."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(note="extra")])

    assert load_suite(path).cases[0].case_id == "simple-001"


def test_iter_cases_skips_blank_lines(tmp_path: Path) -> None:
    """Blank and whitespace-only lines are skipped."""
    path = _write_raw(
        tmp_path / "suite.jsonl",
        "\n   \n" + json.dumps(_case()) + "\n\n",
    )

    cases = list(iter_cases(path))

    assert [case.case_id for case in cases] == ["simple-001"]


def test_iter_cases_streams_before_later_failure(tmp_path: Path) -> None:
    """The iterator yields a valid case before a later line fails."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(), "{not json"])
    cases = iter_cases(path)

    first = next(cases)
    assert first.case_id == "simple-001"

    with pytest.raises(CaseValidationError) as excinfo:
        next(cases)
    assert excinfo.value.line_number == 2
    assert excinfo.value.message.startswith("invalid JSON")


# ---------------------------------------------------------------------------
# Rejection rules (ADR-0056)
# ---------------------------------------------------------------------------


def test_invalid_json_line_is_rejected(tmp_path: Path) -> None:
    """A malformed JSONL line is rejected with the decoder detail."""
    path = _write_raw(tmp_path / "suite.jsonl", "{not json\n")

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message.startswith("invalid JSON")
    assert excinfo.value.line_number == 1


def test_non_object_line_is_rejected(tmp_path: Path) -> None:
    """A JSONL line that is not a JSON object is rejected."""
    path = _write_raw(tmp_path / "suite.jsonl", "[1, 2, 3]\n")

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "line is not a JSON object"


def test_unsupported_schema_version_is_rejected(tmp_path: Path) -> None:
    """An unknown schema version is rejected."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(schema_version="2.0")])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "unsupported schema_version '2.0' (supported: 1.0)"
    )
    assert excinfo.value.case_id == "simple-001"
    assert excinfo.value.line_number == 1
    assert SUPPORTED_SCHEMA_VERSIONS == frozenset({"1.0"})


def test_missing_required_field_is_rejected(tmp_path: Path) -> None:
    """A case without the required `turns` field is rejected."""
    payload = _case()
    del payload["turns"]
    path = _write_suite(tmp_path / "suite.jsonl", [payload])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "missing required field 'turns'"


def test_empty_case_id_is_rejected(tmp_path: Path) -> None:
    """An empty `case_id` is rejected."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(case_id="")])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "field 'case_id' must be a non-empty string"


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    """A repeated case_id within a suite is rejected."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(), _case()])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "duplicate case_id 'simple-001' (first seen at line 1)"
    )
    assert excinfo.value.line_number == 2


def test_empty_turns_is_rejected(tmp_path: Path) -> None:
    """An empty `turns` array is rejected."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(turns=[])])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "field 'turns' must be a non-empty array"


def test_non_sequential_turns_are_rejected(tmp_path: Path) -> None:
    """Out-of-order turn numbers are rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [_case(turns=[_turn(1), _turn(3)])],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "turn 3 out of order (expected 2)"


def test_invalid_turn_role_is_rejected(tmp_path: Path) -> None:
    """A turn with a role outside the allowed set is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl", [_case(turns=[_turn(1, role="tool")])]
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "turn 1 has invalid role 'tool' (allowed: user, assistant, system)"
    )
    assert VALID_ROLES == ("user", "assistant", "system")


def test_absolute_fixture_path_is_rejected(tmp_path: Path) -> None:
    """An absolute fixture path is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [_case(fixtures={"input_dir": "/tmp/fixtures/x"})],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "absolute fixture path not allowed: '/tmp/fixtures/x'"
    )


def test_escaping_fixture_path_is_rejected(tmp_path: Path) -> None:
    """A fixture path escaping the case directory is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [_case(fixtures={"input_dir": "../secrets"})],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "fixture path escapes the case directory: '../secrets'"
    )


def test_non_object_fixtures_is_rejected(tmp_path: Path) -> None:
    """A non-object `fixtures` value is rejected."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(fixtures=["x"])])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "field 'fixtures' must be an object"


def test_non_object_run_config_is_rejected(tmp_path: Path) -> None:
    """A non-object `run_config` value is rejected."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(run_config="quick")])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "field 'run_config' must be an object"


def test_invalid_expected_output_format_is_rejected(tmp_path: Path) -> None:
    """An unexpected output format is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [
            _case(
                turns=[
                    _turn(
                        1,
                        expected_outputs=[
                            {"file_path": "out.txt", "output_format": "xml"}
                        ],
                    )
                ]
            )
        ],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "expected output 'out.txt' has invalid output_format 'xml' "
        "(allowed: json, yaml, text, binary)"
    )
    assert VALID_OUTPUT_FORMATS == ("json", "yaml", "text", "binary")


def test_invalid_call_count_range_is_rejected(tmp_path: Path) -> None:
    """A tool call-count range with high < low is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [
            _case(
                turns=[
                    _turn(
                        1,
                        tool_expectations=[
                            {"tool_name": "read_file", "call_count": [3, 1]}
                        ],
                    )
                ]
            )
        ],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == ("tool 'read_file' has invalid call_count [3, 1]")


def test_invalid_rubric_type_is_rejected(tmp_path: Path) -> None:
    """An unexpected rubric type is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [
            _case(
                rubrics=[
                    {
                        "rubric_id": "accuracy",
                        "rubric_type": "ranked",
                        "criteria": ["a"],
                    }
                ]
            )
        ],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "rubric 'accuracy' has invalid rubric_type 'ranked' "
        "(allowed: binary, scale, categorical)"
    )
    assert VALID_RUBRIC_TYPES == ("binary", "scale", "categorical")


def test_duplicate_rubric_id_is_rejected(tmp_path: Path) -> None:
    """A repeated rubric_id within a case is rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [
            _case(
                rubrics=[
                    {
                        "rubric_id": "accuracy",
                        "rubric_type": "binary",
                        "criteria": ["a"],
                    },
                    {
                        "rubric_id": "accuracy",
                        "rubric_type": "binary",
                        "criteria": ["b"],
                    },
                ]
            )
        ],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == "duplicate rubric_id 'accuracy'"


def test_invalid_rubric_criteria_is_rejected(tmp_path: Path) -> None:
    """Rubric criteria that are not a list of strings are rejected."""
    path = _write_suite(
        tmp_path / "suite.jsonl",
        [
            _case(
                rubrics=[
                    {"rubric_id": "accuracy", "rubric_type": "binary", "criteria": "a"}
                ]
            )
        ],
    )

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    assert excinfo.value.message == (
        "rubric 'accuracy' criteria must be a list of strings"
    )


# ---------------------------------------------------------------------------
# Error rendering
# ---------------------------------------------------------------------------


def test_error_str_includes_source_and_line(tmp_path: Path) -> None:
    """`str()` of a validation error renders `<source>:<line>: <message>`."""
    path = _write_suite(tmp_path / "suite.jsonl", [_case(schema_version="2.0")])

    with pytest.raises(CaseValidationError) as excinfo:
        load_suite(path)

    error = excinfo.value
    assert isinstance(error, ValueError)
    assert str(error) == f"{path}:1: {error.message}"
