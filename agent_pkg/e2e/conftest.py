#!/usr/bin/env python3
"""
Fixtures for the manual end-to-end CLI suite.

Each scenario runs the real ``klea`` CLI in a disposable scratch directory.
Because the CLI spawns the API server as a subprocess, the server inherits
that working directory and the bundled file tools are therefore bounded to
the scratch directory - the agent never sees real files.

The suite is opt-in (excluded from the default pytest run via ``testpaths``)
and is skipped unless a model and API key are present (use ``e2e/run.sh``).

File: e2e/conftest.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import os
import shutil
import socket
import subprocess
from pathlib import Path
from typing import Any

import pytest
from tasks import E2ETask

#: Base directory for scratch workspaces (override with KLEA_E2E_WORKDIR).
_WORKDIR_ENV = "KLEA_E2E_WORKDIR"
_DEFAULT_WORKDIR = "/tmp/opencode/klea-e2e"

#: Global wall-clock cap for a CLI run; overrides the per-task timeout.
_TIMEOUT_ENV = "KLEA_E2E_TIMEOUT"


def _base_workdir() -> Path:
    """Return the scratch base, refusing obviously dangerous locations."""
    base = Path(os.environ.get(_WORKDIR_ENV, _DEFAULT_WORKDIR)).expanduser().resolve()
    for dangerous in (Path.home().resolve(), Path.cwd().resolve()):
        if base == dangerous:
            raise RuntimeError(
                f"Refusing to use {base} as the E2E scratch base; set "
                f"{_WORKDIR_ENV} to a throwaway directory"
            )
    base.mkdir(parents=True, exist_ok=True)
    return base


def _free_port() -> int:
    """Return an ephemeral localhost port for the spawned server."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session", autouse=True)
def require_backend() -> None:
    """Skip the whole suite unless the CLI and a model backend are present."""
    if shutil.which("klea") is None:
        pytest.skip("klea CLI not found; install klea_agent (uv pip install -e .)")
    if not (
        os.environ.get("KLEA_AGENT_PLAN_MODEL")
        or os.environ.get("KLEA_AGENT_CHAT_MODEL")
    ):
        pytest.skip("set KLEA_AGENT_PLAN_MODEL/KLEA_AGENT_CHAT_MODEL (use e2e/run.sh)")
    if not os.environ.get("OPENAI_API_KEY"):
        pytest.skip("OPENAI_API_KEY is not set (use e2e/run.sh)")


@pytest.fixture
def task(request: pytest.FixtureRequest) -> E2ETask:
    """Return the parametrized scenario (see ``test_e2e.py``)."""
    return request.param  # type: ignore[attr-defined]


@pytest.fixture
def workspace(task: E2ETask) -> Path:
    """Create a fresh scratch workspace and seed it for the task.

    Writes an empty ``klea_agent.json`` (the config file is required; ``{}``
    means all defaults).  The env file is deliberately absent: model settings
    come from the process environment (set by ``run.sh``).
    """
    path = _base_workdir() / task.id
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    (path / "klea_agent.json").write_text("{}\n")
    if task.setup is not None:
        task.setup(path)
    return path


@pytest.fixture
def run_cli():
    """Return a callable that runs one single query in a workspace."""

    def _run(
        workspace: Path, query: str, timeout: int
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        # A global wall-clock cap (KLEA_E2E_TIMEOUT) overrides the per-task
        # timeout so a whole suite or one scenario can be given more headroom
        # without editing tasks.py.
        timeout = int(os.environ.get(_TIMEOUT_ENV, timeout))
        # Point the (optional) env file at a non-existent scratch path so the
        # run is deterministic: model settings come from the process env.
        env["KLEA_AGENT_ENV_FILE"] = str(workspace / "klea_agent.env")
        env["KLEA_AGENT_APP_CONFIG_FILE"] = "klea_agent.json"
        cmd: list[Any] = [
            shutil.which("klea") or "klea",
            "cli",
            "--single-query",
            query,
            "--debug",
            # A unique port per run so the CLI spawns its own server (with this
            # workspace as cwd) rather than reusing an existing one.
            "--server",
            f"http://127.0.0.1:{_free_port()}",
        ]
        return subprocess.run(
            cmd,
            cwd=workspace,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    return _run
