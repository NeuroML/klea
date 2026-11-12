# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Harness pipeline: run a suite end to end and store every run.

The module is the ADR-0060 orchestration layer.  It is a thin composition of
the collaborators that already own their part of the contract:

- :mod:`klea_eval.cases` loads and validates the suite (ADR-0056);
- :func:`klea_eval.run_manager.start_run` schedules one case and prepares its
  isolated run directory (ADR-0057);
- :func:`klea_eval.run_manager.run_case` executes and scores the turns through
  the injected executor and scorer, and publishes the run (ADR-0057);
- :func:`klea_eval.metrics.load_run` aggregates the run's own records
  (ADR-0058);
- :func:`klea_eval.results.write_metadata` and
  :func:`klea_eval.results.append_index_entry` write ``run.json`` and the one
  index line per completed run (ADR-0059);
- :class:`klea_eval.runners.http.HttpRunner` supplies the Execute stage's
  network surface when the caller does not inject an executor (ADR-0058).

Nothing is reimplemented here: no retry policy, no metric parsing, no
``.tmp`` handling, no transport.  A case that fails is isolated: its failure is
recorded in the returned :class:`CaseRun`, it leaves no published run and no
index line, and the suite continues with the next case.  Only a pre-run fault
(an invalid suite, an unusable workspace root, a missing executor) aborts the
suite before any run starts.

Suite-level aggregation (``pass_at_k``), retention, ``transcript.jsonl``
writing, HITL resume, cost derivation and the bounded worker pool are out of
scope here and belong to the later phases of the implementation plan.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klea_eval import cases, identity, metrics, results, run_manager, workspace
from klea_eval.cases import TestCase
from klea_eval.metrics import RunMetrics
from klea_eval.run_manager import RunContext, TurnExecutor, TurnScorer

logger = logging.getLogger(__name__)

__all__ = [
    "EXIT_CASE_FAILURE",
    "EXIT_CONFIGURATION_ERROR",
    "EXIT_OK",
    "CaseRun",
    "PipelineConfigError",
    "PipelineError",
    "SuiteResult",
    "run_suite",
]

#: Exit code of a suite whose every case met its rubrics (ADR-0060).
EXIT_OK = 0

#: Exit code of a suite that ran but had a case fail its rubrics (ADR-0060).
EXIT_CASE_FAILURE = 1

#: Exit code of a suite that could not start at all (ADR-0060).
EXIT_CONFIGURATION_ERROR = 2


class PipelineError(Exception):
    """Base class for failures raised by the harness pipeline."""


class PipelineConfigError(PipelineError):
    """Raised when the pipeline cannot be configured from its arguments."""


@dataclass(frozen=True, slots=True)
class CaseRun:
    """The outcome of one case in a suite run."""

    case_id: str
    status: str
    run_id: str | None = None
    output_dir: Path | None = None
    metrics: RunMetrics | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """The outcome of one end-to-end suite run, in suite order."""

    suite_path: Path
    workspace_root: Path
    runs: tuple[CaseRun, ...] = ()

    @property
    def completed(self) -> int:
        """Number of cases that executed and were stored."""
        return self._count(run_manager.STATUS_COMPLETED)

    @property
    def failed(self) -> int:
        """Number of cases that failed, were skipped as failures or stored badly."""
        return self._count(run_manager.STATUS_FAILED)

    @property
    def skipped(self) -> int:
        """Number of cases left untouched because their run was already complete."""
        return self._count(run_manager.STATUS_SKIPPED)

    @property
    def exit_code(self) -> int:
        """``0`` when every case passed, ``1`` when one failed its rubrics.

        A configuration fault never reaches this property: it aborts the suite
        before any run starts and is mapped to ``2`` by the CLI.
        """
        for case_run in self.runs:
            if case_run.status == run_manager.STATUS_FAILED:
                return EXIT_CASE_FAILURE
            if case_run.metrics is not None and case_run.metrics.task_success is False:
                return EXIT_CASE_FAILURE
        return EXIT_OK

    @property
    def ok(self) -> bool:
        """Whether the suite completed with every case passing."""
        return self.exit_code == EXIT_OK

    def _count(self, status: str) -> int:
        """Count the case runs whose status is ``status``."""
        return sum(1 for case_run in self.runs if case_run.status == status)


