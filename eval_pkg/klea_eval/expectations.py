# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Deterministic checks of a case's expectations against a finished run.

The module owns the machine-checkable half of grading (ADR-0061): it compares
the expectations a case declares -- ``expected_outputs`` and
``tool_expectations`` -- with what a completed run actually left behind, and
records one verdict per check instead of a single opaque pass/fail.

Why it lives apart from :mod:`klea_eval.scoring`: scoring is the read side that
aggregates *stored* evidence and must stay reproducible from ``run.json`` and
``metrics.jsonl`` alone.  This module is the write side, run once while the
case, the run workspace and the retained per-turn records are all in scope; the
verdicts it returns are recorded into the run's metrics envelope so the
aggregate never has to re-read the produced outputs.

Design rules, all deliberate:

- **Read-only across the workspace boundary.**  Every declared output path is
  resolved under the run's ``workspace/`` directory and must stay inside it; an
  absolute or escaping ``file_path`` is a failed check and no path outside the
  workspace is ever opened.  The case directory is never read: only what the
  run produced counts.
- **The case supplies the operand.**  ``expected_outputs`` carries an optional
  ``expected_content`` beside ``file_path``, ``output_format`` and
  ``validation_rule``; when it is present ``exact_match`` compares it with the
  whole decoded file and ``contains`` looks for it as a substring, each
  recording a pass or a fail.  When it is absent the rule has nothing to
  compare with, so the check is ``unverifiable``, which grades nothing rather
  than passing by default.
- **No new dependency.**  ``yaml`` is a declared ``output_format`` but no YAML
  parser is in the harness dependency set, so a YAML output is recorded
  ``unverifiable`` with that detail instead of pulling PyYAML in.
- **Deterministic.**  No clock, no network, no model and no randomisation: the
  same case and the same workspace always produce the same verdicts in the
  same order.
- **Execution is graded on the exit status alone.**  A case may declare its own
  ``command`` (ADR-0056 amendment 2026-11-19); it runs once with the run's
  ``workspace/`` as its working directory, its standard streams are discarded,
  and only its exit status is graded, so anything it produces must be written to
  a file an ``expected_outputs`` entry can check.

