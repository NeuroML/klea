# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Metrics collection and per-run aggregation for the eval harness.

The module is the read side of the ADR-0058 metrics contract.  The run manager
(:mod:`klea_eval.run_manager`) appends one JSON object per line to
``metrics.jsonl`` at the run root; this module parses those lines, classifies
every record and aggregates the records of a single run into a
:class:`RunMetrics` summary that the scoring and reporting stages consume.

A record is classified as one of:

- a run envelope: a mapping whose ``record_type`` is ``"run"``, or one that
  carries a ``metrics`` mapping (ADR-0058);
- a turn record: a mapping carrying ``turn_number``;
- a failure line: a mapping with ``status == "failed"`` and no ``turn_number``;
- anything else is ignored with a debug message.

The envelope is optional today, because writing it belongs to the ADR-0059
writer; when it is absent the run-level values are recomputed from the turn
lines and ``envelope_present`` stays false.

The module only reads: it never writes a file and never imports
:mod:`klea_eval.run_manager`, so it works on a published run, on a ``.tmp`` run
under construction and on a hand-written fixture alike.

Known divergences from the ADR-0058 contract, all tolerated and logged rather
than repaired here:

- per-turn lines do not yet carry ``schema_version`` or ``record_type``, so the
  record type is inferred from the payload and ``schema_version`` defaults to
  :data:`SCHEMA_VERSION`;
- no price table exists yet, so ``cost_estimate`` is read from the envelope when
  present and stays ``None`` otherwise.

``pass_at_k`` is never produced here: ADR-0058 makes it a suite-level aggregate
of the ADR-0060 stage, not a per-run field.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klea_eval import workspace

logger = logging.getLogger(__name__)

__all__ = [
    "RECORD_TYPE_RUN",
    "RECORD_TYPE_TURN",
    "SCHEMA_VERSION",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "SUPPORTED_SCHEMA_VERSIONS",
    "MetricsError",
    "MetricsSchemaError",
    "RunMetrics",
    "TurnMetrics",
    "aggregate",
    "compare_runs",
    "load_run",
    "read_records",
]

#: Version stamped on the records this module understands.
SCHEMA_VERSION = "1.0"

#: Schema versions the collector accepts; anything else is rejected.
SUPPORTED_SCHEMA_VERSIONS = frozenset({SCHEMA_VERSION})

#: ``record_type`` of a per-turn record.
RECORD_TYPE_TURN = "turn"

#: ``record_type`` of the final per-run envelope.
RECORD_TYPE_RUN = "run"

#: Status of a turn or run that produced its output.
STATUS_COMPLETED = "completed"

#: Status of a turn or run that failed.
STATUS_FAILED = "failed"


class MetricsError(Exception):
    """Base error raised while reading or aggregating metrics records."""


class MetricsSchemaError(MetricsError):
    """Raised when a record carries an unsupported schema version."""


