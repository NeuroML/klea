# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the deterministic expectation checker.

Every fixture is a temporary run workspace written under ``tmp_path`` plus a
hand-built case and its retained per-turn records, so the suite needs no server,
no network access and no LLM; it carries the ``eval`` marker only, like
``tests/test_scoring.py``.
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from typing import Any, cast

import pytest
from klea_eval import expectations, metrics
from klea_eval.cases import ExpectedOutput, TestCase, ToolExpectation, Turn
from klea_eval.expectations import (
    KIND_ARGUMENT_CONSTRAINTS,
    KIND_CALL_COUNT,
    KIND_COMMAND_EXIT,
    KIND_FILE_PRESENT,
    KIND_FORMAT_DECODE,
    KIND_TOOL_NAME,
    KIND_VALIDATION_RULE,
    OUTCOME_FAIL,
    OUTCOME_PASS,
    OUTCOME_UNVERIFIABLE,
    ExpectationError,
)

pytestmark = pytest.mark.eval


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------
def _workspace(tmp_path: Path) -> Path:
    """Create and return a run's ``workspace/`` directory."""
    root = tmp_path / "workspace"
    root.mkdir()
    return root


def _output(**overrides: Any) -> ExpectedOutput:
    """Build one expected output, defaulting to an unruled text file."""
    fields: dict[str, Any] = {
        "file_path": "out.txt",
        "output_format": "text",
        "validation_rule": "",
    }
    fields.update(overrides)
    return ExpectedOutput(**fields)


def _expectation(**overrides: Any) -> ToolExpectation:
    """Build one tool expectation, defaulting to a single ``read_file`` call."""
    fields: dict[str, Any] = {"tool_name": "read_file", "call_count": (1, 1)}
    fields.update(overrides)
    return ToolExpectation(**fields)


def _turn(
    number: int = 1,
    *,
    expected_outputs: tuple[ExpectedOutput, ...] = (),
    tool_expectations: tuple[ToolExpectation, ...] = (),
) -> Turn:
    """Build one case turn declaring the given expectations."""
    return Turn(
        turn_number=number,
        role="user",
        content="do the task",
        expected_outputs=expected_outputs,
        tool_expectations=tool_expectations,
    )


def _case(*turns: Turn) -> TestCase:
    """Build a case carrying ``turns``."""
    return TestCase(
        case_id="case-1",
        schema_version="1.0",
        description="expectation fixture",
        turns=tuple(turns),
    )


def _command_case(command: tuple[str, ...], *, timeout: float = 300.0) -> TestCase:
    """Build a case whose only declared expectation is ``command``."""
    return TestCase(
        case_id="case-1",
        schema_version="1.0",
        description="command fixture",
        turns=(_turn(),),
        command=command,
        command_timeout=timeout,
    )


def _call(tool_name: str, **arguments: Any) -> metrics.ToolCallRecord:
    """Build one retained tool-call record."""
    return metrics.ToolCallRecord(tool_name=tool_name, arguments=dict(arguments))


def _record(
    number: int = 1, tool_calls: tuple[metrics.ToolCallRecord, ...] = ()
) -> metrics.TurnMetrics:
    """Build one retained per-turn record carrying ``tool_calls``."""
    return metrics.TurnMetrics(turn_number=number, tool_calls=tool_calls)


def _by_kind(checks: Any, kind: str) -> Any:
    """Return the single check of ``kind``, asserting there is exactly one."""
    found = [check for check in checks if check.kind == kind]
    assert len(found) == 1, (kind, checks)
    return found[0]


def test_grade_checks_stays_unverifiable_over_a_passing_check() -> None:
    """A passing check never overrides an unverifiable one."""
    checks = (
        expectations.ExpectationCheck(
            turn_number=1,
            kind=KIND_FILE_PRESENT,
            target="out.txt",
            outcome=OUTCOME_PASS,
            detail="present",
        ),
        expectations.ExpectationCheck(
            turn_number=1,
            kind=KIND_FORMAT_DECODE,
            target="out.txt",
            outcome=OUTCOME_PASS,
            detail="decoded",
        ),
        expectations.ExpectationCheck(
            turn_number=1,
            kind=KIND_VALIDATION_RULE,
            target="out.txt",
            outcome=OUTCOME_UNVERIFIABLE,
            detail="no rule to apply",
        ),
    )

    assert expectations.grade_checks(checks) is None