Only the standard library and the package's own modules are imported, and no
function here writes a file.
"""

from __future__ import annotations

import json
import logging
import shlex
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from klea_eval import cases, metrics

logger = logging.getLogger(__name__)

__all__ = [
    "KIND_ARGUMENT_CONSTRAINTS",
    "KIND_CALL_COUNT",
    "KIND_COMMAND_EXIT",
    "KIND_FILE_PRESENT",
    "KIND_FORMAT_DECODE",
    "KIND_TOOL_NAME",
    "KIND_VALIDATION_RULE",
    "OUTCOME_FAIL",
    "OUTCOME_PASS",
    "OUTCOME_UNVERIFIABLE",
    "RULE_CONTAINS",
    "RULE_EXACT_MATCH",
    "ExpectationCheck",
    "ExpectationError",
    "check_case",
    "grade_checks",
]

#: Verdict of a check whose expectation the run met.
OUTCOME_PASS = "pass"

#: Verdict of a check whose expectation the run did not meet.
OUTCOME_FAIL = "fail"

#: Verdict of a check the recorded evidence cannot decide either way.
OUTCOME_UNVERIFIABLE = "unverifiable"

#: One check kind per machine-checkable field of the case schema.
KIND_FILE_PRESENT = "file_present"
KIND_FORMAT_DECODE = "format_decode"
KIND_VALIDATION_RULE = "validation_rule"
KIND_TOOL_NAME = "tool_name"
KIND_CALL_COUNT = "call_count"
KIND_ARGUMENT_CONSTRAINTS = "argument_constraints"

#: The case's own ``command``, graded on its exit status alone (ADR-0056
#: amendment 2026-11-19): status ``0`` passes, anything else fails.
KIND_COMMAND_EXIT = "command_exit"

#: ``validation_rule`` values this module applies; each needs an operand.
RULE_EXACT_MATCH = "exact_match"
RULE_CONTAINS = "contains"

#: Call-count range assumed when an expectation carries none, mirroring the
#: case loader's own default so the two can never disagree.
_DEFAULT_CALL_COUNT = (1, 1)

#: Encoding a ``text`` output must decode in to count as well formed.
_TEXT_ENCODING = "utf-8"


class ExpectationError(Exception):
    """Raised when a check request cannot be carried out as given."""


@dataclass(frozen=True, slots=True)
class ExpectationCheck:
    """One machine-checkable verdict recorded for a single case turn.

    ``kind`` names the case field that was checked, ``target`` the specific
    file or tool it applied to, and ``outcome`` one of :data:`OUTCOME_PASS`,
    :data:`OUTCOME_FAIL` or :data:`OUTCOME_UNVERIFIABLE`.  ``detail`` explains a
    failure or an unverifiable outcome and is ``None`` for a plain pass.

    The record is stored in the run's metrics envelope as its :meth:`to_dict`
    form, so a stored run stays gradable without the run workspace: the
    five keys below are the persisted contract, plus an ``exit_code`` that only
    the ``command_exit`` check writes.
    """

    turn_number: int
    kind: str
    target: str
    outcome: str
    detail: str | None = None
    exit_code: int | None = None

    @property
    def identifier(self) -> str:
        """Stable identifier of this check, unique within a case run."""
        return f"turn{self.turn_number}:{self.kind}:{self.target}"

    @property
    def passed(self) -> bool:
        """Whether the run met the expectation."""
        return self.outcome == OUTCOME_PASS

    @property
    def failed(self) -> bool:
        """Whether the run did not meet the expectation."""
        return self.outcome == OUTCOME_FAIL

    @property
    def unverifiable(self) -> bool:
        """Whether the recorded evidence cannot decide the expectation."""
        return self.outcome == OUTCOME_UNVERIFIABLE

    def to_dict(self) -> dict[str, Any]:
        """Return the check as the plain, JSON-ready record that is stored."""
        record: dict[str, Any] = {
            "turn_number": self.turn_number,
            "kind": self.kind,
            "target": self.target,
            "outcome": self.outcome,
            "detail": self.detail,
        }
        if self.exit_code is not None:
            record["exit_code"] = self.exit_code
        return record


def grade_checks(checks: Sequence[ExpectationCheck]) -> bool | None:
    """Reduce the checks of one case to its grade.

    ``True`` only when every check passed, ``False`` as soon as one check
    failed, and ``None`` when any check could not be verified -- a passing check
    never overrides an unverifiable one, and a case with nothing verifiable is
    left ungraded (and so reports grade-missing) rather than passing by default.
    """
    if not checks:
        logger.debug("expectations: no checks to grade")
        return None
    if any(check.failed for check in checks):
        failures = sum(1 for check in checks if check.failed)
        logger.debug(f"{len(checks) = }, {failures = }")
        return False
    if any(check.unverifiable for check in checks):
        unverifiable = sum(1 for check in checks if check.unverifiable)
        logger.debug(f"{len(checks) = }, {unverifiable = }")
        return None
    if all(check.passed for check in checks):
        logger.debug(f"{len(checks) = }, every check passed")
        return True

    logger.debug(f"{len(checks) = }, no check was verifiable")
    return None


def check_case(
    case: cases.TestCase,
    workspace_dir: str | Path,
    turns: Sequence[metrics.TurnMetrics],
) -> tuple[ExpectationCheck, ...]:
    """Check every expectation of ``case`` against one finished run.

    ``workspace_dir`` is the run's ``workspace/`` directory: relative expected
    output paths resolve against it and nothing outside it is ever opened.
    ``turns`` are the retained per-turn records of the run, matched to the
    case's turns by ``turn_number``, from which the recorded tool calls are
    read.

    Every declared expectation yields exactly one verdict per checked field, in
    case order and then declaration order, so the result is stable across
    calls; a case that declares a ``command`` yields one further verdict for it,
    recorded under the last turn's number.  A case that declares no expectation
    yields an empty tuple.

    The ``command`` check runs once with the working directory pinned to
    ``workspace_dir`` and ``shell=False``, discarding standard input, output
    and error; it is never retried.  Exit status ``0`` passes and any non-zero
    status fails, while a timeout or a launch failure fails the check without
    an exit code.
    """
    if not isinstance(workspace_dir, (str, Path)):
        raise ExpectationError(
            f"expected a workspace path, got {type(workspace_dir).__name__}"
        )
    if isinstance(turns, (str, bytes)) or not isinstance(turns, Iterable):
        raise ExpectationError(f"expected the turn records, got {type(turns).__name__}")

    root = Path(workspace_dir).expanduser().resolve()
    retained = tuple(turns)
    by_number = _turns_by_number(retained)
    declared = _case_turns(case)
    logger.debug(f"{root = }, {len(declared) = }, {len(by_number) = }")

    checks: list[ExpectationCheck] = []
    for turn in declared:
        number = _turn_number(turn)
        metrics_of_turn = by_number.get(number)
        for expected in _as_sequence(getattr(turn, "expected_outputs", None)):
            checks.extend(_check_expected_output(number, expected, root))
        for expectation in _as_sequence(getattr(turn, "tool_expectations", None)):
            checks.extend(_check_tool_expectation(number, expectation, metrics_of_turn))

    command = _case_command(case)
    if command:
        checks.append(_check_command(case, command, root))

    case_id = getattr(case, "case_id", "?")
    logger.debug(
        f"{case_id = }, {len(checks) = }, {grade_checks(checks) = }, "
        f"{sum(1 for check in checks if check.unverifiable) = }"
    )
    return tuple(checks)


def _case_turns(case: Any) -> tuple[Any, ...]:
    """Return the turns a case declares, in loader order."""
    declared = getattr(case, "turns", None)
    if declared is None:
        raise ExpectationError("the case does not expose its turns")

    turns = _as_sequence(declared)
    logger.debug(f"{len(turns) = }")
    return turns


def _turns_by_number(retained: Sequence[Any]) -> dict[int, Any]:
    """Index the retained turn records by their ``turn_number``."""
    by_number: dict[int, Any] = {}
    for turn in retained:
        number = getattr(turn, "turn_number", None)
        if isinstance(number, int) and not isinstance(number, bool):
            by_number.setdefault(number, turn)

    logger.debug(f"{sorted(by_number) = }")
    return by_number


def _turn_number(turn: Any) -> int:
    """Return a case turn's number, defaulting to ``0`` when it declares none."""
    number = getattr(turn, "turn_number", None)
    if isinstance(number, int) and not isinstance(number, bool):
        return number

    logger.warning("expectations: a case turn declares no usable turn_number")
    return 0