@dataclass(frozen=True, slots=True)
class TurnMetrics:
    """One aggregated per-turn record."""

    turn_number: int
    role: str | None = None
    status: str = STATUS_COMPLETED
    attempts: int | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    latency_ms: float | None = None
    model: str | None = None
    passed: bool | None = None
    score: dict[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class RunMetrics:
    """The per-run aggregate recomputed from one run's records."""

    run_id: str
    case_id: str
    schema_version: str = SCHEMA_VERSION
    status: str = STATUS_COMPLETED
    record_count: int = 0
    turn_count: int = 0
    task_success: bool | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    total_latency_ms: float | None = None
    tool_error_count: int = 0
    cost_estimate: float | None = None
    turns: tuple[TurnMetrics, ...] = ()
    error: str | None = None
    envelope_present: bool = False


def read_records(path: str | Path) -> tuple[dict[str, Any], ...]:
    """Parse ``path`` into one mapping per JSON line, in file order.

    Blank lines are ignored.  A line that is not parsable JSON, or that does
    not hold a JSON object, is skipped with a logged warning: a corrupted line
    cannot be repaired by the reader, and ADR-0058's completeness invariant is
    enforced at write time.  A missing or unreadable file raises
    :class:`MetricsError`.
    """
    if not isinstance(path, (str, Path)):
        raise MetricsError(f"expected a path, got {type(path).__name__}")

    target = Path(path)
    logger.debug(f"{target = }")

    records: list[dict[str, Any]] = []
    try:
        with target.open("r", encoding="utf-8", errors="replace") as handle:
            for number, line in enumerate(handle, start=1):
                text = line.strip()
                if not text:
                    continue
                try:
                    value = json.loads(text)
                except json.JSONDecodeError as error:
                    logger.warning(
                        "metrics: skipping unparsable line %d of %s (%s)",
                        number,
                        target,
                        error,
                    )
                    continue
                if not isinstance(value, Mapping):
                    logger.warning(
                        "metrics: skipping non-object line %d of %s, got %s",
                        number,
                        target,
                        type(value).__name__,
                    )
                    continue
                records.append(dict(value))
    except OSError as error:
        raise MetricsError(
            f"could not read metrics from '{target}': {error}"
        ) from error

    logger.debug(f"{target = }, {len(records) = }")
    return tuple(records)


def aggregate(
    records: Iterable[Mapping[str, Any]],
    *,
    run_id: str | None = None,
    case_id: str | None = None,
) -> RunMetrics:
    """Aggregate the parsed records of one run into a :class:`RunMetrics`.

    The result is deterministic: turns are ordered by ``turn_number`` and, for
    equal numbers, by their position in ``records``, so the same transcript in
    any line order yields the same summary.  Run-level values are recomputed
    from the turn records; a run envelope, when present, only contributes the
    values that cannot be derived (``cost_estimate``, ``status``, a declared
    ``task_success``) and is checked against the recomputed totals.
    """
    logger.debug(f"{run_id = }, {case_id = }")

    if isinstance(records, (str, bytes, Mapping)):
        raise MetricsError(
            f"expected an iterable of metric records, got {type(records).__name__}"
        )
    try:
        iterable = iter(records)
    except TypeError as error:
        raise MetricsError(
            f"expected an iterable of metric records, got {type(records).__name__}"
        ) from error

    rows: list[dict[str, Any]] = []
    for index, row in enumerate(iterable):
        if not isinstance(row, Mapping):
            logger.warning(
                "metrics: skipping record %d, expected a mapping, got %s",
                index,
                type(row).__name__,
            )
            continue
        rows.append(dict(row))

    logger.debug(f"{len(rows) = }")

    turn_rows: list[tuple[int, int, dict[str, Any]]] = []
    envelopes: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []

    for index, row in enumerate(rows):
        kind = _classify(row)
        if kind == "run":
            envelopes.append(row)
        elif kind == "turn":
            number = _as_int(row.get("turn_number"), None)
            if number is None:
                logger.warning(
                    "metrics: skipping a turn record with an unusable turn_number (%r)",
                    row.get("turn_number"),
                )
                continue
            turn_rows.append((number, index, row))
        elif kind == "failure":
            failures.append(row)
        else:
            logger.debug(f"skipping record {index} of kind {kind!r}")

    turn_rows.sort(key=lambda item: item[0])

    envelope = envelopes[-1] if envelopes else None
    failure = failures[-1] if failures else None
    logger.debug(f"{len(turn_rows) = }, {len(envelopes) = }, {len(failures) = }")

    fallback_run_id: str | None = None
    fallback_case_id: str | None = None
    for row in rows:
        if fallback_run_id is None:
            fallback_run_id = _text_or_none(row.get("run_id"))
        if fallback_case_id is None:
            fallback_case_id = _text_or_none(row.get("case_id"))
        if fallback_run_id is not None and fallback_case_id is not None:
            break

    resolved_run_id = _text_or_none(run_id) or fallback_run_id
    resolved_case_id = _text_or_none(case_id) or fallback_case_id
    if resolved_run_id is None:
        raise MetricsError("could not resolve a run_id from the records")
    if resolved_case_id is None:
        raise MetricsError(f"could not resolve a case_id for run '{resolved_run_id}'")

    schema_version = _resolve_schema_version(envelope, resolved_run_id)
    status, error = _resolve_status(envelope, failure)

    turns = tuple(_turn_metrics(row) for _, _, row in turn_rows)

    total_input = sum(turn.input_tokens for turn in turns)
    total_output = sum(turn.output_tokens for turn in turns)
    total_cached = sum(turn.cached_tokens for turn in turns)
    latencies = [turn.latency_ms for turn in turns if turn.latency_ms is not None]
    total_latency = float(sum(latencies)) if latencies else None
    tool_errors = sum(_tool_error_count(row) for _, _, row in turn_rows)

    envelope_metrics = _envelope_metrics(envelope)
    declared = _declared_totals(envelope_metrics)
    if turns:
        _warn_on_mismatch(resolved_run_id, "input", total_input, declared.get("input"))
        _warn_on_mismatch(
            resolved_run_id, "output", total_output, declared.get("output")
        )
        _warn_on_mismatch(
            resolved_run_id, "cached", total_cached, declared.get("cached")
        )
    else:
        total_input = declared.get("input") or 0
        total_output = declared.get("output") or 0
        total_cached = declared.get("cached") or 0
        if not tool_errors:
            tool_errors = sum(
                _tool_error_count(row) for row in (envelope_metrics or {},)
            )

    task_success = _envelope_task_success(envelope, envelope_metrics)
    if task_success is None and turns:
        flags = [turn.passed for turn in turns]
        if all(flag is not None for flag in flags):
            task_success = all(flags)

    cost_estimate = None
    if envelope_metrics is not None:
        raw_cost = envelope_metrics.get("cost_estimate")
        if raw_cost is None and envelope is not None:
            raw_cost = envelope.get("cost_estimate")
        cost_estimate = _as_float(raw_cost, None)

    summary = RunMetrics(
        run_id=resolved_run_id,
        case_id=resolved_case_id,
        schema_version=schema_version,
        status=status,
        record_count=len(rows),
        turn_count=len(turns),
        task_success=task_success,
        total_input_tokens=total_input,
        total_output_tokens=total_output,
        total_cached_tokens=total_cached,
        total_latency_ms=total_latency,
        tool_error_count=tool_errors,
        cost_estimate=cost_estimate,
        turns=turns,
        error=error,
        envelope_present=envelope is not None,
    )
    logger.debug(
        f"{summary.run_id = }, {summary.turn_count = }, "
        f"{summary.task_success = }, {summary.status = }"
    )
    return summary


def load_run(metrics_file: str | Path) -> RunMetrics:
    """Read and aggregate one run's metrics.

    ``metrics_file`` is either the ``metrics.jsonl`` file itself or the run
    directory that holds it, so a published run and a ``.tmp`` run under
    construction are both accepted.
    """
    if not isinstance(metrics_file, (str, Path)):
        raise MetricsError(f"expected a path, got {type(metrics_file).__name__}")

    target = Path(metrics_file)
    if target.is_dir():
        target = Path(workspace.metrics_path(target))
    logger.debug(f"{target = }")

    summary = aggregate(read_records(target))
    logger.debug(f"{target = }, {summary.run_id = }, {summary.turn_count = }")
    return summary


def compare_runs(*runs: RunMetrics) -> tuple[dict[str, Any], ...]:
    """Build one flat summary row per run, in argument order.

    The rows feed the ``task x model`` grid of the reporter.  Latency is
    deliberately excluded: ADR-0058 declares it non-comparable across runs.
    """
    rows: list[dict[str, Any]] = []
    for run in runs:
        if not isinstance(run, RunMetrics):
            raise MetricsError(f"expected a RunMetrics, got {type(run).__name__}")
        rows.append(
            {
                "run_id": run.run_id,
                "case_id": run.case_id,
                "status": run.status,
                "task_success": run.task_success,
                "turn_count": run.turn_count,
                "total_input_tokens": run.total_input_tokens,
                "total_output_tokens": run.total_output_tokens,
                "total_cached_tokens": run.total_cached_tokens,
                "cost_estimate": run.cost_estimate,
            }
        )

    logger.debug(f"{len(rows) = }")
    return tuple(rows)


def _classify(record: Mapping[str, Any]) -> str:
    """Return ``"run"``, ``"turn"``, ``"failure"`` or ``"unknown"``."""
    record_type = _text_or_none(record.get("record_type"))
    if record_type is not None and record_type.lower() == RECORD_TYPE_RUN:
        return "run"
    if isinstance(record.get("metrics"), Mapping):
        return "run"
    if record.get("turn_number") is not None:
        return "turn"
    if _status_text(record.get("status")) == STATUS_FAILED:
        return "failure"
    return "unknown"


def _turn_metrics(record: Mapping[str, Any]) -> TurnMetrics:
    """Build one :class:`TurnMetrics` from a per-turn record."""
    tokens = record.get("token_usage")
    model: str | None = None
    if isinstance(tokens, Mapping):
        input_tokens = _as_int_or(tokens.get("input_tokens"), 0)
        output_tokens = _as_int_or(tokens.get("output_tokens"), 0)
        cached_tokens = _as_int_or(tokens.get("cached_tokens"), 0)
        model = _text_or_none(tokens.get("model"))
    else:
        input_tokens = _as_int_or(record.get("input_tokens"), 0)
        output_tokens = _as_int_or(record.get("output_tokens"), 0)
        cached_tokens = _as_int_or(record.get("cached_tokens"), 0)
    model = model or _text_or_none(record.get("model"))

    raw_score = record.get("score")
    score = dict(raw_score) if isinstance(raw_score, Mapping) else None

    return TurnMetrics(
        turn_number=_as_int_or(record.get("turn_number"), 0),
        role=_text_or_none(record.get("role")),
        status=_status_text(record.get("status")) or STATUS_COMPLETED,
        attempts=_as_int(record.get("attempts"), None),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cached_tokens=cached_tokens,
        latency_ms=_as_float(record.get("latency_ms"), None),
        model=model,
        passed=_pass_flag(record, score),
        score=score,
    )


def _pass_flag(
    record: Mapping[str, Any], score: Mapping[str, Any] | None
) -> bool | None:
    """Read the per-turn pass flag from the score payload or the record."""
    for source in (score, record):
        if not isinstance(source, Mapping):
            continue
        for key in ("pass_fail", "passed"):
            value = source.get(key)
            if isinstance(value, bool):
                return value
    return None


def _tool_error_count(record: Mapping[str, Any]) -> int:
    """Count failed tool calls in one record."""
    if not isinstance(record, Mapping):
        return 0

    errors = 0
    calls = record.get("tool_calls")
    if isinstance(calls, Iterable) and not isinstance(calls, (str, bytes, Mapping)):
        for call in calls:
            if isinstance(call, Mapping) and call.get("error") is not None:
                errors += 1

    if errors:
        return errors
    return _as_int_or(record.get("tool_error_count"), 0)


def _envelope_metrics(
    envelope: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Return the envelope's ``metrics`` mapping, ``_primary``/``_secondary``
    groups folded in, or ``None`` when there is no envelope."""
    if envelope is None:
        return None
    metrics = envelope.get("metrics")
    if not isinstance(metrics, Mapping):
        return None

    merged = dict(metrics)
    for group in ("_primary", "_secondary"):
        nested = metrics.get(group)
        if isinstance(nested, Mapping):
            for key, value in nested.items():
                merged.setdefault(key, value)
    return merged


def _declared_totals(metrics: Mapping[str, Any] | None) -> dict[str, int | None]:
    """Read the token totals an envelope declares, if any."""
    totals: dict[str, int | None] = {"input": None, "output": None, "cached": None}
    if metrics is None:
        return totals

    tokens = metrics.get("token_usage")
    if isinstance(tokens, Mapping):
        totals["input"] = _as_int(tokens.get("total_input_tokens"), None)
        totals["output"] = _as_int(tokens.get("total_output_tokens"), None)
        totals["cached"] = _as_int(tokens.get("total_cached_tokens"), None)
    return totals


def _envelope_task_success(
    envelope: Mapping[str, Any] | None, metrics: Mapping[str, Any] | None
) -> bool | None:
    """Read the ``task_success`` an envelope declares, if any."""
    candidates: list[Any] = []
    if metrics is not None:
        candidates.append(metrics.get("task_success"))
        evaluation = metrics.get("evaluation_results")
        if isinstance(evaluation, Mapping):
            candidates.append(evaluation.get("task_success"))
    if envelope is not None:
        candidates.append(envelope.get("task_success"))

    for value in candidates:
        if isinstance(value, bool):
            return value
    return None


def _resolve_schema_version(envelope: Mapping[str, Any] | None, run_id: str) -> str:
    """Return the envelope's schema version, defaulting to the current one."""
    if envelope is None:
        return SCHEMA_VERSION

    raw = envelope.get("schema_version")
    if raw is None:
        logger.debug(f"run '{run_id}' has no schema_version, assuming {SCHEMA_VERSION}")
        return SCHEMA_VERSION

    text = str(raw).strip()
    if text not in SUPPORTED_SCHEMA_VERSIONS:
        raise MetricsSchemaError(
            f"unsupported metrics schema version '{text}' for run '{run_id}'"
        )
    return text


def _resolve_status(
    envelope: Mapping[str, Any] | None, failure: Mapping[str, Any] | None
) -> tuple[str, str | None]:
    """Return the run status and error text from the failure line or envelope."""
    if failure is not None:
        status = _status_text(failure.get("status")) or STATUS_FAILED
        raw_error = failure.get("error")
        return status, str(raw_error) if raw_error is not None else None

    if envelope is not None:
        status = _status_text(envelope.get("status")) or STATUS_COMPLETED
        raw_error = envelope.get("error")
        return status, str(raw_error) if raw_error is not None else None

    return STATUS_COMPLETED, None


def _warn_on_mismatch(
    run_id: str, label: str, recomputed: int, declared: int | None
) -> None:
    """Warn when an envelope's declared total disagrees with the turns."""
    if declared is None or declared == recomputed:
        return
    logger.warning(
        "metrics: run %s declares %s total %d but its turn records sum to %d; "
        "using the recomputed value",
        run_id,
        label,
        declared,
        recomputed,
    )


def _status_text(value: Any) -> str:
    """Normalise a status value to lowercase text."""
    if value is None:
        return ""
    return str(getattr(value, "value", value)).strip().lower()


def _text_or_none(value: Any) -> str | None:
    """Return ``value`` as stripped text, or ``None`` when it is empty."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_int(value: Any, default: int | None) -> int | None:
    """Coerce ``value`` to an integer, falling back to ``default``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return int(text)
        except ValueError:
            try:
                return int(float(text))
            except ValueError:
                logger.warning(f"metrics: {value!r} is not an integer")
                return default
    logger.warning(f"metrics: {value!r} is not an integer")
    return default


def _as_float(value: Any, default: float | None) -> float | None:
    """Coerce ``value`` to a float, falling back to ``default``."""
    if value is None:
        return default
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return default
        try:
            return float(text)
        except ValueError:
            logger.warning(f"metrics: {value!r} is not a number")
            return default
    logger.warning(f"metrics: {value!r} is not a number")
    return default


def _as_int_or(value: Any, default: int) -> int:
    """Like :func:`_as_int`, but always returns an ``int``."""
    result = _as_int(value, default)
    return default if result is None else result
