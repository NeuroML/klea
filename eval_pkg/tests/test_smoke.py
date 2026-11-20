# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""No-LLM smoke tests for the klea_eval scaffold.

These tests must stay fast, offline and dependency-light: they only verify that
the package imports, that it reports the expected version and that the Typer
application starts and renders its help text.
"""

import klea_eval
from klea_eval.ui.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def test_package_imports_and_reports_version() -> None:
    """The package imports cleanly and exposes the expected version."""
    assert klea_eval.__version__ == "0.1.0"


def test_cli_help_exits_zero() -> None:
    """Invoking the Typer app's help path exits successfully."""
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0


def test_cli_version_command_prints_version() -> None:
    """The lightweight version command prints the package version."""
    result = runner.invoke(app, ["version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output