def run_suite(
    suite_path: str | Path,
    *,
    workspace_root: str | Path | None = None,
    execute_turn: TurnExecutor | None = None,
    score_turn: TurnScorer | None = None,
    runner: Any | None = None,
    base_url: str | None = None,
    endpoint: str | None = None,
    timeout: float | None = None,
    harness_seed: str | int | None = None,
    max_attempts: int = run_manager.DEFAULT_MAX_ATTEMPTS,
    sleep: Callable[[float], None] | None = None,
    overwrite: bool = False,
    model_overrides: Mapping[str, str] | None = None,
    apply_models: Callable[..., dict[str, str]] | None = None,
    attempt: int = 0,
) -> SuiteResult:
    """Execute every case of ``suite_path`` and store each completed run.

    ``execute_turn`` is the synchronous turn executor the run manager calls; if
    it is omitted, ``runner`` (an object exposing ``as_turn_executor()``) is
    used, and if that is omitted too an
    :class:`klea_eval.runners.http.HttpRunner` is built from ``base_url``,
    ``endpoint`` and ``timeout``.  Supplying none of the three is a
    configuration error: the suite cannot start, and nothing is stored.

    Cases run in suite order, single-threaded (the bounded worker pool is a
    later phase).  A case that fails is recorded and skipped, never promoted to
    a suite-level error; an already complete run is left untouched and reports
    :data:`klea_eval.run_manager.STATUS_SKIPPED` unless ``overwrite`` is set.
    """
    if not isinstance(suite_path, (str, Path)):
        raise PipelineConfigError(f"expected a path, got {type(suite_path).__name__}")
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise PipelineConfigError("max_attempts must be an integer")
    if max_attempts < 1:
        raise PipelineConfigError("max_attempts must be at least 1")
    if not isinstance(overwrite, bool):
        raise PipelineConfigError("overwrite must be a boolean")
    if score_turn is not None and not callable(score_turn):
        raise PipelineConfigError("score_turn must be callable or None")
    if sleep is not None and not callable(sleep):
        raise PipelineConfigError("sleep must be callable or None")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 0:
        raise PipelineConfigError("attempt must be a non-negative integer")

    executor = _resolve_executor(execute_turn, runner, base_url, endpoint, timeout)
    overrides = _checked_model_overrides(model_overrides)
    applier = _resolve_applier(apply_models)
    root = _prepare_workspace_root(workspace_root)

    loaded = cases.load_suite(suite_path)
    loaded_cases = _loaded_cases(loaded)
    fixture_base = _fixture_base(loaded, Path(suite_path))
    logger.debug(
        f"{suite_path = }, {root = }, {fixture_base = }, "
        f"{len(loaded_cases) = }, {overwrite = }"
    )

    if _overrides_need_base_url(overrides, loaded_cases) and (
        not isinstance(base_url, str) or not base_url.strip()
    ):
        raise PipelineConfigError(
            "no base URL: the model overrides need one to be applied"
        )

    outcomes: list[CaseRun] = []
    for case in loaded_cases:
        outcomes.append(
            _run_one_case(
                case,
                fixture_base,
                root,
                executor,
                score_turn,
                harness_seed,
                max_attempts,
                sleep,
                overwrite,
                base_url,
                overrides,
                endpoint,
                applier,
                attempt,
            )
        )

    outcome_tuple = tuple(outcomes)
    logger.debug(
        f"{suite_path = }, {len(outcome_tuple) = }, "
        f"{sum(1 for run in outcome_tuple if run.status == run_manager.STATUS_FAILED) = }"
    )
    return SuiteResult(
        suite_path=Path(suite_path).resolve(),
        workspace_root=root,
        runs=outcome_tuple,
    )