# ---------------------------------------------------------------------------
# Expected outputs: file presence, decoding and validation rules
# ---------------------------------------------------------------------------
def test_case_without_expectations_yields_no_checks(tmp_path: Path) -> None:
    """A case declaring nothing produces no verdicts."""
    checks = expectations.check_case(_case(_turn()), _workspace(tmp_path), ())

    assert checks == ()


def test_exact_match_passes_against_expected_content(tmp_path: Path) -> None:
    """`exact_match` passes when the decoded file equals `expected_content`."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("hello\n", encoding="utf-8")
    turn = _turn(
        1,
        expected_outputs=(
            _output(validation_rule="exact_match", expected_content="hello\n"),
        ),
    )

    checks = expectations.check_case(_case(turn), root, ())

    assert _by_kind(checks, KIND_FILE_PRESENT).outcome == OUTCOME_PASS
    assert _by_kind(checks, KIND_FORMAT_DECODE).outcome == OUTCOME_PASS
    assert _by_kind(checks, KIND_VALIDATION_RULE).outcome == OUTCOME_PASS
    assert expectations.grade_checks(checks) is True


def test_exact_match_fails_when_the_content_differs(tmp_path: Path) -> None:
    """`exact_match` fails, and names the target, when the text differs."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("hello\n", encoding="utf-8")
    turn = _turn(
        1,
        expected_outputs=(
            _output(validation_rule="exact_match", expected_content="goodbye"),
        ),
    )

    checks = expectations.check_case(_case(turn), root, ())

    rule = _by_kind(checks, KIND_VALIDATION_RULE)
    assert rule.outcome == OUTCOME_FAIL
    assert rule.detail == "validation_rule 'exact_match' did not hold for 'out.txt'"
    assert expectations.grade_checks(checks) is False


def test_contains_passes_when_the_substring_is_present(tmp_path: Path) -> None:
    """`contains` passes when `expected_content` is a substring."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("the quick brown fox", encoding="utf-8")
    turn = _turn(
        1,
        expected_outputs=(
            _output(validation_rule="contains", expected_content="brown"),
        ),
    )

    checks = expectations.check_case(_case(turn), root, ())

    assert _by_kind(checks, KIND_VALIDATION_RULE).outcome == OUTCOME_PASS
    assert expectations.grade_checks(checks) is True


def test_contains_fails_when_the_substring_is_absent(tmp_path: Path) -> None:
    """`contains` fails when `expected_content` is not a substring."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("the quick brown fox", encoding="utf-8")
    turn = _turn(
        1,
        expected_outputs=(
            _output(validation_rule="contains", expected_content="purple"),
        ),
    )

    checks = expectations.check_case(_case(turn), root, ())

    rule = _by_kind(checks, KIND_VALIDATION_RULE)
    assert rule.outcome == OUTCOME_FAIL
    assert rule.detail == "validation_rule 'contains' did not hold for 'out.txt'"
    assert expectations.grade_checks(checks) is False


