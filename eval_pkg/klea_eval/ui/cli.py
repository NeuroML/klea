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

app = typer.Typer(
    name="klea-eval",
    help="Run, score and report on the Klea evaluations (ADRs 0056-0060).",
    no_args_is_help=True,
    add_completion=False,
)


@app.callback()
def main() -> None:
    """Shared setup for every ``klea-eval`` invocation."""
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
    scenario: str = typer.Argument(..., help="Evaluation scenario to run."),
    output: str = typer.Option(
        "-",
        "--output",
        "-o",
        help="Where to write the report; '-' means standard output.",
    ),
) -> None:
    """Run a single evaluation scenario end to end."""
    # Deferred import: the runners pull in the model client and the HTTP stack,
    # which are slow to import and are not needed to render the help text.
    # Deferred import, same reason: report rendering lives in its own stack and
    # is only touched once results exist.
    from klea_eval.reporting import write_report  # ty: ignore[unresolved-import]
    from klea_eval.runners import run_scenario  # ty: ignore[unresolved-import]

    logger.debug("klea-eval: running scenario %s", scenario)
    result = run_scenario(scenario)
    write_report(result, output)


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


if __name__ == "__main__":
    app()
