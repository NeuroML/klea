# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Run manager: prepare one evaluation run, execute its turns, publish it.

The manager sits between the case loader (:mod:`klea_eval.cases`), the
deterministic identity derivation (:mod:`klea_eval.identity`) and the run
workspace layer (:mod:`klea_eval.workspace`).  It never touches the network
and never calls a model: turn execution and scoring are injected callables, so
the same code serves the live harness and the offline tests.

Lifecycle of one run:

1. :func:`start_run` derives the deterministic identifier triple from
   ``case_id`` and the harness seed through
   :func:`klea_eval.identity.derive_identity`, prepares the run directory
   through :func:`klea_eval.workspace.prepare_run` (which also clears any
   leftover staging directory), copies the declared fixtures into
   ``<run dir>/fixtures/`` (the originals stay untouched) and returns a
   :class:`RunContext`.
2. :func:`run_case` walks ``case.turns`` in loader order (the loader already
   enforces 1-based, contiguous ``turn_number`` values, so nothing is sorted
   here), calls the injected ``execute_turn`` per turn with a bounded retry
   loop, optionally calls the injected ``score_turn``, and appends one metric
   line per turn through :func:`append_metric`.  The number of attempts a turn
   needed is recorded as ``attempts`` on its metric line.
3. A finished run is published through
   :func:`klea_eval.workspace.publish_run`; a failed run is discarded through
   :func:`klea_eval.workspace.discard_run` so that no half-written run is ever
   visible.  A run that is already complete is skipped, leaving its directory
   untouched, unless the caller asks for ``overwrite=True``.

``run.json``, the run index, retention and suite aggregation are deliberately
out of scope for this module.  Only the standard library is imported here.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from klea_eval import workspace
from klea_eval.cases import TestCase, Turn
from klea_eval.identity import Identity, derive_identity

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_HARNESS_SEED",
    "DEFAULT_MAX_ATTEMPTS",
    "FIXTURE_DIR_NAME",
    "RETRY_BACKOFF_SECONDS",
    "STATUS_COMPLETED",
    "STATUS_FAILED",
    "STATUS_SKIPPED",
    "RunContext",
    "RunManagerError",
    "RunPreparationError",
    "RunResult",
    "TurnExecutor",
    "TurnScorer",
    "append_metric",
    "run_case",
    "start_run",
]

#: Seed used when neither the caller nor the case run configuration sets one.
DEFAULT_HARNESS_SEED = "0"

#: Number of attempts a single turn gets before the run is marked failed.
DEFAULT_MAX_ATTEMPTS = 3

#: Base delay, in seconds, between two attempts at the same turn.
RETRY_BACKOFF_SECONDS = 0.5

#: Sub-directory of a run workspace that receives the copied fixtures.
FIXTURE_DIR_NAME = "fixtures"

#: Status recorded for a turn that produced output.
STATUS_COMPLETED = "completed"

#: Status recorded when a run (or turn) fails.
STATUS_FAILED = "failed"

#: Status recorded when an already complete run is left untouched.
STATUS_SKIPPED = "skipped"

#: Workspace states that mean the run directory already holds a finished run.
_SKIPPABLE_STATES = frozenset({"complete", "completed", "published"})


class RunManagerError(Exception):
    """Base class for failures raised by the run manager."""


class RunPreparationError(RunManagerError):
    """Raised when a run cannot be prepared from its case definition."""


class _TurnFailure(Exception):
    """Carries the last error of a turn and how many attempts it took."""

    def __init__(self, error: BaseException, attempts: int) -> None:
        super().__init__(str(error) or type(error).__name__)
        self.error = error
        self.attempts = attempts


