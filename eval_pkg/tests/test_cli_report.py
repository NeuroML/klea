# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline tests for the ``klea-eval report`` command (ADR-0060).

``report`` is the only command whose body defers
``from klea_eval.reporting import write_report``: rendering ``--help`` and every
other subcommand never import the reporting stack.  These tests therefore invoke
the command itself through the Typer runner, so the deferred import, the
renderer and the stored records it reads are all exercised.  A missing or renamed
symbol in that import then fails here instead of passing unnoticed.
"""

from __future__ import annotations

import json
from pathlib import Path

from klea_eval import metrics, results, workspace
from klea_eval.ui.cli import app
from typer.testing import CliRunner

runner = CliRunner()


def _seed_run(root: Path, run_id: str = "run-seed", case_id: str = "case-a") -> None:
    """Publish and index one completed run carrying a stored ``task_success``."""
    staging = workspace.prepare_run(run_id, root)
    envelope = {
        "record_type": metrics.RECORD_TYPE_RUN,
        "metrics": {"task_success": True},
    }
    workspace.metrics_path(staging).write_text(
        json.dumps(envelope) + "\n", encoding="utf-8"
    )

    metadata = results.RunMetadata(
        run_id=run_id,
        case_id=case_id,
        status=metrics.STATUS_COMPLETED,
        harness_seed="0",
        case_version="1.0",
        expects_grade=False,
    )
    stamped = results.write_metadata(staging, metadata)
    workspace.publish_run(run_id, root)
    results.append_index_entry(results.to_index_entry(stamped), root)


def test_report_renders_a_seeded_run_to_stdout(tmp_path: Path) -> None:
    """The command body runs and renders the index line plus the counters."""
    _seed_run(tmp_path)

    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert "ImportError" not in result.output
    assert f"Evaluation report for {tmp_path}" in result.output
    assert "run-seed case=case-a status=completed" in result.output
    assert "completed=1 failed=0" in result.output


def test_report_writes_the_rendered_report_to_a_file(tmp_path: Path) -> None:
    """``--output`` writes the same rendering to a file, creating parents."""
    _seed_run(tmp_path)
    target = tmp_path / "reports" / "suite.txt"

    result = runner.invoke(app, ["report", str(tmp_path), "-o", str(target)])

    assert result.exit_code == 0, result.output
    rendered = target.read_text(encoding="utf-8")
    assert f"Evaluation report for {tmp_path}" in rendered
    assert "run-seed case=case-a status=completed" in rendered


def test_report_of_an_empty_workspace_is_all_zero(tmp_path: Path) -> None:
    """A workspace with no stored runs renders the header and zero counters."""
    result = runner.invoke(app, ["report", str(tmp_path)])

    assert result.exit_code == 0, result.output
    assert f"Evaluation report for {tmp_path}" in result.output
    assert "completed=0 failed=0" in result.output
    assert "total=0 unindexed=0" in result.output