def _as_sequence(value: Any) -> tuple[Any, ...]:
    """Return ``value`` as a tuple, treating a missing declaration as empty."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Iterable):
        return ()
    return tuple(value)


def _text_field(declaration: Any, name: str) -> str:
    """Return one string field of a case declaration, stripped."""
    if isinstance(declaration, Mapping):
        raw = declaration.get(name)
    else:
        raw = getattr(declaration, name, None)
    return raw.strip() if isinstance(raw, str) else ""


def _check(
    turn_number: int,
    kind: str,
    target: str,
    outcome: str,
    detail: str | None,
    exit_code: int | None = None,
) -> ExpectationCheck:
    """Build one verdict, logging it at the level its outcome deserves."""
    check = ExpectationCheck(turn_number, kind, target, outcome, detail, exit_code)
    if check.failed:
        logger.warning(f"{check.identifier = }, {detail = }")
    else:
        logger.debug(f"{check.identifier = }, {outcome = }, {detail = }")
    return check


def _case_command(case: Any) -> tuple[str, ...]:
    """Return the argv a case declares, or an empty tuple when it declares none."""
    declared = getattr(case, "command", ())
    if isinstance(declared, (str, bytes)) or not isinstance(declared, Iterable):
        return ()

    command = tuple(declared)
    logger.debug(f"{len(command) = }")
    return command


def _check_command(case: Any, command: Sequence[str], root: Path) -> ExpectationCheck:
    """Run a case's own command once in ``root`` and grade its exit status.

    The verdict is the exit status alone (ADR-0056 amendment 2026-11-19): ``0``
    passes, any other status fails, and a command that cannot be started or does
    not finish inside the case's own timeout fails too.  Standard input and both
    output streams are discarded, so a command with something to report must
    write it to a file the case's ``expected_outputs`` can check; nothing is ever
    read back from the process.
    """
    turn_number = len(_case_turns(case))
    target = shlex.join(command)
    timeout = getattr(case, "command_timeout", None)
    logger.debug(f"{target = }, {timeout = }, {root = }")

    try:
        completed = subprocess.run(
            list(command),
            cwd=root,
            timeout=timeout,
            shell=False,
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        return _check(
            turn_number,
            KIND_COMMAND_EXIT,
            target,
            OUTCOME_FAIL,
            f"the command did not finish within {timeout}s",
        )
    except OSError as exc:
        return _check(
            turn_number,
            KIND_COMMAND_EXIT,
            target,
            OUTCOME_FAIL,
            f"the command could not be started: {exc}",
        )

    status = completed.returncode
    if status == 0:
        return _check(
            turn_number, KIND_COMMAND_EXIT, target, OUTCOME_PASS, None, status
        )

    return _check(
        turn_number,
        KIND_COMMAND_EXIT,
        target,
        OUTCOME_FAIL,
        f"the command exited with status {status}",
        status,
    )


def _check_expected_output(
    turn_number: int, expected: Any, root: Path
) -> list[ExpectationCheck]:
    """Resolve, decode and validate one declared expected output.

    The three fields are checked in dependency order: a missing or rejected
    path ends the entry (a format or content check on an absent file would be
    meaningless), a failed decode ends it too (a rule cannot be applied to
    content that could not be read), and only then is the rule evaluated.
    """
    file_path = _text_field(expected, "file_path")
    target = file_path or "<missing file_path>"
    if not file_path:
        return [
            _check(
                turn_number,
                KIND_FILE_PRESENT,
                target,
                OUTCOME_FAIL,
                "the expectation declares no file_path",
            )
        ]

    resolved, reason = _resolve_output(file_path, root)
    if resolved is None:
        return [_check(turn_number, KIND_FILE_PRESENT, target, OUTCOME_FAIL, reason)]

    logger.debug(f"{file_path = }, {resolved = }")
    checks = [_check(turn_number, KIND_FILE_PRESENT, target, OUTCOME_PASS, None)]

    output_format = _text_field(expected, "output_format").lower() or "text"
    decoded, decode_check = _decode_output(turn_number, target, resolved, output_format)
    checks.append(decode_check)
    if not decode_check.passed:
        return checks

    rule = _text_field(expected, "validation_rule").lower()
    if rule:
        checks.append(
            _check_validation_rule(turn_number, target, rule, decoded, expected)
        )

    return checks


def _resolve_output(file_path: str, root: Path) -> tuple[Path | None, str | None]:
    """Resolve an expected output path inside ``root``, or explain why not.

    An absolute path is rejected outright, as is any path whose resolved form
    leaves the run workspace (``..`` components, symlinks); a path that stays
    inside but names no regular file is a failed check too.
    """
    candidate = Path(file_path)
    if candidate.is_absolute():
        return None, "the expected output path is absolute"

    try:
        resolved = (root / candidate).resolve()
    except OSError as exc:
        return None, f"the expected output path cannot be resolved: {exc}"

    if not resolved.is_relative_to(root):
        return None, "the expected output path escapes the run workspace"
    if not resolved.is_file():
        return None, "the run workspace holds no such file"

    return resolved, None


def _decode_output(
    turn_number: int, target: str, path: Path, output_format: str
) -> tuple[Any, ExpectationCheck]:
    """Read and decode one expected output; return ``(content, verdict)``.

    ``json`` content is returned parsed, ``text`` content as the decoded string
    and ``binary`` content as raw bytes.  ``yaml`` is a declared format with no
    parser in the dependency set, so it is recorded unverifiable instead of
    adding a dependency; an unknown format is treated the same way rather than
    silently passing.
    """
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return None, _check(
            turn_number,
            KIND_FORMAT_DECODE,
            target,
            OUTCOME_FAIL,
            f"the expected output could not be read: {exc}",
        )

    logger.debug(f"{path = }, {len(raw) = }, {output_format = }")

    if output_format == "binary":
        return raw, _check(turn_number, KIND_FORMAT_DECODE, target, OUTCOME_PASS, None)

    if output_format == "yaml":
        return None, _check(
            turn_number,
            KIND_FORMAT_DECODE,
            target,
            OUTCOME_UNVERIFIABLE,
            "no YAML parser is in the harness dependency set",
        )

    if output_format not in ("json", "text"):
        return None, _check(
            turn_number,
            KIND_FORMAT_DECODE,
            target,
            OUTCOME_UNVERIFIABLE,
            f"output_format '{output_format}' is not implemented",
        )

    try:
        text = raw.decode(_TEXT_ENCODING)
    except UnicodeDecodeError as exc:
        return None, _check(
            turn_number,
            KIND_FORMAT_DECODE,
            target,
            OUTCOME_FAIL,
            f"the expected output is not valid {_TEXT_ENCODING}: {exc}",
        )

    if output_format == "text":
        return text, _check(turn_number, KIND_FORMAT_DECODE, target, OUTCOME_PASS, None)

    try:
        decoded = json.loads(text)
    except ValueError as exc:
        return None, _check(
            turn_number,
            KIND_FORMAT_DECODE,
            target,
            OUTCOME_FAIL,
            f"the expected output is not valid JSON: {exc}",
        )

    return decoded, _check(turn_number, KIND_FORMAT_DECODE, target, OUTCOME_PASS, None)


def _check_validation_rule(
    turn_number: int, target: str, rule: str, decoded: Any, expected: Any
) -> ExpectationCheck:
    """Apply one ``validation_rule`` to the decoded content of the output."""
    if rule not in (RULE_EXACT_MATCH, RULE_CONTAINS):
        return _check(
            turn_number,
            KIND_VALIDATION_RULE,
            target,
            OUTCOME_UNVERIFIABLE,
            f"validation_rule '{rule}' is not implemented by the checker",
        )

    operand = _expected_content(expected)
    if operand is None:
        return _check(
            turn_number,
            KIND_VALIDATION_RULE,
            target,
            OUTCOME_UNVERIFIABLE,
            f"validation_rule '{rule}' has no expected value in the case schema",
        )

    actual = _as_text(decoded)
    wanted = _as_text(operand)
    if rule == RULE_EXACT_MATCH:
        matched = actual == wanted
    else:
        matched = wanted in actual

    logger.debug(f"{rule = }, {matched = }, {len(actual) = }, {len(wanted) = }")
    return _check(
        turn_number,
        KIND_VALIDATION_RULE,
        target,
        OUTCOME_PASS if matched else OUTCOME_FAIL,
        None if matched else f"validation_rule '{rule}' did not hold for '{target}'",
    )


def _expected_content(expected: Any) -> Any:
    """Return the value a ``validation_rule`` compares the output against.

    ``ExpectedOutput`` carries an optional ``expected_content``: the literal
    text the produced file must equal (``exact_match``) or contain
    (``contains``).  A declaration without it -- or with a non-string one --
    yields ``None``, which keeps the rule unverifiable instead of passing by
    default.  The field is read from a mapping or an object so the checker
    works on both the dataclass and a raw case payload.
    """
    if isinstance(expected, Mapping):
        return expected.get("expected_content")
    return getattr(expected, "expected_content", None)


def _as_text(value: Any) -> str:
    """Render a decoded value as the text a validation rule compares against."""
    if isinstance(value, str):
        return value
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).decode(_TEXT_ENCODING, errors="replace")

    try:
        return json.dumps(value, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _check_tool_expectation(
    turn_number: int, expectation: Any, retained: Any
) -> list[ExpectationCheck]:
    """Check one declared tool expectation against a turn's tool calls.

    Three verdicts follow from one declaration: the tool was called at all, it
    was called within the declared ``[min, max]`` range, and (when constraints
    are declared) at least one call carried them.  When the run retained no
    tool calls at all, the expectation is unverifiable rather than failed: the
    evidence is missing, the call may still have happened.
    """
    tool_name = _text_field(expectation, "tool_name")
    if not tool_name:
        return [
            _check(
                turn_number,
                KIND_TOOL_NAME,
                "<missing tool_name>",
                OUTCOME_FAIL,
                "the expectation declares no tool_name",
            )
        ]

    calls = _tool_calls_of(retained)
    if calls is None:
        return [
            _check(
                turn_number,
                KIND_TOOL_NAME,
                tool_name,
                OUTCOME_UNVERIFIABLE,
                "the run recorded no tool calls for this turn",
            )
        ]

    matching = tuple(call for call in calls if call["tool_name"] == tool_name)
    logger.debug(f"{tool_name = }, {len(calls) = }, {len(matching) = }")

    checks = [
        _check(
            turn_number,
            KIND_TOOL_NAME,
            tool_name,
            OUTCOME_PASS if matching else OUTCOME_FAIL,
            None if matching else f"the run recorded no '{tool_name}' call",
        )
    ]

    low, high = _call_count_range(expectation)
    count = len(matching)
    checks.append(
        _check(
            turn_number,
            KIND_CALL_COUNT,
            tool_name,
            OUTCOME_PASS if low <= count <= high else OUTCOME_FAIL,
            f"{count} call(s), expected {low}..{high}",
        )
    )

    constraints = _constraints(expectation)
    if constraints:
        matched = any(
            _constraints_hold(constraints, call["arguments"]) for call in matching
        )
        checks.append(
            _check(
                turn_number,
                KIND_ARGUMENT_CONSTRAINTS,
                tool_name,
                OUTCOME_PASS if matched else OUTCOME_FAIL,
                None if matched else "no matching call carried the declared arguments",
            )
        )

    return checks


def _tool_calls_of(turn: Any) -> tuple[dict[str, Any], ...] | None:
    """Return the tool calls retained for one turn, or ``None`` if unrecorded.

    The collector keeps them on the turn record when the runner reported them;
    a record written before that field existed, or a turn whose executor
    reported none, yields ``None`` so the tool checks report unverifiable
    rather than a fabricated empty call list.
    """
    if turn is None:
        return None

    raw = getattr(turn, "tool_calls", None)
    if raw is None and isinstance(turn, Mapping):
        raw = turn.get("tool_calls")
    if raw is None or isinstance(raw, (str, bytes)) or not isinstance(raw, Iterable):
        return None

    calls: list[dict[str, Any]] = []
    for entry in raw:
        call = _tool_call(entry)
        if call is not None:
            calls.append(call)

    logger.debug(f"{len(calls) = }")
    return tuple(calls)


def _tool_call(entry: Any) -> dict[str, Any] | None:
    """Normalise one recorded tool call to a name plus its arguments.

    The stored entries name the tool as ``tool`` (the runner's own shape) or
    ``tool_name`` (the case schema's shape); both are accepted.  An entry that
    names no tool cannot be matched and is dropped.
    """
    if isinstance(entry, Mapping):
        name = entry.get("tool_name", entry.get("tool"))
        arguments = entry.get("arguments", entry.get("args"))
    else:
        name = getattr(entry, "tool_name", None) or getattr(entry, "tool", None)
        arguments = getattr(entry, "arguments", None) or getattr(entry, "args", None)

    if not isinstance(name, str) or not name.strip():
        return None

    return {
        "tool_name": name.strip(),
        "arguments": arguments if isinstance(arguments, Mapping) else None,
    }


def _constraints(expectation: Any) -> dict[str, Any]:
    """Return the argument constraints declared by one expectation."""
    if isinstance(expectation, Mapping):
        raw = expectation.get("argument_constraints")
    else:
        raw = getattr(expectation, "argument_constraints", None)

    if not isinstance(raw, Mapping):
        return {}
    return dict(raw)


def _constraints_hold(
    constraints: Mapping[str, Any], arguments: Mapping[str, Any] | None
) -> bool:
    """Return whether a call's arguments match every declared constraint.

    The match is an exact subset: every declared key must be present with an
    equal value, nested mappings are compared the same way, and extra keys are
    allowed because a call may pass more than the case pinned.
    """
    if not isinstance(arguments, Mapping):
        return False

    return all(
        key in arguments and _value_matches(value, arguments[key])
        for key, value in constraints.items()
    )


def _value_matches(expected: Any, actual: Any) -> bool:
    """Return whether ``actual`` matches one constrained value."""
    if isinstance(expected, Mapping):
        return _constraints_hold(expected, actual)
    return expected == actual


def _call_count_range(expectation: Any) -> tuple[int, int]:
    """Return the inclusive ``[min, max]`` call count of one expectation."""
    if isinstance(expectation, Mapping):
        raw = expectation.get("call_count")
    else:
        raw = getattr(expectation, "call_count", None)

    if isinstance(raw, (tuple, list)) and len(raw) == 2:
        low, high = raw
        if (
            isinstance(low, int)
            and isinstance(high, int)
            and not isinstance(low, bool)
            and not isinstance(high, bool)
            and low <= high
        ):
            return int(low), int(high)

    logger.debug(f"{raw = }, falling back to {_DEFAULT_CALL_COUNT = }")
    return _DEFAULT_CALL_COUNT
