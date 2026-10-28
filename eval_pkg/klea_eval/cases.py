# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Streaming JSONL test-case loader and validator (ADR-0056).

The loader reads a test suite one JSONL line at a time so that large suites
are never held in memory in full, validates every case against the ADR-0056
schema and invariants, and raises
:class:`~klea_eval.errors.CaseValidationError` for any load-time rejection.

Only the standard library is imported here: the loader must stay cheap to
import and must not drag in the model client or the HTTP stack.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klea_eval.errors import CaseValidationError

logger = logging.getLogger(__name__)

__all__ = [
    "SUPPORTED_SCHEMA_VERSIONS",
    "VALID_OUTPUT_FORMATS",
    "VALID_ROLES",
    "VALID_RUBRIC_TYPES",
    "ExpectedOutput",
    "Rubric",
    "TestCase",
    "TestSuite",
    "ToolExpectation",
    "Turn",
    "iter_cases",
    "load_suite",
]

#: Schema generations this harness understands (ADR-0056 versioning strategy).
SUPPORTED_SCHEMA_VERSIONS = frozenset({"1.0"})

#: Top-level fields that every case must carry.
REQUIRED_FIELDS = ("case_id", "schema_version", "turns")

#: Allowed turn roles, in the order used when rendering error messages.
VALID_ROLES = ("user", "assistant", "system")

#: Allowed expected-output formats, in error-message order.
VALID_OUTPUT_FORMATS = ("json", "yaml", "text", "binary")

#: Allowed rubric types, in error-message order.
VALID_RUBRIC_TYPES = ("binary", "scale", "categorical")

#: Default validation rule when an expected output omits one.
DEFAULT_VALIDATION_RULE = "exact_match"

#: Default tool call-count range when an expectation omits one.
DEFAULT_CALL_COUNT = (1, 1)


@dataclass(frozen=True, slots=True)
class ExpectedOutput:
    """One expected output file declared by a turn."""

    file_path: str
    output_format: str
    validation_rule: str


@dataclass(frozen=True, slots=True)
class ToolExpectation:
    """One tool-call expectation declared by a turn."""

    tool_name: str
    call_count: tuple[int, int]
    argument_constraints: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class Rubric:
    """One evaluation rubric declared by a case."""

    rubric_id: str
    rubric_type: str
    criteria: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Turn:
    """One conversation turn of a test case."""

    turn_number: int
    role: str
    content: str
    expected_outputs: tuple[ExpectedOutput, ...] = ()
    tool_expectations: tuple[ToolExpectation, ...] = ()


@dataclass(frozen=True, slots=True)
class TestCase:
    """A validated, immutable test case."""

    case_id: str
    schema_version: str
    description: str
    turns: tuple[Turn, ...]
    fixtures: dict[str, Any] | None = None
    run_config: dict[str, Any] | None = None
    rubrics: tuple[Rubric, ...] = ()


@dataclass(frozen=True, slots=True)
class TestSuite:
    """A fully validated suite of test cases loaded from one JSONL file."""

    source: Path
    cases: tuple[TestCase, ...]


def load_suite(path: str | Path) -> TestSuite:
    """Load and validate every case in the JSONL suite at ``path``.

    The whole suite is validated before it is returned (fail-fast, ADR-0056).
    """
    source = Path(path)
    logger.debug("loading test suite from %s", source)
    cases = tuple(iter_cases(source))
    logger.debug("loaded %d test cases from %s", len(cases), source)
    return TestSuite(source=source, cases=cases)


def iter_cases(path: str | Path) -> Iterator[TestCase]:
    """Yield validated cases from ``path`` one JSONL line at a time.

    Blank and whitespace-only lines are skipped.  Each case is validated
    before it is yielded, and ``case_id`` uniqueness is enforced across the
    whole file as the lines are consumed.
    """
    source = Path(path)
    first_seen: dict[str, int] = {}
    with source.open("r", encoding="utf-8") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            line = raw_line.strip()
            if not line:
                continue
            case = _parse_case(line, line_number, source)
            previous = first_seen.get(case.case_id)
            if previous is not None:
                raise _fail(
                    f"duplicate case_id '{case.case_id}' (first seen at line {previous})",
                    source,
                    line_number,
                    case.case_id,
                )
            first_seen[case.case_id] = line_number
            yield case


def _fail(
    message: str,
    source: Path,
    line_number: int | None,
    case_id: str | None = None,
) -> CaseValidationError:
    """Build a :class:`CaseValidationError` carrying the case location."""
    return CaseValidationError(
        message,
        source=source,
        line_number=line_number,
        case_id=case_id,
    )


def _allowed(values: tuple[str, ...]) -> str:
    """Render an allowed-value list for an error message."""
    return ", ".join(values)