def test_text_output_is_decoded(tmp_path: Path) -> None:
    """A `text` output decodes as UTF-8."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("plain text\n", encoding="utf-8")

    checks = expectations.check_case(
        _case(_turn(1, expected_outputs=(_output(),))), root, ()
    )

    assert [check.kind for check in checks] == [KIND_FILE_PRESENT, KIND_FORMAT_DECODE]
    assert _by_kind(checks, KIND_FORMAT_DECODE).outcome == OUTCOME_PASS


def test_json_output_is_decoded(tmp_path: Path) -> None:
    """A `json` output decodes as JSON."""
    root = _workspace(tmp_path)
    (root / "out.json").write_text('{"answer": 42}', encoding="utf-8")
    turn = _turn(
        1, expected_outputs=(_output(file_path="out.json", output_format="json"),)
    )

    checks = expectations.check_case(_case(turn), root, ())

    assert _by_kind(checks, KIND_FORMAT_DECODE).outcome == OUTCOME_PASS


def test_invalid_json_output_fails_decoding(tmp_path: Path) -> None:
    """Undecodable JSON fails the decode check and ends the entry."""
    root = _workspace(tmp_path)
    (root / "out.json").write_text("not json", encoding="utf-8")
    turn = _turn(
        1,
        expected_outputs=(
            _output(
                file_path="out.json",
                output_format="json",
                validation_rule="exact_match",
                expected_content="{}",
            ),
        ),
    )

    checks = expectations.check_case(_case(turn), root, ())

    decode = _by_kind(checks, KIND_FORMAT_DECODE)
    assert decode.outcome == OUTCOME_FAIL
    assert decode.detail.startswith("the expected output is not valid JSON:")
    assert [check.kind for check in checks] == [KIND_FILE_PRESENT, KIND_FORMAT_DECODE]


def test_yaml_output_is_unverifiable(tmp_path: Path) -> None:
    """A `yaml` output is unverifiable: no parser is in the dependency set."""
    root = _workspace(tmp_path)
    (root / "out.yaml").write_text("answer: 42\n", encoding="utf-8")
    turn = _turn(
        1, expected_outputs=(_output(file_path="out.yaml", output_format="yaml"),)
    )

    checks = expectations.check_case(_case(turn), root, ())

    decode = _by_kind(checks, KIND_FORMAT_DECODE)
    assert decode.outcome == OUTCOME_UNVERIFIABLE
    assert decode.detail == "no YAML parser is in the harness dependency set"


def test_validation_rule_without_expected_content_is_unverifiable(
    tmp_path: Path,
) -> None:
    """A rule with no operand is unverifiable rather than passing by default."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("hello\n", encoding="utf-8")
    turn = _turn(1, expected_outputs=(_output(validation_rule="exact_match"),))

    checks = expectations.check_case(_case(turn), root, ())

    rule = _by_kind(checks, KIND_VALIDATION_RULE)
    assert rule.outcome == OUTCOME_UNVERIFIABLE
    assert rule.detail == (
        "validation_rule 'exact_match' has no expected value in the case schema"
    )
    assert expectations.grade_checks(checks) is None


def test_missing_output_file_fails(tmp_path: Path) -> None:
    """A declared output the run never produced fails."""
    root = _workspace(tmp_path)

    checks = expectations.check_case(
        _case(_turn(1, expected_outputs=(_output(),))), root, ()
    )

    assert [check.kind for check in checks] == [KIND_FILE_PRESENT]
    present = _by_kind(checks, KIND_FILE_PRESENT)
    assert present.outcome == OUTCOME_FAIL
    assert present.detail == "the run workspace holds no such file"


def test_absolute_output_path_is_refused(tmp_path: Path) -> None:
    """An absolute `file_path` is refused without opening it."""
    root = _workspace(tmp_path)
    (root / "out.txt").write_text("hi", encoding="utf-8")
    turn = _turn(1, expected_outputs=(_output(file_path=str(root / "out.txt")),))

    checks = expectations.check_case(_case(turn), root, ())

    present = _by_kind(checks, KIND_FILE_PRESENT)
    assert present.outcome == OUTCOME_FAIL
    assert present.detail == "the expected output path is absolute"


def test_workspace_escaping_output_path_is_refused(tmp_path: Path) -> None:
    """A `..` path leaving the workspace is refused even when it exists."""
    root = _workspace(tmp_path)
    (tmp_path / "outside.txt").write_text("hi", encoding="utf-8")
    turn = _turn(1, expected_outputs=(_output(file_path="../outside.txt"),))

    checks = expectations.check_case(_case(turn), root, ())

    present = _by_kind(checks, KIND_FILE_PRESENT)
    assert present.outcome == OUTCOME_FAIL
    assert present.detail == "the expected output path escapes the run workspace"


# ---------------------------------------------------------------------------
# Tool expectations
# ---------------------------------------------------------------------------
def test_declared_tool_call_is_found(tmp_path: Path) -> None:
    """A recorded call satisfies the tool name and call-count checks."""
    root = _workspace(tmp_path)
    record = _record(1, (_call("read_file", path="data.json"),))
    turn = _turn(1, tool_expectations=(_expectation(),))

    checks = expectations.check_case(_case(turn), root, (record,))

    assert [check.kind for check in checks] == [KIND_TOOL_NAME, KIND_CALL_COUNT]
    assert all(check.passed for check in checks)
    assert expectations.grade_checks(checks) is True


