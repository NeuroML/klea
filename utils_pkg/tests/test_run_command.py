#!/usr/bin/env python3
"""
Tests for the run_command shell execution implementation (ADR-0038).

File: tests/test_run_command.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import shlex
import sys

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


async def test_working_directory_outside_project_denied(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    result = await run_command(
        "pwd", working_directory=str(outside), project_root=str(root)
    )

    assert result["returncode"] is None
    assert "denied" in result["error"].lower()


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