def _run_one_case(
    case: TestCase,
    fixture_base: Path,
    root: Path,
    execute_turn: TurnExecutor,
    score_turn: TurnScorer | None,
    harness_seed: str | int | None,
    max_attempts: int,
    sleep: Callable[[float], None] | None,
    overwrite: bool,
    base_url: str | None,
    overrides: Mapping[str, str],
    endpoint: str | None,
    applier: Callable[..., dict[str, str]],
    attempt: int = 0,
) -> CaseRun:
    """Schedule, execute and store one case, isolating any failure of it."""
    run_id = _run_id(case, harness_seed, overrides, endpoint)
    complete = workspace.detect_state(run_id, root) is workspace.RunState.COMPLETE

    if complete and not overwrite:
        output_dir = workspace.run_dir(run_id, root)
        if not _is_indexed(run_id, root):
            reindexed = _reindex_run(run_id, output_dir, root)
            if reindexed is not None:
                logger.info(
                    "pipeline: run %s was complete but unindexed, re-indexing it",
                    run_id,
                )
                return reindexed
        logger.info("pipeline: run %s is already complete, skipping it", run_id)
        return CaseRun(
            case_id=case.case_id,
            status=run_manager.STATUS_SKIPPED,
            run_id=run_id,
            output_dir=output_dir,
            metrics=_stored_metrics(output_dir),
        )

    try:
        if complete:
            # The workspace layer owns deletion of a published run; clearing it
            # first lets the run manager re-prepare the same deterministic id.
            workspace.prepare_run(run_id, root, overwrite=True)
        context = run_manager.start_run(
            case,
            fixture_base,
            root,
            harness_seed,
            model_overrides=overrides,
            endpoint=endpoint,
            attempt=attempt,
            revision=1 if overwrite else 0,
        )
    except Exception as exc:
        logger.exception("pipeline: could not prepare case %s", case.case_id)
        return CaseRun(
            case_id=case.case_id,
            status=run_manager.STATUS_FAILED,
            run_id=run_id,
            error=_error_text(exc),
        )

    logger.debug(f"{run_id = }, {context.run_dir = }, {len(case.turns) = }")

    merged = _merged_model_overrides(overrides, context)
    resolved_models: dict[str, str] = {}
    if merged:
        applied, failure = _apply_model_overrides(
            applier, context, base_url or "", merged
        )
        if failure is not None:
            return CaseRun(
                case_id=case.case_id,
                status=run_manager.STATUS_FAILED,
                run_id=run_id,
                error=failure,
            )
        resolved_models = applied or {}

    try:
        _write_run_metadata(context, case, resolved_models)
        outcome = run_manager.run_case(
            context,
            case,
            execute_turn,
            score_turn,
            sleep=sleep,
            max_attempts=max_attempts,
            overwrite=overwrite,
        )
    except Exception as exc:
        logger.exception("pipeline: case %s failed to execute", case.case_id)
        return CaseRun(
            case_id=case.case_id,
            status=run_manager.STATUS_FAILED,
            run_id=run_id,
            error=_error_text(exc),
        )

    if outcome.status != run_manager.STATUS_COMPLETED:
        logger.warning(
            "pipeline: case %s ended with status %s", case.case_id, outcome.status
        )
        return CaseRun(
            case_id=case.case_id,
            status=outcome.status,
            run_id=run_id,
            output_dir=outcome.output_dir,
            error=outcome.error,
        )

    output_dir = outcome.output_dir
    if output_dir is None:
        return CaseRun(
            case_id=case.case_id,
            status=run_manager.STATUS_FAILED,
            run_id=run_id,
            error="the run manager published no run directory",
        )

    try:
        _append_run_envelope(context, Path(output_dir), resolved_models)
        summary = metrics.load_run(output_dir)
        _store_run(context, case, Path(output_dir), root, resolved_models)
    except (
        metrics.MetricsError,
        results.ResultsError,
        run_manager.RunManagerError,
        OSError,
    ) as exc:
        logger.exception("pipeline: could not store the run of %s", case.case_id)
        return CaseRun(
            case_id=case.case_id,
            status=run_manager.STATUS_FAILED,
            run_id=run_id,
            output_dir=Path(output_dir),
            error=_error_text(exc),
        )

    logger.debug(
        f"{run_id = }, {output_dir = }, {summary.turn_count = }, "
        f"{summary.task_success = }"
    )
    return CaseRun(
        case_id=case.case_id,
        status=run_manager.STATUS_COMPLETED,
        run_id=run_id,
        output_dir=Path(output_dir),
        metrics=summary,
    )