def test_undeclared_tool_call_fails_both_checks(tmp_path: Path) -> None:
    """A call to another tool fails the name and the call-count checks."""
    root = _workspace(tmp_path)
    record = _record(1, (_call("write_file", path="out.txt"),))
    turn = _turn(1, tool_expectations=(_expectation(),))

    checks = expectations.check_case(_case(turn), root, (record,))

    name = _by_kind(checks, KIND_TOOL_NAME)
    assert name.outcome == OUTCOME_FAIL
    assert name.detail == "the run recorded no 'read_file' call"
    count = _by_kind(checks, KIND_CALL_COUNT)
    assert count.outcome == OUTCOME_FAIL
    assert count.detail == "0 call(s), expected 1..1"
    assert expectations.grade_checks(checks) is False


def test_unrecorded_tool_calls_are_unverifiable(tmp_path: Path) -> None:
    """A turn with no retained records is unverifiable, not failed."""
    root = _workspace(tmp_path)
    turn = _turn(1, tool_expectations=(_expectation(),))

    checks = expectations.check_case(_case(turn), root, ())

    assert len(checks) == 1
    assert checks[0].kind == KIND_TOOL_NAME
    assert checks[0].outcome == OUTCOME_UNVERIFIABLE
    assert checks[0].detail == "the run recorded no tool calls for this turn"
    assert expectations.grade_checks(checks) is None


def test_call_count_inside_the_range_passes(tmp_path: Path) -> None:
    """A call count inside `[min, max]` passes."""
    root = _workspace(tmp_path)
    record = _record(1, (_call("read_file", path="a"), _call("read_file", path="b")))
    turn = _turn(1, tool_expectations=(_expectation(call_count=(1, 2)),))

    checks = expectations.check_case(_case(turn), root, (record,))

    assert _by_kind(checks, KIND_CALL_COUNT).outcome == OUTCOME_PASS


def test_call_count_outside_the_range_fails(tmp_path: Path) -> None:
    """A call count outside `[min, max]` fails and reports the range."""
    root = _workspace(tmp_path)
    record = _record(1, (_call("read_file", path="a"),))
    turn = _turn(1, tool_expectations=(_expectation(call_count=(2, 3)),))

    checks = expectations.check_case(_case(turn), root, (record,))

    count = _by_kind(checks, KIND_CALL_COUNT)
    assert count.outcome == OUTCOME_FAIL
    assert count.detail == "1 call(s), expected 2..3"


def test_argument_constraints_subset_passes(tmp_path: Path) -> None:
    """Constraints are a subset match: extra call arguments are allowed."""
    root = _workspace(tmp_path)
    record = _record(1, (_call("read_file", path="data.json", limit=10),))
    turn = _turn(
        1,
        tool_expectations=(_expectation(argument_constraints={"path": "data.json"}),),
    )

    checks = expectations.check_case(_case(turn), root, (record,))

    assert _by_kind(checks, KIND_ARGUMENT_CONSTRAINTS).outcome == OUTCOME_PASS
    assert expectations.grade_checks(checks) is True


def test_argument_constraints_mismatch_fails(tmp_path: Path) -> None:
    """A call whose arguments contradict the constraints fails."""
    root = _workspace(tmp_path)
    record = _record(1, (_call("read_file", path="data.json"),))
    turn = _turn(
        1,
        tool_expectations=(_expectation(argument_constraints={"path": "other.json"}),),
    )

    checks = expectations.check_case(_case(turn), root, (record,))

    constraints = _by_kind(checks, KIND_ARGUMENT_CONSTRAINTS)
    assert constraints.outcome == OUTCOME_FAIL
    assert constraints.detail == "no matching call carried the declared arguments"


# ---------------------------------------------------------------------------
# Execution checks
# ---------------------------------------------------------------------------
def test_command_exit_zero_passes(tmp_path: Path) -> None:
    """A command that exits 0 passes and grades the case."""
    case = _command_case((sys.executable, "-c", "pass"))

    checks = expectations.check_case(case, _workspace(tmp_path), (_record(1),))

    check = _by_kind(checks, KIND_COMMAND_EXIT)
    assert check.outcome == OUTCOME_PASS
    assert check.exit_code == 0
    assert check.detail is None
    assert expectations.grade_checks(checks) is True


def test_command_non_zero_exit_fails(tmp_path: Path) -> None:
    """A command that exits non-zero fails and records its status."""
    case = _command_case((sys.executable, "-c", "raise SystemExit(3)"))

    checks = expectations.check_case(case, _workspace(tmp_path), (_record(1),))

    check = _by_kind(checks, KIND_COMMAND_EXIT)
    assert check.outcome == OUTCOME_FAIL
    assert check.exit_code == 3
    assert check.detail == "the command exited with status 3"
    assert expectations.grade_checks(checks) is False


