#!/usr/bin/env python3
"""
Tests for the run_command shell execution implementation (ADR-0038).

File: tests/test_run_command.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
import os
import shlex
import signal
import sys

import pytest
from klea_utils.mcp.tool_impls.run_command import (
    DEFAULT_MAX_TIMEOUT_SECONDS,
    MAX_TIMEOUT_ENV_VAR,
    max_timeout_seconds,
    run_command,
)


async def test_runs_command_and_captures_output(tmp_path):
    result = await run_command(
        "echo hello", working_directory=str(tmp_path), project_root=str(tmp_path)
    )
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]
    assert result["error"] == ""
    assert result["working_directory"] == str(tmp_path.resolve())


async def test_nonzero_exit_is_a_normal_result(tmp_path):
    """A non-zero exit is reported via ``returncode``, not as an error.

    Many tools signal conditions with exit codes (``diff`` = 1, ``grep`` = 1
    on no match, a failing test), so the command result is returned normally
    and the caller judges it (ADR-0038).
    """
    result = await run_command(
        "exit 3", working_directory=str(tmp_path), project_root=str(tmp_path)
    )
    assert result["returncode"] == 3
    assert result["error"] == ""
    assert result["stdout"] == ""


async def test_nonzero_exit_keeps_output(tmp_path):
    """A command that writes output and exits non-zero still returns it."""
    result = await run_command(
        "echo out; echo err >&2; exit 1",
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
    )
    assert result["returncode"] == 1
    assert result["error"] == ""
    assert "out" in result["stdout"]
    assert "err" in result["stderr"]


async def test_display_shell_block_includes_command_output_and_exit(tmp_path):
    """The chat display is one text/x-shell block with command/output/exit."""
    result = await run_command(
        "echo out; echo err >&2; exit 1",
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
    )
    display = result["display"]
    assert display["mime"] == "text/x-shell"
    assert display["data"].splitlines()[0] == "$ echo out; echo err >&2; exit 1"
    assert "out" in display["data"]
    assert "--- stderr ---" in display["data"]
    assert "err" in display["data"]
    assert display["data"].rstrip().endswith("[exit 1]")
    assert display["meta"] == {
        "command": "echo out; echo err >&2; exit 1",
        "returncode": 1,
    }


async def test_display_silent_success_has_no_block(tmp_path):
    """A command with no output and a zero exit produces no chat block."""
    result = await run_command(
        "true", working_directory=str(tmp_path), project_root=str(tmp_path)
    )
    assert result["returncode"] == 0
    assert result["display"] is None


async def test_display_nonzero_exit_without_output(tmp_path):
    """A silent non-zero exit still produces a block, so it is visible."""
    result = await run_command(
        "exit 3", working_directory=str(tmp_path), project_root=str(tmp_path)
    )
    display = result["display"]
    assert display["mime"] == "text/x-shell"
    assert display["data"] == "$ exit 3\n[exit 3]"
    assert display["meta"]["returncode"] == 3


async def test_display_error_is_shown(tmp_path):
    """A call-level error (invalid cwd) is shown in the block."""
    root = tmp_path / "root"
    root.mkdir()
    not_a_dir = root / "file.txt"
    not_a_dir.write_text("x")
    result = await run_command(
        "pwd", working_directory=str(not_a_dir), project_root=str(root)
    )
    display = result["display"]
    assert display["mime"] == "text/x-shell"
    assert "--- error ---" in display["data"]
    assert display["meta"]["returncode"] is None


async def test_working_directory_used(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    result = await run_command(
        "pwd", working_directory=str(sub), project_root=str(tmp_path)
    )
    assert result["returncode"] == 0
    assert str(sub.resolve()) in result["stdout"]


async def test_default_working_directory_is_project_root(tmp_path):
    result = await run_command("pwd", project_root=str(tmp_path))
    assert result["returncode"] == 0
    assert str(tmp_path.resolve()) in result["stdout"]


async def test_working_directory_must_be_directory(tmp_path):
    """A file as working_directory is a non-halting error, not an exception."""
    a_file = tmp_path / "file.txt"
    a_file.write_text("x")
    result = await run_command(
        "pwd", working_directory=str(a_file), project_root=str(tmp_path)
    )
    assert result["returncode"] is None
    assert "not a directory" in result["error"].lower()


async def test_empty_command_rejected(tmp_path):
    result = await run_command("   ", project_root=str(tmp_path))
    assert result["returncode"] is None
    assert result["error"]


async def test_timeout_kills_command(tmp_path):
    result = await run_command(
        "sleep 5",
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
        timeout_seconds=1.0,
    )
    assert result["returncode"] is None
    assert "timed out" in result["error"]


async def _wait_for_death(pid: int, *, attempts: int = 40) -> bool:
    """Return whether *pid* exits within a short grace, then force-clean it.

    :param pid: Process id to poll with signal 0.
    :param attempts: Number of 50 ms polls before giving up.
    :returns: ``True`` when the process is gone, ``False`` otherwise (the
        process is SIGKILLed before returning so a failure does not leak).
    """
    for _ in range(attempts):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        await asyncio.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    return False


async def test_timeout_kills_sigterm_ignoring_grandchild(tmp_path):
    """A grandchild that ignores SIGTERM is still killed on timeout.

    The direct child (the shell) exits on SIGTERM, but the backgrounded
    grandchild ignores it.  The tool must not return as soon as the direct
    child is reaped; it has to escalate to SIGKILL for the whole process
    group, or the grandchild leaks.
    """
    pidfile = tmp_path / "grandchild.pid"
    command = (
        f"sh -c 'trap \"\" TERM; sleep 30' "
        f"& echo $! > {shlex.quote(str(pidfile))}; sleep 30"
    )
    result = await run_command(
        command,
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
        timeout_seconds=1.0,
    )
    assert result["returncode"] is None
    assert "timed out" in result["error"]

    pid = int(pidfile.read_text().strip())
    assert await _wait_for_death(pid), f"grandchild {pid} survived the timeout kill"


async def test_cancellation_kills_process_group(tmp_path):
    """Cancelling ``run_command`` kills the spawned process group.

    A client-side call timeout (or graph cancellation) cancels the coroutine
    while it is awaiting the child; the process group must still be
    terminated instead of leaking.
    """
    pidfile = tmp_path / "shell.pid"
    command = f"echo $$ > {shlex.quote(str(pidfile))}; sleep 30"

    task = asyncio.create_task(
        run_command(
            command,
            working_directory=str(tmp_path),
            project_root=str(tmp_path),
            timeout_seconds=30.0,
        )
    )
    for _ in range(200):
        if pidfile.exists() and pidfile.read_text().strip():
            break
        await asyncio.sleep(0.02)
    pid = int(pidfile.read_text().strip())

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _wait_for_death(pid), f"process {pid} survived cancellation"


async def test_output_truncated(tmp_path):
    command = f"{shlex.quote(sys.executable)} -c \"print('x' * 1000)\""
    result = await run_command(
        command,
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
        max_output_chars=100,
    )
    assert result["returncode"] == 0
    assert result["truncated"] is True
    assert len(result["stdout"]) == 100


async def test_stdin_is_closed(tmp_path):
    """``cat`` with no stdin returns at EOF instead of blocking to timeout."""
    result = await run_command(
        "cat",
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
        timeout_seconds=5.0,
    )
    assert result["returncode"] == 0
    assert result["stdout"] == ""


async def test_timeout_above_ceiling_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv(MAX_TIMEOUT_ENV_VAR, "2")
    result = await run_command(
        "echo hi",
        working_directory=str(tmp_path),
        project_root=str(tmp_path),
        timeout_seconds=10.0,
    )
    assert result["returncode"] is None
    assert MAX_TIMEOUT_ENV_VAR in result["error"]


def test_max_timeout_env_override(monkeypatch):
    monkeypatch.setenv(MAX_TIMEOUT_ENV_VAR, "1234")
    assert max_timeout_seconds() == 1234.0


def test_max_timeout_invalid_env_falls_back(monkeypatch, caplog):
    monkeypatch.setenv(MAX_TIMEOUT_ENV_VAR, "not-a-number")
    with caplog.at_level(logging.WARNING):
        assert max_timeout_seconds() == DEFAULT_MAX_TIMEOUT_SECONDS
    assert MAX_TIMEOUT_ENV_VAR in caplog.text


def test_max_timeout_non_finite_env_falls_back(monkeypatch, caplog):
    """``float`` accepts inf/nan; the ceiling must not."""
    for raw in ("inf", "-inf", "nan"):
        monkeypatch.setenv(MAX_TIMEOUT_ENV_VAR, raw)
        with caplog.at_level(logging.WARNING):
            assert max_timeout_seconds() == DEFAULT_MAX_TIMEOUT_SECONDS


def test_max_timeout_default(monkeypatch):
    monkeypatch.delenv(MAX_TIMEOUT_ENV_VAR, raising=False)
    assert max_timeout_seconds() == DEFAULT_MAX_TIMEOUT_SECONDS