@dataclass(frozen=True, slots=True)
class RunContext:
    """Everything one prepared run needs before its turns execute."""

    identity: Identity
    harness_seed: str
    workspace_root: Path
    run_dir: Path
    fixture_base: Path
    fixtures: dict[str, Any] | None = None
    fixture_sources: dict[str, Any] | None = None
    model_overrides: dict[str, Any] | None = None

    @property
    def case_id(self) -> str:
        """The case this run was prepared for."""
        return self.identity.case_id

    @property
    def run_id(self) -> str:
        """The deterministic run identifier."""
        return self.identity.run_id

    @property
    def user_id(self) -> str:
        """The deterministic user identifier."""
        return self.identity.user_id

    @property
    def chat_id(self) -> str:
        """The deterministic chat identifier."""
        return self.identity.chat_id

    @property
    def fixture_dir(self) -> Path:
        """Directory inside the run workspace that holds the copied fixtures."""
        return self.run_dir / FIXTURE_DIR_NAME

    @property
    def metrics_file(self) -> Path:
        """Path of the run's ``metrics.jsonl`` file."""
        return Path(workspace.metrics_path(self.run_dir))


@dataclass(frozen=True, slots=True)
class RunResult:
    """The outcome of executing one prepared run."""

    context: RunContext
    status: str
    metrics: tuple[dict[str, Any], ...] = ()
    output_dir: Path | None = None
    error: str | None = None


#: Executes one turn and returns the metric record describing it.
TurnExecutor = Callable[[RunContext, Turn], Mapping[str, Any] | None]

#: Scores one executed turn; returns the score payload to embed in the record.
TurnScorer = Callable[[RunContext, Turn, Mapping[str, Any]], Mapping[str, Any] | None]


def start_run(
    case: TestCase,
    fixture_base: str | Path,
    workspace_root: str | Path,
    harness_seed: str | int | None = None,
    *,
    model_overrides: Mapping[str, str] | None = None,
    endpoint: str | None = None,
) -> RunContext:
    """Derive the run identity and prepare an empty run workspace.

    ``fixture_base`` is the directory that the case's relative fixture paths
    resolve against, normally ``load_suite(path).source.parent``: the case
    object does not carry its owning suite file, so the caller supplies it and
    the current working directory is never guessed.  Every declared fixture is
    copied into ``<run dir>/fixtures/``; the originals are left untouched.
    """
    if not isinstance(case, TestCase):
        raise RunPreparationError(f"expected a TestCase, got {type(case).__name__}")

    base = Path(fixture_base).resolve()
    root = Path(workspace_root).resolve()
    seed = _resolve_harness_seed(case, harness_seed)
    overrides = _merge_model_overrides(model_overrides, _resolve_model_overrides(case))
    identity = derive_identity(
        case.case_id,
        seed,
        model_overrides=overrides,
        endpoint=endpoint,
    )
    sources = _resolve_fixtures(case.fixtures, base, case.case_id)

    prepared = workspace.prepare_run(identity.run_id, root)
    run_dir = Path(prepared) if prepared is not None else root / identity.run_id
    copied = _copy_fixtures(sources, base, run_dir / FIXTURE_DIR_NAME, case.case_id)

    context = RunContext(
        identity=identity,
        harness_seed=seed,
        workspace_root=root,
        run_dir=run_dir,
        fixture_base=base,
        fixtures=copied,
        fixture_sources=sources,
        model_overrides=overrides,
    )
    logger.debug(
        "run_manager: %s %s %s",
        f"{identity = }",
        f"{run_dir = }",
        f"{len(copied or {}) = }",
    )
    return context


