#!/usr/bin/env python3
"""
Manual end-to-end CLI smoke tests.

Runs each scenario in ``tasks.py`` through ``klea cli --single-query`` and
prints the full transcript plus the resulting workspace for observation.
Selection uses normal pytest options, for example::

    pytest e2e/ --collect-only -q      # list scenarios
    pytest e2e/ -k create_file         # one scenario
    pytest e2e/ -m e2e_tools           # a feature group
    pytest e2e/ -k "not missing_file"  # deselect

See ``e2e/README.md``.  These are not part of the default/CI suite.

File: e2e/test_e2e.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import subprocess
from pathlib import Path

import pytest
from platformdirs import PlatformDirs
from tasks import TASKS, E2ETask

_PARAMS = [
    pytest.param(
        task,
        id=task.id,
        marks=[
            pytest.mark.e2e,
            *(getattr(pytest.mark, f"e2e_{tag}") for tag in task.tags),
        ],
    )
    for task in TASKS
]


def _server_log_dir() -> str:
    return PlatformDirs("klea-agent").user_data_dir


def _print_report(
    task: E2ETask,
    workspace: Path,
    result: subprocess.CompletedProcess[str],
    output: str,
) -> None:
    print("\n" + "=" * 72)
    print(
        f"E2E {task.id}  (tags: {', '.join(task.tags) or '-'}; expect: {task.expect})"
    )
    print(f"query: {task.query}")
    print(f"exit: {result.returncode}")
    print(f"workspace: {workspace}")
    print("--- CLI output " + "-" * 57)
    print(output.rstrip() or "(no output)")
    print("--- workspace files " + "-" * 52)
    files = [p for p in sorted(workspace.rglob("*")) if p.is_file()]
    if not files:
        print("  (none)")
    for path in files:
        print(f"  {path.relative_to(workspace)} ({path.stat().st_size} bytes)")
    print(f"--- server logs: {_server_log_dir()}")
    print("=" * 72 + "\n")


@pytest.mark.parametrize("task", _PARAMS, indirect=True)
def test_e2e(task: E2ETask, workspace: Path, run_cli) -> None:
    """Run one scenario and report; assert only when the task opts in."""
    result = run_cli(workspace, task.query, task.timeout)
    output = (result.stdout or "") + (result.stderr or "")
    _print_report(task, workspace, result, output)

    if task.expect == "ok":
        assert result.returncode == 0, f"klea CLI exited with {result.returncode}"
        assert "(ERROR)" not in (result.stdout or ""), "the CLI reported an error event"

    if task.check is not None:
        task.check(workspace, output)
