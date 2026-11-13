# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""In-process scheduler: run evaluation suites unattended (P9, ADR-0060).

The module is only the Schedule stage of ADR-0060.  It enumerates the suites to
run, drives each of them through :func:`klea_eval.pipeline.run_suite` and
aggregates the stored runs of each suite through
:func:`klea_eval.scoring.aggregate_suite`.  It owns no loading, execution,
storage or scoring logic of its own: the loader (:mod:`klea_eval.cases`), the
run manager (:mod:`klea_eval.run_manager`), the ADR-0059 writer
(:mod:`klea_eval.results`) and the scorer (:mod:`klea_eval.scoring`) keep their
contracts, and this module only orders the calls.

A suite that cannot start is isolated: its failure is recorded in its
:class:`SuiteRun` and the remaining suites still run, so one misconfigured suite
does not abandon an unattended run.  A leftover ``.tmp`` run directory is an
interrupted run, never a result (ADR-0060 crash recovery), so the report lists
it in :attr:`ScheduleReport.incomplete_runs` and maps it to exit code 3.

Exit codes follow ADR-0060: ``0`` when every case of every suite met its
rubrics, ``1`` when a case failed its rubrics, ``2`` when a suite could not
start and ``3`` when incomplete ``.tmp`` runs were found.

Deliberately deferred, all belonging to later work: the bounded worker pool and
any parallel worker count (execution stays sequential, ``DEFAULT_MAX_WORKERS``
is 1), an external or broker-backed scheduler backend, a ``klea-eval schedule``
CLI command, automatic repair or redo of leftover ``.tmp`` runs,
``transcript.jsonl`` writing, HITL ``interrupt`` resume, cost derivation (no
price table exists), MLflow export, a persisted per-suite report artefact and
the Markdown reporter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from klea_eval import pipeline, run_manager, scoring, workspace
from klea_eval.errors import CaseValidationError
from klea_eval.run_manager import TurnExecutor, TurnScorer

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_MAX_WORKERS",
    "DEFAULT_SUITE_GLOB",
    "EXIT_CASE_FAILURE",
    "EXIT_CONFIGURATION_ERROR",
    "EXIT_INCOMPLETE_RUNS",
    "EXIT_OK",
    "ScheduleReport",
    "SchedulerConfigError",
    "SchedulerError",
    "SuiteRun",
    "discover_suites",
    "find_incomplete_runs",
    "run_suites",
]

#: Worker count of the in-process pool; execution is sequential by default
#: (ADR-0060), so the scheduler drives one suite at a time.
DEFAULT_MAX_WORKERS = 1

#: Glob used when the scheduler is pointed at a directory of suites.
DEFAULT_SUITE_GLOB = "*.jsonl"

#: Exit code of a run where every case met its rubrics (ADR-0060).
EXIT_OK = pipeline.EXIT_OK

#: Exit code of a run where a case failed its rubrics (ADR-0060).
EXIT_CASE_FAILURE = pipeline.EXIT_CASE_FAILURE

#: Exit code of a suite that could not start at all (ADR-0060).
EXIT_CONFIGURATION_ERROR = pipeline.EXIT_CONFIGURATION_ERROR

#: Exit code reported when interrupted ``.tmp`` runs were found (ADR-0060).
EXIT_INCOMPLETE_RUNS = 3


class SchedulerError(Exception):
    """Base error for scheduler operations."""


class SchedulerConfigError(SchedulerError):
    """Raised when the scheduler cannot be configured from its arguments."""


@dataclass(frozen=True, slots=True)
class SuiteRun:
    """The outcome of scheduling one suite."""

    suite_path: Path
    result: pipeline.SuiteResult | None = None
    aggregate: scoring.SuiteAggregate | None = None
    error: str | None = None

    @property
    def completed(self) -> int:
        """Number of cases that executed and were stored."""
        if self.result is None:
            return 0
        return self.result.completed

    @property
    def failed(self) -> int:
        """Number of cases that failed, were skipped as failures or stored badly."""
        if self.result is None:
            return 0
        return self.result.failed

    @property
    def skipped(self) -> int:
        """Number of cases left untouched because their run was already complete."""
        if self.result is None:
            return 0
        return self.result.skipped

    @property
    def exit_code(self) -> int:
        """The ADR-0060 exit code of this suite alone."""
        if self.error is not None:
            return EXIT_CONFIGURATION_ERROR
        if self.result is None:
            return EXIT_CONFIGURATION_ERROR
        return self.result.exit_code