def run_case(
    context: RunContext,
    case: TestCase,
    execute_turn: TurnExecutor,
    score_turn: TurnScorer | None = None,
    sleep: Callable[[float], None] | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    overwrite: bool = False,
) -> RunResult:
    """Execute every turn of ``case`` in the prepared run ``context``.

    Each turn is attempted up to ``max_attempts`` times; ``sleep`` is called
    with the backoff delay between attempts and defaults to :func:`time.sleep`.
    One metric line is appended per successful turn, carrying the number of
    attempts it needed as ``attempts``.

    A run whose directory already holds a finished run is skipped (the
    directory is left untouched) unless ``overwrite`` is true.  On success the
    run directory is published; on failure the failure is recorded, the run
    directory is discarded and a :class:`RunResult` with :data:`STATUS_FAILED`
    is returned.
    """
    if not isinstance(context, RunContext):
        raise RunManagerError(f"expected a RunContext, got {type(context).__name__}")
    if not isinstance(case, TestCase):
        raise RunPreparationError(f"expected a TestCase, got {type(case).__name__}")
    if case.case_id != context.case_id:
        raise RunPreparationError(
            f"case '{case.case_id}' does not match the prepared run for "
            f"'{context.case_id}'"
        )
    if not callable(execute_turn):
        raise RunManagerError("execute_turn must be callable")
    if score_turn is not None and not callable(score_turn):
        raise RunManagerError("score_turn must be callable or None")
    if sleep is not None and not callable(sleep):
        raise RunManagerError("sleep must be callable or None")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise RunManagerError("max_attempts must be an integer")
    if max_attempts < 1:
        raise RunManagerError("max_attempts must be at least 1")
    if not isinstance(overwrite, bool):
        raise RunManagerError("overwrite must be a boolean")

    state = _state_text(workspace.detect_state(context.run_id, context.workspace_root))
    logger.debug(
        "run_manager: %s %s %s",
        f"{context.run_id = }",
        f"{state = }",
        f"{overwrite = }",
    )

    if state in _SKIPPABLE_STATES:
        if not overwrite:
            logger.info(
                "run_manager: run %s is already complete, skipping it",
                context.run_id,
            )
            return RunResult(
                context=context,
                status=STATUS_SKIPPED,
                metrics=(),
                output_dir=context.run_dir,
                error=None,
            )
        context = _reprepare(context)
        logger.debug("run_manager: %s", f"{context.run_dir = }")

    metrics_file = context.metrics_file
    records: list[dict[str, Any]] = []
    logger.debug(
        "run_manager: %s %s %s",
        f"{context.run_id = }",
        f"{case.case_id = }",
        f"{len(case.turns) = }",
    )

    try:
        for turn in case.turns:
            record = _execute_one(
                context, turn, execute_turn, score_turn, sleep, max_attempts
            )
            append_metric(metrics_file, record)
            records.append(record)
    except Exception as exc:
        attempts: int | None = None
        cause = exc
        if isinstance(exc, _TurnFailure):
            attempts = exc.attempts
            cause = exc.error
        error = f"{type(cause).__name__}: {cause}"
        logger.exception("run_manager: run %s failed during its turns", context.run_id)
        failure: dict[str, Any] = {
            "case_id": context.case_id,
            "run_id": context.run_id,
            "status": STATUS_FAILED,
            "error": error,
        }
        if attempts is not None:
            failure["attempts"] = attempts
        try:
            append_metric(metrics_file, failure)
        except RunManagerError:
            logger.warning(
                "run_manager: could not record the failure metric for run %s",
                context.run_id,
            )
        workspace.discard_run(context.run_id, context.workspace_root)
        return RunResult(
            context=context,
            status=STATUS_FAILED,
            metrics=tuple(records),
            output_dir=None,
            error=error,
        )

    published = workspace.publish_run(context.run_id, context.workspace_root)
    output_dir = Path(published) if published is not None else context.run_dir
    logger.debug(
        "run_manager: %s %s %s",
        f"{context.run_id = }",
        f"{output_dir = }",
        f"{len(records) = }",
    )
    return RunResult(
        context=context,
        status=STATUS_COMPLETED,
        metrics=tuple(records),
        output_dir=output_dir,
        error=None,
    )


