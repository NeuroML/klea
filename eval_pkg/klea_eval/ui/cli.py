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


@app.command()
def run(
    suite: str = typer.Argument(..., help="Path to the JSONL suite to run."),
    workspace: str = typer.Option(
        ".",
        "--workspace",
        "-w",
        help="Directory to write run artefacts into.",
    ),
    base_url: str = typer.Option(
        "",
        "--base-url",
        "-u",
        envvar="KLEA_EVAL_BASE_URL",
        help="Base URL of the model endpoint (defaults to $KLEA_EVAL_BASE_URL).",
    ),
    endpoint: str = typer.Option(
        "/v1/chat/completions",
        "--endpoint",
        help="Model endpoint path appended to --base-url.",
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
) -> None:
    """Run a JSONL evaluation suite end to end (ADR-0056)."""
    # Deferred imports: the pipeline pulls in the model client, the HTTP stack
    # and the reporting renderers, which are slow to import and are not needed
    # to render --help or to run the lightweight commands.
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

    try:
        result = run_suite(
            suite,
            workspace_root=workspace,
            base_url=base_url,
            endpoint=endpoint,
            timeout=timeout,
            harness_seed=harness_seed,
            max_attempts=max_attempts,
            overwrite=overwrite,
        )
    except (PipelineConfigError, CaseValidationError) as error:
        # ``str()`` renders the precise fault; configuration faults exit 2.
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error
    except PipelineError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=2) from error

    for case_run in result.runs:
        passed = case_run.metrics is not None and case_run.metrics.task_success is True
        typer.echo(f"{case_run.case_id}: {'pass' if passed else 'fail'}")

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
    from klea_eval.reporting import write_report  # ty: ignore[unresolved-import]

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


if __name__ == "__main__":
    app()
