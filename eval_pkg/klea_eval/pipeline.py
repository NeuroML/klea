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

Suite-level aggregation (``pass_at_k``), retention, HITL resume, provider
pricing (a cost stays derivable from the recorded token counts) and the bounded
worker pool are out of scope here and belong to the later phases of the
implementation plan.
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

#: Exit code of a suite whose completed run declared rubrics but recorded no
#: grade.  Kept distinct from :data:`EXIT_CASE_FAILURE` so a scoring fault is
#: never reported as a real verdict (ADR-0060).
EXIT_GRADE_MISSING = 4


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
    expects_grade: bool = False


@dataclass(frozen=True, slots=True)
class SuiteResult:
    """The outcome of one end-to-end suite run, in suite order."""

    suite_path: Path
    workspace_root: Path
    runs: tuple[CaseRun, ...] = ()

    @property
    def completed(self) -> int:
        """Number of cases that executed, were stored and carried a grade."""
        return self._count_completed(graded=True)

    @property
    def failed(self) -> int:
        """Number of cases that failed, were skipped as failures or stored badly."""
        return self._count(run_manager.STATUS_FAILED)

    @property
    def skipped(self) -> int:
        """Number of cases left untouched because their run was already complete."""
        return self._count(run_manager.STATUS_SKIPPED)

    @property
    def ungraded(self) -> int:
        """Number of cases that ran but carried no grade."""
        return self._count_completed(graded=False)

    @property
    def grade_missing(self) -> int:
        """Number of cases that ran but recorded no grade despite rubrics.

        The condition is :func:`klea_eval.labels.classify_case` itself, so this
        counter cannot drift from the per-case label read by the CLI or from
        the ``grade_missing_count`` aggregate.
        """
        # Deferred import: keep the pipeline's import graph light, matching the
        # other readers that reach for the shared label helper.
        from klea_eval import labels

        return sum(
            1
            for case_run in self.runs
            if labels.classify_case(
                status=case_run.status,
                task_success=(
                    case_run.metrics.task_success
                    if case_run.metrics is not None
                    else None
                ),
                expects_grade=case_run.expects_grade,
            )
            == labels.CASE_GRADE_MISSING
        )

    @property
    def exit_code(self) -> int:
        """``0`` when every case passed, ``1`` when at least one case failed.

        A completed case that declared rubrics but recorded no grade makes the
        suite exit ``4`` (:data:`EXIT_GRADE_MISSING`), which is distinct from
        ``1`` so a scoring fault is never reported as a real verdict.  A real
        failure outranks a missing grade, so ``1`` wins when both occur.

        A configuration fault never reaches this property: it aborts the suite
        before any run starts and is mapped to ``2`` by the CLI.
        """
        # Precedence: a real case failure always wins over a missing grade, so
        # an infrastructure fault is never masked by a scoring gap.
        for case_run in self.runs:
            if case_run.status == run_manager.STATUS_FAILED:
                return EXIT_CASE_FAILURE
            if case_run.metrics is not None and case_run.metrics.task_success is False:
                return EXIT_CASE_FAILURE
        # Nothing failed; a completed grade-missing case still signals a fault.
        # Ungraded and skipped cases alone leave the suite at ``EXIT_OK``.
        if self.grade_missing:
            return EXIT_GRADE_MISSING
        return EXIT_OK

    @property
    def ok(self) -> bool:
        """Whether the suite completed with every case passing."""
        return self.exit_code == EXIT_OK

    def _count(self, status: str) -> int:
        """Count the case runs whose status is ``status``."""
        return sum(1 for case_run in self.runs if case_run.status == status)

    def _count_completed(self, *, graded: bool) -> int:
        """Count completed cases whose grade is present (``graded``) or absent.

        A stored case that carried no grade keeps the
        :data:`run_manager.STATUS_COMPLETED` status it was stamped with, so the
        ``completed`` and ``ungraded`` counters split the completed cases
        between them: together they are exhaustive over ``STATUS_COMPLETED``
        and never count the same case twice.
        """

        def grade_present(case_run: CaseRun) -> bool:
            """Whether ``case_run`` carries an explicit pass/fail grade."""
            return (
                case_run.metrics is not None
                and case_run.metrics.task_success is not None
            )

        return sum(
            1
            for case_run in self.runs
            if case_run.status == run_manager.STATUS_COMPLETED
            and grade_present(case_run) is graded
        )


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
    check_models: Callable[..., dict[str, Any]] | None = None,
    attempt: int = 0,
) -> SuiteResult:
    """Execute every case of ``suite_path`` and store each completed run.

    ``execute_turn`` is the synchronous turn executor the run manager calls; if
    it is omitted, ``runner`` (an object exposing ``as_turn_executor()``) is
    used, and if that is omitted too an
    :class:`klea_eval.runners.http.HttpRunner` is built from ``base_url``,
    ``endpoint`` and ``timeout``.  Supplying none of the three is a
    configuration error: the suite cannot start, and nothing is stored.

    ``check_models`` is the read-only pre-flight that rejects an override role
    the backend does not expose before the first case runs; an unknown role is
    a configuration error of the whole suite, not a failure of one case.

    Cases run in suite order, single-threaded (the bounded worker pool is a
    later phase).  A case that fails is recorded and skipped, never promoted to
    a suite-level error; an already complete run is left untouched and reports
    :data:`klea_eval.run_manager.STATUS_SKIPPED` unless ``overwrite`` is set.

    After a case is stored, its graded summary is re-read from the run so the
    recorded ``task_success`` is visible to the suite, and the returned
    :class:`SuiteResult` reports the ``0``/``1``/``2``/``4`` exit-code contract
    of :attr:`SuiteResult.exit_code`.
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
    checker = _resolve_checker(check_models)
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

    _check_override_roles(checker, base_url, overrides, loaded_cases, harness_seed)

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
    # An archived run is still a published run, so it takes the same branch.
    complete = workspace.detect_state(run_id, root) in {
        workspace.RunState.COMPLETE,
        workspace.RunState.ARCHIVED,
    }

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
            expects_grade=_expects_grade(case),
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
        summary = metrics.load_run(output_dir)
        # The deterministic checker reads the run's own workspace and retained
        # turn records, so it runs here, while both are in scope; a case that
        # declares no expectation yields no checks and stays ungraded.
        from klea_eval import expectations

        checks = expectations.check_case(
            case, Path(output_dir) / "workspace", summary.turns
        )
        grade = expectations.grade_checks(checks)
        logger.info(
            "pipeline: case %s recorded %d expectation check(s), grade %s",
            case.case_id,
            len(checks),
            grade,
        )
        _append_run_envelope(context, Path(output_dir), resolved_models, checks, grade)
        _store_run(context, case, Path(output_dir), root, resolved_models)
        # The summary above was read before the run envelope was appended, so it
        # cannot carry the recorded grade; re-read it after both writes so the
        # returned CaseRun agrees with the stored run.  Otherwise a case that did
        # record a failing grade would still look grade-missing to the suite's
        # exit-code decision, which must exit 1 for a real failure and reserve 4
        # for a declared expectation that recorded no grade at all.
        summary = metrics.load_run(output_dir)
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
        expects_grade=_expects_grade(case),
    )


def _expects_grade(case: TestCase) -> bool:
    """Whether a case declares anything a deterministic grader reports on.

    A case expects a grade when it declares rubrics (ADR-0061), a command to run
    (ADR-0056 amendment 2026-11-19) or any turn expectation the checker can
    evaluate; a case that declares none is ungraded, which the reporter counts
    apart from a graded one.
    """
    if case.rubrics or bool(getattr(case, "command", ())):
        return True

    return any(
        getattr(turn, "expected_outputs", ()) or getattr(turn, "tool_expectations", ())
        for turn in case.turns
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
        expects_grade=_expects_grade(case),
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


def _resolve_checker(
    check_models: Callable[..., dict[str, Any]] | None,
) -> Callable[..., dict[str, Any]] | None:
    """Return the callable that pre-checks the override roles, if any."""
    if check_models is not None:
        if not callable(check_models):
            raise PipelineConfigError("check_models must be callable")
        logger.debug(f"{check_models = }")
        return check_models

    # The checker reads the active model map through its own lazy HTTP client;
    # importing the module here keeps that stack out of an injected caller's
    # import graph, exactly as the applier does.
    from klea_eval.run_config import check_override_roles

    return check_override_roles


def _suite_override_roles(
    defaults: Mapping[str, str], loaded_cases: tuple[TestCase, ...]
) -> dict[str, str]:
    """Union the caller's role defaults with every case's own overrides."""
    roles = {str(role): str(model) for role, model in defaults.items()}
    for case in loaded_cases:
        roles.update(_case_model_overrides(case))
    logger.debug(f"{roles = }")
    return roles


def _check_override_roles(
    checker: Callable[..., dict[str, Any]] | None,
    base_url: str | None,
    defaults: Mapping[str, str],
    loaded_cases: tuple[TestCase, ...],
    harness_seed: str | int | None,
) -> None:
    """Reject an override role the backend does not expose, before any case runs.

    The check is read-only and runs once for the whole suite: the exposed role
    set is app-wide, so one read of a chat this suite will use covers the
    caller defaults and every case's own overrides together.  A rejected role
    aborts the suite with a :class:`PipelineConfigError` instead of leaving the
    case loop to fail each affected case on its own, so no run, ``run.json`` or
    index line is produced for a typo.
    """
    if checker is None:
        return

    roles = _suite_override_roles(defaults, loaded_cases)
    if not roles:
        return

    # The error type belongs to the checker module, so it stays lazily imported.
    from klea_eval.run_config import ModelOverrideError

    # The active map is per chat, so address the chat the first case will use:
    # derive its identity from the same inputs the run manager uses.
    reference = loaded_cases[0]
    seed = _normalized_seed(reference, harness_seed)
    derived = identity.derive_identity(reference.case_id, seed, model_overrides=roles)

    try:
        checker(base_url, derived.user_id, derived.chat_id, roles)
    except ModelOverrideError as exc:
        raise PipelineConfigError(f"model overrides: {exc}") from exc

    logger.debug(f"{reference.case_id = }, {len(roles) = }")


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
    context: RunContext,
    output_dir: Path,
    resolved_models: Mapping[str, str],
    checks: tuple[Any, ...] = (),
    grade: bool | None = None,
) -> None:
    """Append the single run-level envelope record of a completed run.

    ``checks`` are the deterministic expectation verdicts of the run and
    ``grade`` their reduction; both are recorded in the envelope's ``metrics``
    mapping, so the aggregate can report the case without re-reading the run
    workspace.  An ungraded run leaves ``task_success`` out instead of
    recording a passing verdict it never verified.
    """
    # ``append_metric`` owns the atomic, fsync'd line write, so the envelope
    # lands on the same durability path as the run's own turn lines.
    envelope_metrics: dict[str, Any] = {
        "expectation_checks": [check.to_dict() for check in checks],
    }
    if grade is not None:
        envelope_metrics["task_success"] = grade
    record = {
        "run_id": context.run_id,
        "case_id": context.case_id,
        "schema_version": metrics.SCHEMA_VERSION,
        "record_type": metrics.RECORD_TYPE_RUN,
        "status": run_manager.STATUS_COMPLETED,
        "metrics": envelope_metrics,
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
        expects_grade=bool(metadata.expects_grade),
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


def _normalized_seed(case: TestCase, harness_seed: str | int | None) -> str:
    """Resolve the case's harness seed to the non-empty string the identity takes.

    A caller-supplied seed wins; otherwise the case's own ``harness_seed`` is
    read, falling back to :data:`run_manager.DEFAULT_HARNESS_SEED`, exactly as
    :func:`klea_eval.run_manager.start_run` does.  An empty seed is a case
    configuration error, so the identity is never derived from it.
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
    return seed


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
    seed = _normalized_seed(case, harness_seed)

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