def _parse_case(line: str, line_number: int, source: Path) -> TestCase:
    """Parse and validate one JSONL line into a :class:`TestCase`."""
    try:
        payload = json.loads(line)
    except json.JSONDecodeError as exc:
        raise _fail(f"invalid JSON: {exc}", source, line_number) from exc

    if not isinstance(payload, dict):
        raise _fail("line is not a JSON object", source, line_number)

    for name in REQUIRED_FIELDS:
        if name not in payload:
            raise _fail(f"missing required field '{name}'", source, line_number)

    case_id = payload["case_id"]
    if not isinstance(case_id, str) or not case_id.strip():
        raise _fail("field 'case_id' must be a non-empty string", source, line_number)

    schema_version = payload["schema_version"]
    if not isinstance(schema_version, str):
        raise _fail(
            "field 'schema_version' must be a string", source, line_number, case_id
        )
    if schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        supported = ", ".join(sorted(SUPPORTED_SCHEMA_VERSIONS))
        raise _fail(
            f"unsupported schema_version '{schema_version}' (supported: {supported})",
            source,
            line_number,
            case_id,
        )

    description = payload.get("description", "")
    if not isinstance(description, str):
        raise _fail(
            "field 'description' must be a string", source, line_number, case_id
        )

    fixtures = payload.get("fixtures")
    if fixtures is not None and not isinstance(fixtures, dict):
        raise _fail("field 'fixtures' must be an object", source, line_number, case_id)
    if fixtures is not None:
        _check_fixture_paths(fixtures, source, line_number, case_id)

    run_config = payload.get("run_config")
    if run_config is not None and not isinstance(run_config, dict):
        raise _fail(
            "field 'run_config' must be an object", source, line_number, case_id
        )

    turns_data = payload["turns"]
    if not isinstance(turns_data, list) or not turns_data:
        raise _fail(
            "field 'turns' must be a non-empty array", source, line_number, case_id
        )

    turns = tuple(
        _parse_turn(entry, expected_number, source, line_number, case_id)
        for expected_number, entry in enumerate(turns_data, start=1)
    )

    rubrics_data = payload.get("rubrics")
    if rubrics_data is None:
        rubrics: tuple[Rubric, ...] = ()
    elif not isinstance(rubrics_data, list):
        raise _fail("field 'rubrics' must be an array", source, line_number, case_id)
    else:
        rubrics = _parse_rubrics(rubrics_data, source, line_number, case_id)

    logger.debug("validated test case '%s' (line %d)", case_id, line_number)
    return TestCase(
        case_id=case_id,
        schema_version=schema_version,
        description=description,
        turns=turns,
        fixtures=fixtures,
        run_config=run_config,
        rubrics=rubrics,
    )


def _parse_turn(
    entry: Any,
    expected_number: int,
    source: Path,
    line_number: int,
    case_id: str,
) -> Turn:
    """Parse and validate one turn of a case."""
    if not isinstance(entry, dict):
        raise _fail("turn entries must be JSON objects", source, line_number, case_id)

    turn_number = entry.get("turn_number")
    if isinstance(turn_number, bool) or not isinstance(turn_number, int):
        raise _fail(
            "field 'turn_number' must be an integer", source, line_number, case_id
        )
    if turn_number != expected_number:
        raise _fail(
            f"turn {turn_number} out of order (expected {expected_number})",
            source,
            line_number,
            case_id,
        )

    role = entry.get("role")
    if role not in VALID_ROLES:
        raise _fail(
            f"turn {turn_number} has invalid role '{role}' (allowed: {_allowed(VALID_ROLES)})",
            source,
            line_number,
            case_id,
        )

    content = entry.get("content", "")
    if not isinstance(content, str):
        raise _fail(
            f"turn {turn_number} field 'content' must be a string",
            source,
            line_number,
            case_id,
        )

    expected_outputs = _parse_expected_outputs(
        entry.get("expected_outputs"), turn_number, source, line_number, case_id
    )
    tool_expectations = _parse_tool_expectations(
        entry.get("tool_expectations"), turn_number, source, line_number, case_id
    )

    return Turn(
        turn_number=turn_number,
        role=role,
        content=content,
        expected_outputs=expected_outputs,
        tool_expectations=tool_expectations,
    )


def _parse_expected_outputs(
    entries: Any,
    turn_number: int,
    source: Path,
    line_number: int,
    case_id: str,
) -> tuple[ExpectedOutput, ...]:
    """Parse the optional ``expected_outputs`` array of a turn."""
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise _fail(
            f"turn {turn_number} field 'expected_outputs' must be an array",
            source,
            line_number,
            case_id,
        )

    parsed: list[ExpectedOutput] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _fail(
                "expected_outputs entries must be JSON objects",
                source,
                line_number,
                case_id,
            )

        file_path = entry.get("file_path")
        if not isinstance(file_path, str) or not file_path.strip():
            raise _fail(
                "expected output is missing 'file_path'", source, line_number, case_id
            )

        output_format = entry.get("output_format")
        if output_format not in VALID_OUTPUT_FORMATS:
            raise _fail(
                f"expected output '{file_path}' has invalid output_format '{output_format}' "
                f"(allowed: {_allowed(VALID_OUTPUT_FORMATS)})",
                source,
                line_number,
                case_id,
            )

        validation_rule = entry.get("validation_rule", DEFAULT_VALIDATION_RULE)
        if not isinstance(validation_rule, str) or not validation_rule.strip():
            raise _fail(
                f"expected output '{file_path}' must have a non-empty "
                "'validation_rule'",
                source,
                line_number,
                case_id,
            )

        parsed.append(
            ExpectedOutput(
                file_path=file_path,
                output_format=output_format,
                validation_rule=validation_rule,
            )
        )

    return tuple(parsed)