@dataclass(frozen=True, slots=True)
class ScheduleReport:
    """What one unattended scheduling pass did, in suite order."""

    workspace_root: Path
    suites: tuple[SuiteRun, ...] = ()
    incomplete_runs: tuple[str, ...] = ()

    @property
    def exit_code(self) -> int:
        """The ADR-0060 exit code of the whole pass.

        A suite that could not start outranks everything else (``2``), because
        nothing was stored for it; leftover ``.tmp`` runs then report ``3``; a
        stored case that failed its rubrics reports ``1``.
        """
        if any(suite.error is not None for suite in self.suites):
            return EXIT_CONFIGURATION_ERROR
        if self.incomplete_runs:
            return EXIT_INCOMPLETE_RUNS
        for suite in self.suites:
            if suite.exit_code != EXIT_OK:
                return suite.exit_code
        return EXIT_OK

    @property
    def ok(self) -> bool:
        """Whether the pass completed with every case passing."""
        return self.exit_code == EXIT_OK

    def to_dict(self) -> dict[str, Any]:
        """Return the report as a plain, JSON-ready mapping."""
        return {
            "workspace_root": str(self.workspace_root),
            "exit_code": self.exit_code,
            "incomplete_runs": list(self.incomplete_runs),
            "suites": [
                {
                    "suite_path": str(suite.suite_path),
                    "completed": suite.completed,
                    "failed": suite.failed,
                    "skipped": suite.skipped,
                    "exit_code": suite.exit_code,
                    "error": suite.error,
                    "aggregate": _json_form(suite.aggregate),
                }
                for suite in self.suites
            ],
        }


def discover_suites(path: str | Path) -> tuple[Path, ...]:
    """Return the suites to run under ``path``.

    A suite file yields itself; a directory yields its ``*.jsonl`` files sorted
    by name, so the order of an unattended pass is deterministic.  A missing
    path, or a directory holding no suite, raises :class:`SchedulerConfigError`.
    """
    if not isinstance(path, (str, Path)):
        raise SchedulerConfigError(f"expected a path, got {type(path).__name__}")

    target = Path(path).expanduser()
    if target.is_file():
        logger.debug(f"{target = }")
        return (target,)

    if target.is_dir():
        found = tuple(
            sorted(
                candidate
                for candidate in target.glob(DEFAULT_SUITE_GLOB)
                if candidate.is_file()
            )
        )
        if not found:
            raise SchedulerConfigError(
                f"no {DEFAULT_SUITE_GLOB} suites under '{target}'"
            )
        logger.debug(f"{target = }, {len(found) = }")
        return found

    raise SchedulerConfigError(f"no suite file or directory at '{target}'")


def run_suites(
    suite_paths: Sequence[str | Path],
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
    k: int = scoring.DEFAULT_K,
) -> ScheduleReport:
    """Run every suite in ``suite_paths`` unattended, in the given order.

    Each suite is handed to :func:`klea_eval.pipeline.run_suite` unchanged, so
    loading, execution, scoring and storage keep the contracts of their own
    modules; the suite's stored runs are then aggregated through
    :func:`klea_eval.scoring.aggregate_suite`, restricted to that suite's cases.
    Execution is single-threaded (ADR-0060), and a suite that cannot start is
    recorded in its :class:`SuiteRun` without stopping the remaining suites.
    """
    if isinstance(suite_paths, (str, bytes)):
        raise SchedulerConfigError(
            "suite_paths must be a sequence of paths; "
            "call discover_suites() for a single path"
        )
    if isinstance(max_attempts, bool) or not isinstance(max_attempts, int):
        raise SchedulerConfigError("max_attempts must be an integer")
    if max_attempts < 1:
        raise SchedulerConfigError("max_attempts must be at least 1")
    if isinstance(k, bool) or not isinstance(k, int):
        raise SchedulerConfigError("k must be an integer")
    if k < 1:
        raise SchedulerConfigError("k must be at least 1")
    if not isinstance(overwrite, bool):
        raise SchedulerConfigError("overwrite must be a boolean")
    if sleep is not None and not callable(sleep):
        raise SchedulerConfigError("sleep must be callable or None")

    paths = _suite_paths(suite_paths)
    root_path = _workspace_root(workspace_root)
    logger.debug(f"{len(paths) = }, {root_path = }, {k = }, {overwrite = }")

    outcomes: list[SuiteRun] = []
    for suite_path in paths:
        outcomes.append(
            _run_one_suite(
                suite_path,
                workspace_root=workspace_root,
                execute_turn=execute_turn,
                score_turn=score_turn,
                runner=runner,
                base_url=base_url,
                endpoint=endpoint,
                timeout=timeout,
                harness_seed=harness_seed,
                max_attempts=max_attempts,
                sleep=sleep,
                overwrite=overwrite,
                k=k,
            )
        )

    report = ScheduleReport(
        workspace_root=root_path,
        suites=tuple(outcomes),
        incomplete_runs=find_incomplete_runs(root_path),
    )
    logger.debug(
        f"{len(report.suites) = }, {report.exit_code = }, "
        f"{len(report.incomplete_runs) = }"
    )
    return report