def test_command_timeout_fails(tmp_path: Path) -> None:
    """A command that outlives ``command_timeout`` fails without an exit code."""
    case = _command_case(
        (sys.executable, "-c", "import time; time.sleep(5)"), timeout=0.1
    )

    checks = expectations.check_case(case, _workspace(tmp_path), (_record(1),))

    check = _by_kind(checks, KIND_COMMAND_EXIT)
    assert check.outcome == OUTCOME_FAIL
    assert check.exit_code is None
    assert check.detail == "the command did not finish within 0.1s"
    assert "exit_code" not in check.to_dict()


def test_command_runs_in_the_pinned_workspace(tmp_path: Path) -> None:
    """The command runs with the run workspace as its working directory."""
    root = _workspace(tmp_path)
    (root / "probe.txt").write_text("probe", encoding="utf-8")
    script = (
        "import pathlib, sys; sys.exit(0 if pathlib.Path('probe.txt').is_file() else 7)"
    )
    case = _command_case((sys.executable, "-c", script))

    checks = expectations.check_case(case, root, (_record(1),))

    assert _by_kind(checks, KIND_COMMAND_EXIT).outcome == OUTCOME_PASS


def test_command_that_cannot_start_fails(tmp_path: Path) -> None:
    """A command that cannot be launched fails without an exit code."""
    case = _command_case(("klea-eval-no-such-command-xyzzy",))

    checks = expectations.check_case(case, _workspace(tmp_path), (_record(1),))

    check = _by_kind(checks, KIND_COMMAND_EXIT)
    assert check.outcome == OUTCOME_FAIL
    assert check.exit_code is None
    assert check.detail is not None
    assert check.detail.startswith("the command could not be started:")


def test_command_is_recorded_with_its_exit_status(tmp_path: Path) -> None:
    """The check records the command and its exit status."""
    command = (sys.executable, "-c", "pass")
    case = _command_case(command)

    checks = expectations.check_case(case, _workspace(tmp_path), (_record(1),))

    check = _by_kind(checks, KIND_COMMAND_EXIT)
    assert check.target == shlex.join(command)
    assert check.turn_number == 1
    assert check.to_dict()["exit_code"] == 0


def test_command_only_case_expects_a_grade() -> None:
    """A case that declares only a command expects a grade."""
    from klea_eval import pipeline

    assert (
        pipeline._expects_grade(_command_case((sys.executable, "-c", "pass"))) is True
    )


# ---------------------------------------------------------------------------
# Grading and request validation
# ---------------------------------------------------------------------------
def test_grade_checks_is_none_without_checks() -> None:
    """No checks grade nothing."""
    assert expectations.grade_checks(()) is None


def test_grade_checks_is_none_when_nothing_is_verifiable() -> None:
    """All-unverifiable checks grade nothing rather than passing."""
    checks = (
        expectations.ExpectationCheck(
            1, KIND_FORMAT_DECODE, "out.yaml", OUTCOME_UNVERIFIABLE, "no parser"
        ),
        expectations.ExpectationCheck(
            2, KIND_TOOL_NAME, "read_file", OUTCOME_UNVERIFIABLE, "no calls"
        ),
    )

    assert expectations.grade_checks(checks) is None


def test_grade_checks_fails_on_any_failure() -> None:
    """One failed check fails the grade even beside a pass."""
    checks = (
        expectations.ExpectationCheck(1, KIND_FILE_PRESENT, "out.txt", OUTCOME_PASS),
        expectations.ExpectationCheck(
            1, KIND_VALIDATION_RULE, "out.txt", OUTCOME_FAIL, "mismatch"
        ),
    )

    assert expectations.grade_checks(checks) is False


def test_check_case_rejects_a_non_path_workspace() -> None:
    """A workspace that is not a path is refused."""
    with pytest.raises(ExpectationError):
        expectations.check_case(_case(_turn()), cast(Any, 42), ())


def test_check_case_rejects_non_iterable_turns(tmp_path: Path) -> None:
    """Retained turn records that are not iterable are refused."""
    root = _workspace(tmp_path)

    with pytest.raises(ExpectationError):
        expectations.check_case(_case(_turn()), root, cast(Any, 42))