def _parse_tool_expectations(
    entries: Any,
    turn_number: int,
    source: Path,
    line_number: int,
    case_id: str,
) -> tuple[ToolExpectation, ...]:
    """Parse the optional ``tool_expectations`` array of a turn."""
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise _fail(
            f"turn {turn_number} field 'tool_expectations' must be an array",
            source,
            line_number,
            case_id,
        )

    parsed: list[ToolExpectation] = []
    for entry in entries:
        if not isinstance(entry, dict):
            raise _fail(
                "tool_expectations entries must be JSON objects",
                source,
                line_number,
                case_id,
            )

        tool_name = entry.get("tool_name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise _fail(
                "tool expectation is missing 'tool_name'", source, line_number, case_id
            )

        call_count = _parse_call_count(
            entry.get("call_count", list(DEFAULT_CALL_COUNT)),
            tool_name,
            source,
            line_number,
            case_id,
        )

        argument_constraints = entry.get("argument_constraints")
        if argument_constraints is not None and not isinstance(
            argument_constraints, dict
        ):
            raise _fail(
                f"tool '{tool_name}' argument_constraints must be an object",
                source,
                line_number,
                case_id,
            )

        parsed.append(
            ToolExpectation(
                tool_name=tool_name,
                call_count=call_count,
                argument_constraints=argument_constraints,
            )
        )

    return tuple(parsed)


def _parse_call_count(
    value: Any,
    tool_name: str,
    source: Path,
    line_number: int,
    case_id: str,
) -> tuple[int, int]:
    """Validate a tool's ``[min, max]`` call-count range."""
    prefix = f"tool '{tool_name}' has invalid call_count"
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _fail(f"{prefix} {value!r}", source, line_number, case_id)

    low, high = value
    if (
        isinstance(low, bool)
        or isinstance(high, bool)
        or not isinstance(low, int)
        or not isinstance(high, int)
        or low < 0
        or high < low
    ):
        raise _fail(f"{prefix} [{low}, {high}]", source, line_number, case_id)

    return (low, high)


def _parse_rubrics(
    entries: list[Any],
    source: Path,
    line_number: int,
    case_id: str,
) -> tuple[Rubric, ...]:
    """Parse and validate the optional ``rubrics`` array of a case."""
    parsed: list[Rubric] = []
    seen: set[str] = set()

    for entry in entries:
        if not isinstance(entry, dict):
            raise _fail(
                "rubric entries must be JSON objects", source, line_number, case_id
            )

        rubric_id = entry.get("rubric_id")
        if not isinstance(rubric_id, str) or not rubric_id.strip():
            raise _fail("rubric is missing 'rubric_id'", source, line_number, case_id)
        if rubric_id in seen:
            raise _fail(
                f"duplicate rubric_id '{rubric_id}'", source, line_number, case_id
            )
        seen.add(rubric_id)

        rubric_type = entry.get("rubric_type")
        if rubric_type not in VALID_RUBRIC_TYPES:
            raise _fail(
                f"rubric '{rubric_id}' has invalid rubric_type '{rubric_type}' "
                f"(allowed: {_allowed(VALID_RUBRIC_TYPES)})",
                source,
                line_number,
                case_id,
            )

        criteria = entry.get("criteria")
        if not isinstance(criteria, list) or not all(
            isinstance(item, str) for item in criteria
        ):
            raise _fail(
                f"rubric '{rubric_id}' criteria must be a list of strings",
                source,
                line_number,
                case_id,
            )

        parsed.append(
            Rubric(
                rubric_id=rubric_id,
                rubric_type=rubric_type,
                criteria=tuple(criteria),
            )
        )

    return tuple(parsed)


def _check_fixture_paths(
    fixtures: dict[str, Any],
    source: Path,
    line_number: int,
    case_id: str,
) -> None:
    """Reject absolute fixture paths and paths escaping the case directory."""
    base = source.parent.resolve()

    for raw in _iter_fixture_strings(fixtures):
        candidate = Path(raw)
        if candidate.is_absolute():
            raise _fail(
                f"absolute fixture path not allowed: '{raw}'",
                source,
                line_number,
                case_id,
            )

        resolved = (base / candidate).resolve()
        try:
            resolved.relative_to(base)
        except ValueError:
            raise _fail(
                f"fixture path escapes the case directory: '{raw}'",
                source,
                line_number,
                case_id,
            ) from None


def _iter_fixture_strings(value: Any) -> Iterator[str]:
    """Yield every string nested inside the fixture definition."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_fixture_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _iter_fixture_strings(item)