def _run_metadata(
    context: RunContext,
    case: TestCase,
    resolved_models: Mapping[str, str] | None = None,
) -> results.RunMetadata:
    """Build the ``run.json`` envelope of one completed run."""
    return results.RunMetadata(
        run_id=context.run_id,
        case_id=context.case_id,
        status=run_manager.STATUS_COMPLETED,
        harness_seed=context.harness_seed,
        case_version=case.schema_version,
        models=context.model_overrides,
        resolved_models=dict(resolved_models or {}),
        chat_id=context.chat_id,
        attempt=context.attempt,
        revision=context.revision,
    )


def _write_run_metadata(
    context: RunContext,
    case: TestCase,
    resolved_models: Mapping[str, str] | None = None,
) -> None:
    """Write ``run.json`` into the staged run directory before publication.

    ``workspace.publish_run`` marks a run complete by renaming its staging
    directory, so stamping ``run.json`` first means a COMPLETE marker always
    implies a run whose metadata is already recoverable.
    """
    stamped = results.write_metadata(
        context.run_dir, _run_metadata(context, case, resolved_models)
    )
    logger.debug(f"{stamped.run_id = }, {context.run_dir = }")


def _store_run(
    context: RunContext,
    case: TestCase,
    output_dir: Path,
    root: Path,
    resolved_models: Mapping[str, str] | None = None,
) -> None:
    """Stamp ``run.json`` and append the single index line of a completed run."""
    stamped = results.write_metadata(
        output_dir, _run_metadata(context, case, resolved_models)
    )
    results.append_index_entry(results.to_index_entry(stamped), root)
    logger.debug(f"{stamped.run_id = }, {output_dir = }, {root = }")


def _checked_model_overrides(model_overrides: Any) -> dict[str, str]:
    """Validate the caller's role-to-model defaults into a stripped mapping."""
    if model_overrides is None:
        return {}
    if not isinstance(model_overrides, Mapping):
        raise PipelineConfigError(
            f"model_overrides must be a mapping, got {type(model_overrides).__name__}"
        )

    checked: dict[str, str] = {}
    for role, model in model_overrides.items():
        role_text = str(role).strip()
        if not role_text:
            raise PipelineConfigError("a model override declares an empty role")
        if not isinstance(model, str) or not model.strip():
            raise PipelineConfigError(
                f"model override for role '{role_text}' must be a non-empty string"
            )
        checked[role_text] = model.strip()

    logger.debug(f"{checked = }")
    return checked


def _resolve_applier(
    apply_models: Callable[..., dict[str, str]] | None,
) -> Callable[..., dict[str, str]]:
    """Return the callable that applies the role-to-model overrides."""
    if apply_models is not None:
        if not callable(apply_models):
            raise PipelineConfigError("apply_models must be callable")
        logger.debug(f"{apply_models = }")
        return apply_models

    # The applier builds its HTTP client lazily itself; importing the module
    # here keeps that stack out of an injected caller's import graph.
    from klea_eval.run_config import apply_run_models

    return apply_run_models


def _case_model_overrides(case: TestCase) -> dict[str, str]:
    """Read a case's own ``model_overrides`` mapping for the pre-run check."""
    run_config = case.run_config or {}
    raw = run_config.get("model_overrides")
    if not isinstance(raw, Mapping):
        return {}
    return {str(role): str(model) for role, model in raw.items()}


def _merged_default_overrides(
    defaults: Mapping[str, str], case: TestCase
) -> dict[str, str]:
    """Merge the caller's role defaults under the case's own overrides."""
    merged = {str(role): str(model) for role, model in defaults.items()}
    merged.update(_case_model_overrides(case))
    return merged


