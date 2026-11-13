# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Command line interface for the Klea evaluation harness.

``setup.cfg`` points the ``klea-eval`` console script at the module level
``app`` object defined here.

Nothing heavy is imported at module level: the harness, its runners and the
reporting stack cost seconds to import, and ``--help`` plus the lightweight
commands must stay instant.
"""

import logging

import typer

logger = logging.getLogger(__name__)

#: Accepted ``--log-level`` values, mapped to the :mod:`logging` constants.
_LOG_LEVELS: dict[str, int] = {
    "CRITICAL": logging.CRITICAL,
    "ERROR": logging.ERROR,
    "WARNING": logging.WARNING,
    "INFO": logging.INFO,
    "DEBUG": logging.DEBUG,
    "NOTSET": logging.NOTSET,
}

app = typer.Typer(
    name="klea-eval",
    help="Run, score and report on the Klea evaluations (ADRs 0056-0060).",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main(
    debug: bool = typer.Option(
        False,
        "--debug",
        help="Enable verbose debug logging.",
    ),
    log_level: str = typer.Option(
        "INFO",
        "--log-level",
        envvar="KLEA_LOG_LEVEL",
        help="Logging level (defaults to $KLEA_LOG_LEVEL).",
    ),
) -> None:
    """Shared setup for every ``klea-eval`` invocation."""
    level = (
        logging.DEBUG
        if debug
        else _LOG_LEVELS.get(log_level.strip().upper(), logging.INFO)
    )
    logging.basicConfig(level=level)
    logger.debug("klea-eval: start")


@app.command()
def version() -> None:
    """Print the installed klea-eval version."""
    # Deferred import: keeps the package import graph out of --help and out of
    # the commands that do not need the harness at all.
    from klea_eval import __version__

    typer.echo(__version__)


def _parse_model_overrides(entries: list[str]) -> dict[str, str]:
    """Parse repeatable ``ROLE=MODEL`` options into a role-to-model mapping."""
    overrides: dict[str, str] = {}
    for entry in entries:
        role, separator, model = entry.partition("=")
        role_text = role.strip()
        model_text = model.strip()
        if not separator or not role_text or not model_text:
            # A malformed pair is a run-configuration fault, so it exits like
            # the missing base URL: code 2, message on stderr.
            typer.echo(f"error: --model expects ROLE=MODEL, got '{entry}'", err=True)
            raise typer.Exit(code=2)
        overrides[role_text] = model_text

    return overrides


@app.command()
def run(
    suite: str = typer.Argument(..., help="Path to the JSONL suite to run."),
    workspace: str | None = typer.Option(
        None,
        "--workspace",
        "-w",
        help=(
            "Directory to write run artefacts into (defaults to "
            "$KLEA_EVAL_WORKSPACE, else the current directory)."
        ),
    ),
    base_url: str = typer.Option(
        "",
        "--base-url",
        "-u",
        envvar="KLEA_EVAL_BASE_URL",
        help="Base URL of the model endpoint (defaults to $KLEA_EVAL_BASE_URL).",
    ),
    endpoint: str = typer.Option(
        "/query/stream",
        "--endpoint",
        help=(
            "Model endpoint path appended to --base-url.  Defaults to "
            "/query/stream, the SSE route that klea-serve and klea-rag-serve "
            "both expose and the one klea_eval.runners.http targets; pass "
            "this flag for any other surface."
        ),
    ),
    timeout: float = typer.Option(
        60.0,
        "--timeout",
        help="Per-request timeout in seconds.",
    ),
    harness_seed: int = typer.Option(
        0,
        "--harness-seed",
        "-s",
        help="Seed for the deterministic harness.",
    ),
    max_attempts: int = typer.Option(
        3,
        "--max-attempts",
        help="Maximum attempts per case before giving up.",
    ),
    overwrite: bool = typer.Option(
        False,
        "--overwrite",
        help="Overwrite an existing workspace instead of failing.",
    ),
    model: list[str] = typer.Option(  # noqa: B008
        [],
        "-m",
        "--model",
        metavar="ROLE=MODEL",
        help=(
            "Override a model role for this run (repeatable), e.g. "
            "-m chat=openai:gpt-4o."
        ),
    ),
) -> None:
    """Run a JSONL evaluation suite end to end (ADR-0056)."""
    # Deferred imports: the pipeline pulls in the model client, the HTTP stack
    # and the reporting renderers, which are slow to import and are not needed
    # to render --help or to run the lightweight commands.
    import os

    from klea_eval.errors import CaseValidationError
    from klea_eval.pipeline import (
        PipelineConfigError,
        PipelineError,
        run_suite,
    )

    logger.debug("klea-eval: running suite %s", suite)
    if not base_url:
        typer.echo(
            "error: no base URL configured; pass --base-url or set $KLEA_EVAL_BASE_URL",
            err=True,
        )
        raise typer.Exit(code=2)

    overrides = _parse_model_overrides(model)
    env_workspace = os.environ.get("KLEA_EVAL_WORKSPACE", "").strip()
    workspace_root = workspace or env_workspace or "."
    logger.debug(f"{workspace_root = }")

    try:
        result = run_suite(
            suite,
            workspace_root=workspace_root,
            base_url=base_url,
            endpoint=endpoint,
            timeout=timeout,
            harness_seed=harness_seed,
            max_attempts=max_attempts,
            overwrite=overwrite,
            model_overrides=overrides or None,
        )
    except (PipelineConfigError, CaseValidationError) as error:
        # ``str()`` renders the precise fault; configuration faults exit 2.
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except PipelineError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    for case_run in result.runs:
        metrics = case_run.metrics
        if metrics is not None and metrics.task_success is None:
            label = "ungraded"
        elif metrics is not None and metrics.task_success is True:
            label = "pass"
        else:
            label = "fail"
        typer.echo(f"{case_run.case_id}: {label}")

    raise typer.Exit(code=result.exit_code)


@app.command()
def report(
    results: str = typer.Argument(..., help="Path to the collected results."),
    output: str = typer.Option(
        "-",
        "--output",
        "-o",
        help="Where to write the report; '-' means standard output.",
    ),
) -> None:
    """Render a report from already collected evaluation results."""
    # Deferred import: the reporting stack pulls in the renderers and is only
    # needed once a command actually renders something.
    from klea_eval.reporting import write_report

    logger.debug("klea-eval: rendering report for %s", results)
    write_report(results, output)


@app.command()
def validate(
    suite: str = typer.Argument(..., help="Path to the JSONL suite to validate."),
) -> None:
    """Validate a JSONL test suite without running it (ADR-0056)."""
    # Deferred imports: the loader only needs the standard library, but the CLI
    # keeps every command body light so importing the module (and rendering
    # --help) never walks the harness import graph.
    from klea_eval.cases import load_suite
    from klea_eval.errors import CaseValidationError

    logger.debug("klea-eval: validating suite %s", suite)
    try:
        loaded = load_suite(suite)
    except CaseValidationError as error:
        # ``str()`` renders ``<source>:<line>: <message>`` per ADR-0056.
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    typer.echo(f"{loaded.source}: {len(loaded.cases)} cases valid")


@app.command()
def show(
    run_id: str = typer.Argument(..., help="Identifier of the stored run to show."),
    workspace: str = typer.Option(
        "",
        "--workspace",
        "-w",
        help="Workspace holding the runs (defaults to $KLEA_EVAL_WORKSPACE).",
    ),
) -> None:
    """Show one stored run read back through the API (P8)."""
    # Deferred import: the API composes the results, metrics and scoring
    # modules (plus the workspace layer), which --help and the lightweight
    # commands must not pull in.
    from klea_eval.api import ApiError, get_run

    logger.debug("klea-eval: showing run %s", run_id)
    try:
        record = get_run(run_id, workspace or None)
    except ApiError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    outcome = "pass" if record.passed else "fail"
    typer.echo(f"{record.run_id}: {record.case_id} {record.status} {outcome}")


@app.command()
def runs(
    workspace: str = typer.Option(
        "",
        "--workspace",
        "-w",
        help="Workspace holding the runs (defaults to $KLEA_EVAL_WORKSPACE).",
    ),
    case_id: str = typer.Option(
        "",
        "--case-id",
        help="Only list the runs of this case.",
    ),
    status: str = typer.Option(
        "",
        "--status",
        help="Only list the runs with this status.",
    ),
) -> None:
    """List the stored runs from the run index (P8)."""
    # Deferred import: only the index reader is needed here, and the API module
    # stays out of the import graph of the lightweight commands.
    from klea_eval.api import list_runs

    logger.debug("klea-eval: listing runs")
    entries = list_runs(
        workspace or None,
        case_id=case_id or None,
        status=status or None,
    )
    for entry in entries:
        typer.echo(f"{entry.run_id} {entry.case_id} {entry.status} {entry.timestamp}")


@app.command()
def prune(
    workspace: str = typer.Option(
        "",
        "--workspace",
        "-w",
        help="Workspace holding the runs (defaults to $KLEA_EVAL_WORKSPACE).",
    ),
    keep: int = typer.Option(
        20,
        "--keep",
        "-k",
        help="Number of completed runs kept per case.",
    ),
) -> None:
    """Apply the ADR-0059 retention policy to the stored runs (P8)."""
    # Deferred import: retention purges directories and rewrites the index, so
    # it is only imported once this command actually runs.
    from klea_eval.api import ApiError, apply_retention

    logger.debug("klea-eval: pruning runs, keep=%d", keep)
    try:
        result = apply_retention(workspace or None, keep=keep)
    except ApiError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    typer.echo(
        f"kept {len(result.kept_runs)} runs, pruned {len(result.pruned_runs)} runs"
    )


@app.command()
def summary(
    workspace: str = typer.Option(
        "",
        "--workspace",
        "-w",
        help="Workspace holding the runs (defaults to $KLEA_EVAL_WORKSPACE).",
    ),
    k: int = typer.Option(
        1,
        "--k",
        help="Attempts per case inspected by pass_at_k.",
    ),
) -> None:
    """Print the suite-level aggregate of the stored runs (P8)."""
    # Deferred import: the aggregate reads the per-run metrics, which are only
    # needed once this command actually summarises something.
    from klea_eval.api import suite_summary

    logger.debug("klea-eval: summarising the suite, k=%d", k)
    aggregate = suite_summary(workspace or None, k=k)
    typer.echo(f"suite_score: {aggregate.suite_score:.4f}")
    typer.echo(f"pass_at_k: {aggregate.pass_at_k:.4f}")


if __name__ == "__main__":
    app()