def append_metric(path: str | Path, record: Mapping[str, Any]) -> None:
    """Append ``record`` as one JSON object on its own line of ``path``.

    The file is opened with ``O_APPEND`` and flushed with :func:`os.fsync`, so
    concurrent writers never truncate earlier lines and a crash cannot leave a
    half-written record inside another one.
    """
    if not isinstance(record, Mapping):
        raise RunManagerError(
            f"metric record must be a mapping, got {type(record).__name__}"
        )

    target = Path(path)
    try:
        payload = json.dumps(dict(record), sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise RunManagerError(
            f"metric record is not JSON serialisable for '{target}': {exc}"
        ) from exc

    line = f"{payload}\n".encode()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise RunManagerError(
            f"could not append a metric to '{target}': {exc}"
        ) from exc

    logger.debug("run_manager: %s", f"{target = }")


def _reprepare(context: RunContext) -> RunContext:
    """Rebuild the run workspace for an overwrite of a finished run."""
    prepared = workspace.prepare_run(context.run_id, context.workspace_root)
    run_dir = (
        Path(prepared)
        if prepared is not None
        else context.workspace_root / context.run_id
    )
    copied = _copy_fixtures(
        context.fixture_sources,
        context.fixture_base,
        run_dir / FIXTURE_DIR_NAME,
        context.case_id,
    )
    return replace(context, run_dir=run_dir, fixtures=copied)


def _execute_one(
    context: RunContext,
    turn: Turn,
    execute_turn: TurnExecutor,
    score_turn: TurnScorer | None,
    sleep: Callable[[float], None] | None,
    max_attempts: int,
) -> dict[str, Any]:
    """Run a single turn with retries and build its metric record."""
    sleeper = sleep if sleep is not None else time.sleep
    attempts = 0

    while True:
        attempts += 1
        logger.debug(
            "run_manager: %s %s %s",
            f"{turn.turn_number = }",
            f"{turn.role = }",
            f"{attempts = }",
        )
        try:
            raw = execute_turn(context, turn)
        except Exception as exc:
            if attempts >= max_attempts:
                logger.warning(
                    "run_manager: turn %d failed after %d attempt(s)",
                    turn.turn_number,
                    attempts,
                )
                raise _TurnFailure(exc, attempts) from exc
            delay = RETRY_BACKOFF_SECONDS * (2 ** (attempts - 1))
            logger.warning(
                "run_manager: turn %d attempt %d failed (%s); retrying in %.2fs",
                turn.turn_number,
                attempts,
                type(exc).__name__,
                delay,
            )
            sleeper(delay)
            continue

        if raw is not None and not isinstance(raw, Mapping):
            raise RunManagerError(
                f"turn {turn.turn_number} executor must return a mapping or None, "
                f"got {type(raw).__name__}"
            )

        record: dict[str, Any] = dict(raw) if raw is not None else {}
        record.setdefault("case_id", context.case_id)
        record.setdefault("run_id", context.run_id)
        record.setdefault("user_id", context.user_id)
        record.setdefault("chat_id", context.chat_id)
        record.setdefault("turn_number", turn.turn_number)
        record.setdefault("role", turn.role)
        record.setdefault("status", STATUS_COMPLETED)
        record["attempts"] = attempts

        if score_turn is not None:
            scored = score_turn(context, turn, record)
            if scored:
                record["score"] = dict(scored)

        return record


def _state_text(state: Any) -> str:
    """Normalise a workspace state value to lowercase text."""
    value = getattr(state, "value", state)
    return str(value).strip().lower()


def _resolve_harness_seed(case: TestCase, harness_seed: str | int | None) -> str:
    """Pick the harness seed from the argument, else ``run_config``, else default."""
    if harness_seed is None:
        run_config = case.run_config or {}
        raw: Any = run_config.get("harness_seed", DEFAULT_HARNESS_SEED)
    else:
        raw = harness_seed

    text = str(raw).strip()
    if not text:
        raise RunPreparationError(
            f"case '{case.case_id}' does not define a usable harness seed"
        )
    logger.debug("run_manager: %s %s", f"{case.case_id = }", f"{text = }")
    return text


def _merge_model_overrides(
    defaults: Mapping[str, str] | None,
    case_overrides: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Merge caller-level model defaults under the case's own overrides.

    The case's values win, matching the pipeline's merge, so the run id the
    pipeline derives and the one :func:`start_run` prepares stay equal.
    """
    merged: dict[str, Any] = {}
    if defaults:
        merged.update({str(role): model for role, model in defaults.items()})
    if case_overrides:
        merged.update({str(role): model for role, model in case_overrides.items()})
    return merged or None


def _resolve_model_overrides(case: TestCase) -> dict[str, Any] | None:
    """Read the optional ``model_overrides`` mapping from ``run_config``."""
    run_config = case.run_config or {}
    overrides = run_config.get("model_overrides")
    if overrides is None:
        return None
    if not isinstance(overrides, Mapping):
        raise RunPreparationError(
            f"case '{case.case_id}' model_overrides must be an object"
        )
    return {str(key): value for key, value in overrides.items()}


def _resolve_fixtures(
    fixtures: dict[str, Any] | None, base: Path, case_id: str
) -> dict[str, Any] | None:
    """Resolve every declared fixture path against ``base``.

    Absolute paths and paths escaping ``base`` are rejected before any run
    directory exists.  The declared nesting shape is preserved; only string
    leaves become :class:`pathlib.Path` objects.
    """
    if fixtures is None:
        return None

    return {
        str(name): _resolve_fixture_value(value, base, case_id, str(name))
        for name, value in fixtures.items()
    }


def _resolve_fixture_value(value: Any, base: Path, case_id: str, name: str) -> Any:
    """Resolve fixture strings while keeping the declared nesting shape."""
    if isinstance(value, str):
        return _resolve_fixture_path(value, base, case_id, name)
    if isinstance(value, Mapping):
        return {
            str(key): _resolve_fixture_value(item, base, case_id, name)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_resolve_fixture_value(item, base, case_id, name) for item in value]
    return value


def _resolve_fixture_path(raw: str, base: Path, case_id: str, name: str) -> Path:
    """Resolve one fixture path and reject absolute or escaping ones."""
    text = raw.strip()
    if not text:
        raise RunPreparationError(
            f"case '{case_id}' fixture '{name}' has an empty path"
        )

    candidate = Path(text)
    if candidate.is_absolute():
        raise RunPreparationError(
            f"case '{case_id}' fixture '{name}' uses an absolute path: '{raw}'"
        )

    resolved = (base / candidate).resolve()
    try:
        resolved.relative_to(base)
    except ValueError:
        raise RunPreparationError(
            f"case '{case_id}' fixture '{name}' escapes the fixture base: '{raw}'"
        ) from None

    return resolved


def _copy_fixtures(
    fixtures: dict[str, Any] | None,
    base: Path,
    fixture_dir: Path,
    case_id: str,
) -> dict[str, Any] | None:
    """Copy every resolved fixture into ``fixture_dir``, keeping the shape."""
    if fixtures is None:
        return None

    return {
        str(name): _copy_fixture_value(value, base, fixture_dir, case_id, str(name))
        for name, value in fixtures.items()
    }


def _copy_fixture_value(
    value: Any,
    base: Path,
    fixture_dir: Path,
    case_id: str,
    name: str,
) -> Any:
    """Copy one fixture node, preserving the declared nesting shape."""
    if isinstance(value, Path):
        return _copy_fixture_file(value, base, fixture_dir, case_id, name)
    if isinstance(value, Mapping):
        return {
            str(key): _copy_fixture_value(item, base, fixture_dir, case_id, name)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [
            _copy_fixture_value(item, base, fixture_dir, case_id, name)
            for item in value
        ]
    return value


def _copy_fixture_file(
    source: Path,
    base: Path,
    fixture_dir: Path,
    case_id: str,
    name: str,
) -> Path:
    """Copy one fixture file or directory into the run workspace.

    The path relative to the fixture base is preserved inside ``fixture_dir``
    so that two fixtures with the same file name never collide.  The source is
    only read, never modified or moved.
    """
    try:
        relative = source.relative_to(base)
    except ValueError:
        relative = Path(source.name)

    destination = fixture_dir / relative
    if destination.resolve() == source.resolve():
        return destination

    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(source, destination)
    except OSError as exc:
        raise RunPreparationError(
            f"case '{case_id}' fixture '{name}' could not be copied: {exc}"
        ) from exc

    logger.debug("run_manager: %s %s", f"{source = }", f"{destination = }")
    return destination