def find_incomplete_runs(root: str | Path | None = None) -> tuple[str, ...]:
    """Return the identifiers of runs with a leftover ``.tmp`` directory.

    ADR-0060 crash recovery: a killed harness leaves exactly
    ``runs/<run_id>.tmp/`` directories, which are unambiguously incomplete.  The
    scheduler reports them and never treats them as results; repairing them is
    left to the next run of the same deterministic identifier.
    """
    root_path = _workspace_root(root)
    directory = workspace.runs_dir(root_path)
    if not directory.is_dir():
        logger.debug(f"{directory = }, no runs directory")
        return ()

    incomplete: list[str] = []
    for entry in sorted(directory.iterdir(), key=lambda item: item.name):
        if not entry.is_dir():
            continue
        if entry.name.endswith(workspace.TMP_SUFFIX):
            run_id = entry.name[: -len(workspace.TMP_SUFFIX)]
        else:
            run_id = entry.name
        state = workspace.detect_state(run_id, root_path)
        if state is workspace.RunState.INCOMPLETE and run_id not in incomplete:
            incomplete.append(run_id)

    logger.debug(f"{root_path = }, {incomplete = }")
    return tuple(incomplete)


def _run_one_suite(
    suite_path: Path,
    *,
    workspace_root: str | Path | None,
    execute_turn: TurnExecutor | None,
    score_turn: TurnScorer | None,
    runner: Any | None,
    base_url: str | None,
    endpoint: str | None,
    timeout: float | None,
    harness_seed: str | int | None,
    max_attempts: int,
    sleep: Callable[[float], None] | None,
    overwrite: bool,
    k: int,
) -> SuiteRun:
    """Schedule one suite through the pipeline, isolating its failure."""
    try:
        result = pipeline.run_suite(
            suite_path,
            workspace_root=workspace_root,
            execute_turn=execute_turn,
            score_turn=score_turn,
            runner=runner,
            base_url=base_url,
            endpoint=endpoint,
            timeout=timeout,
            harness_seed=harness_seed,
            max_attempts=max_attempts,
            sleep=sleep,
            overwrite=overwrite,
        )
    except (pipeline.PipelineError, CaseValidationError) as exc:
        logger.warning("scheduler: suite %s could not start (%s)", suite_path, exc)
        return SuiteRun(
            suite_path=suite_path,
            error=f"{type(exc).__name__}: {exc}",
        )

    aggregate = _aggregate_suite(result, k)
    logger.debug(f"{suite_path = }, {result.exit_code = }, {len(result.runs) = }")
    return SuiteRun(suite_path=suite_path, result=result, aggregate=aggregate)


def _aggregate_suite(result: pipeline.SuiteResult, k: int) -> scoring.SuiteAggregate:
    """Aggregate the stored runs of one suite, restricted to its own cases."""
    case_ids: list[str] = []
    for case_run in result.runs:
        if case_run.case_id not in case_ids:
            case_ids.append(case_run.case_id)

    if not case_ids:
        logger.debug(f"{result.suite_path = }, no cases to aggregate")
        return scoring.SuiteAggregate(root=str(result.workspace_root), k=k)

    return scoring.aggregate_suite(
        result.workspace_root,
        k=k,
        case_ids=case_ids,
    )


def _suite_paths(suite_paths: Sequence[str | Path]) -> tuple[Path, ...]:
    """Normalise the suite sequence into unique paths, preserving order."""
    paths: list[Path] = []
    for entry in suite_paths:
        if not isinstance(entry, (str, Path)):
            raise SchedulerConfigError(
                f"suite paths must be paths, got {type(entry).__name__}"
            )
        candidate = Path(entry).expanduser()
        if candidate not in paths:
            paths.append(candidate)

    if not paths:
        raise SchedulerConfigError("no suites to run")

    logger.debug(f"{len(paths) = }")
    return tuple(paths)


def _workspace_root(root: str | Path | None) -> Path:
    """Resolve the evaluation workspace root without creating it."""
    explicit = Path(root) if root is not None else None
    resolved = Path(workspace.workspace_root(explicit))
    logger.debug(f"{resolved = }")
    return resolved


def _json_form(value: Any) -> Any:
    """Return the JSON-ready form of a record that exposes ``to_dict``."""
    if value is None:
        return None

    renderer = getattr(value, "to_dict", None)
    if callable(renderer):
        return renderer()
    return value