def _overrides_need_base_url(
    defaults: Mapping[str, str], loaded_cases: tuple[TestCase, ...]
) -> bool:
    """Whether any case of the suite needs the backend to apply overrides."""
    if defaults:
        return True
    return any(bool(_case_model_overrides(case)) for case in loaded_cases)


def _merged_model_overrides(
    defaults: Mapping[str, str], context: RunContext
) -> dict[str, str]:
    """Merge the caller's defaults under the case's values, the case winning."""
    merged = dict(defaults)
    case_overrides = context.model_overrides or {}
    merged.update({str(role): str(model) for role, model in case_overrides.items()})
    logger.debug(f"{merged = }")
    return merged


def _apply_model_overrides(
    applier: Callable[..., dict[str, str]],
    context: RunContext,
    base_url: str,
    merged: Mapping[str, str],
) -> tuple[dict[str, str] | None, str | None]:
    """Apply the merged overrides; return the resolved map or the failure text."""
    # The error type belongs to the applier module, whose HTTP stack must stay
    # out of the import graph of a caller that never applies an override.
    from klea_eval.run_config import ModelOverrideError

    try:
        resolved = applier(
            None,
            base_url,
            context.user_id,
            context.chat_id,
            merged,
        )
    except ModelOverrideError as exc:
        logger.exception(
            "pipeline: could not apply the model overrides of %s", context.case_id
        )
        workspace.discard_run(context.run_id, context.workspace_root)
        return None, _error_text(exc)

    logger.debug(f"{resolved = }")
    return dict(resolved), None


def _append_run_envelope(
    context: RunContext, output_dir: Path, resolved_models: Mapping[str, str]
) -> None:
    """Append the single run-level envelope record of a completed run."""
    # ``append_metric`` owns the atomic, fsync'd line write, so the envelope
    # lands on the same durability path as the run's own turn lines.
    record = {
        "run_id": context.run_id,
        "case_id": context.case_id,
        "schema_version": metrics.SCHEMA_VERSION,
        "record_type": metrics.RECORD_TYPE_RUN,
        "status": run_manager.STATUS_COMPLETED,
        "metrics": {},
        "resolved_models": dict(resolved_models),
        "chat_id": context.chat_id,
        "attempt": context.attempt,
        "revision": context.revision,
    }
    run_manager.append_metric(Path(workspace.metrics_path(output_dir)), record)
    logger.debug(f"{context.run_id = }, {output_dir = }")


def _stored_metrics(run_dir: Path) -> metrics.RunMetrics | None:
    """Read the metrics of an already published run, tolerating absence."""
    try:
        return metrics.load_run(run_dir)
    except metrics.MetricsError as exc:
        logger.debug(f"{run_dir = }, {exc = }")
        return None


def _is_indexed(run_id: str, root: Path) -> bool:
    """Whether ``runs/index.jsonl`` already carries the line of ``run_id``."""
    try:
        entries = results.read_index(root)
    except results.ResultsError as exc:
        logger.debug(f"{root = }, {exc = }")
        return False

    indexed = any(entry.run_id == run_id for entry in entries)
    logger.debug(f"{run_id = }, {indexed = }")
    return indexed


def _reindex_run(run_id: str, output_dir: Path, root: Path) -> CaseRun | None:
    """Append the missing index line of a stored run; ``None`` if unreadable."""
    try:
        metadata = results.read_metadata(output_dir)
        results.append_index_entry(results.to_index_entry(metadata), root)
    except (results.ResultsError, OSError) as exc:
        logger.warning("pipeline: could not re-index run %s: %s", run_id, exc)
        return None

    logger.debug(f"{run_id = }, {output_dir = }, {root = }")
    return CaseRun(
        case_id=metadata.case_id,
        status=run_manager.STATUS_COMPLETED,
        run_id=run_id,
        output_dir=output_dir,
        metrics=_stored_metrics(output_dir),
    )


def _resolve_executor(
    execute_turn: TurnExecutor | None,
    runner: Any | None,
    base_url: str | None,
    endpoint: str | None,
    timeout: float | None,
) -> TurnExecutor:
    """Return the synchronous turn executor the run manager calls."""
    if execute_turn is not None:
        if not callable(execute_turn):
            raise PipelineConfigError("execute_turn must be callable")
        logger.debug(f"{execute_turn = }")
        return execute_turn

    http_runner = runner
    if http_runner is None:
        if not isinstance(base_url, str) or not base_url.strip():
            raise PipelineConfigError(
                "no turn executor: pass execute_turn, a runner or a base_url"
            )
        # The HTTP adapter imports ``httpx``; importing it here keeps the
        # pipeline importable by callers that inject their own executor.
        from klea_eval.runners.http import (
            DEFAULT_ENDPOINT,
            DEFAULT_TIMEOUT_SECONDS,
            HttpRunner,
        )

        http_runner = HttpRunner(
            base_url,
            endpoint=endpoint or DEFAULT_ENDPOINT,
            timeout=timeout if timeout is not None else DEFAULT_TIMEOUT_SECONDS,
        )

    factory = getattr(http_runner, "as_turn_executor", None)
    if not callable(factory):
        raise PipelineConfigError("runner must expose as_turn_executor()")

    executor = factory()
    logger.debug(f"{http_runner = }, {executor = }")
    return executor


def _prepare_workspace_root(workspace_root: str | Path | None) -> Path:
    """Resolve the workspace root and make its runs directory writable."""
    root = workspace.workspace_root(
        Path(workspace_root) if workspace_root is not None else None
    )
    try:
        workspace.runs_dir(root).mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise PipelineConfigError(
            f"could not create the runs directory under '{root}': {exc}"
        ) from exc

    logger.debug(f"{root = }")
    return root


def _loaded_cases(loaded: Any) -> tuple[TestCase, ...]:
    """Return the cases of a loaded suite, in loader order."""
    raw = getattr(loaded, "cases", None)
    if raw is None and isinstance(loaded, (tuple, list)):
        raw = loaded
    if raw is None:
        raise PipelineError("the loaded suite does not expose its cases")

    loaded_cases = tuple(raw)
    logger.debug(f"{len(loaded_cases) = }")
    return loaded_cases


def _fixture_base(loaded: Any, suite_path: Path) -> Path:
    """Return the directory a case's relative fixture paths resolve against."""
    source = getattr(loaded, "source", None)
    base = Path(source) if isinstance(source, (str, Path)) else suite_path
    resolved = base.resolve().parent
    logger.debug(f"{base = }, {resolved = }")
    return resolved


def _run_id(
    case: TestCase,
    harness_seed: str | int | None,
    overrides: Mapping[str, str],
    endpoint: str | None = None,
) -> str:
    """Derive the deterministic run id the run manager will use for ``case``.

    The pipeline needs the id before it prepares the run, so that a second run
    of the same case under a different model set or endpoint is not skipped as
    an already complete one.  The derivation mirrors
    :func:`klea_eval.run_manager.start_run`: the caller's ``overrides`` are
    merged under the case's own model overrides (the case wins), and the merged
    set plus ``endpoint`` enter the identity exactly as they do there.
    """
    if harness_seed is not None:
        seed = str(harness_seed).strip()
    else:
        run_config = case.run_config or {}
        raw = run_config.get("harness_seed", run_manager.DEFAULT_HARNESS_SEED)
        seed = str(raw).strip()

    if not seed:
        raise PipelineConfigError(
            f"case '{case.case_id}' does not define a usable harness seed"
        )

    merged = _merged_default_overrides(overrides, case)
    derived = identity.derive_identity(
        case.case_id,
        seed,
        model_overrides=merged,
        endpoint=endpoint,
    )
    logger.debug(
        f"{case.case_id = }, {seed = }, {merged = }, {endpoint = }, {derived.run_id = }"
    )
    return derived.run_id


def _error_text(exc: BaseException) -> str:
    """Render an exception as a single ``Type: message`` line."""
    return f"{type(exc).__name__}: {exc}"
