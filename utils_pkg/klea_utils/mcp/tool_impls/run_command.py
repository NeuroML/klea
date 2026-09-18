#!/usr/bin/env python3
"""
Shell command execution implementation for Klea MCP tools (ADR-0038).

File: klea_utils/mcp/tool_impls/run_command.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
import math
import os
import signal
from pathlib import Path
from typing import Any

from klea_utils.mcp.errors import PermissionDeniedError
from klea_utils.mcp.tool_impls.permission import check_path_access

logger = logging.getLogger(__name__)

#: Default per-command timeout in seconds.
DEFAULT_TIMEOUT_SECONDS = 30.0

#: Default ceiling for the per-command timeout, overridable via
#: :data:`MAX_TIMEOUT_ENV_VAR` (mirrors opencode's 10-minute bash cap).
DEFAULT_MAX_TIMEOUT_SECONDS = 600.0

#: Process environment variable that raises/lowers the timeout ceiling
#: (seconds).  A process env var, not an env-file/config field: it is an
#: operational bound (like ``KLEA_LOG_LEVEL``), not app configuration.
MAX_TIMEOUT_ENV_VAR = "KLEA_RUN_COMMAND_MAX_TIMEOUT"

#: Default cap on captured characters per stream (stdout, stderr).
DEFAULT_MAX_OUTPUT_CHARS = 100_000

#: Grace period between SIGTERM and SIGKILL when killing a timed-out process
#: group.
KILL_GRACE_SECONDS = 3.0


def _result(
    command: str,
    working_directory: str | None,
    *,
    returncode: int | None = None,
    stdout: str = "",
    stderr: str = "",
    truncated: bool = False,
    error: str = "",
) -> dict[str, Any]:
    """Build the standard command result dict.

    :param command: The command string that was run.
    :param working_directory: Working directory used, or ``None`` when the
        command never ran.
    :param returncode: Exit status, or ``None`` when the command never ran.
    :param stdout: Captured standard output (possibly truncated).
    :param stderr: Captured standard error (possibly truncated).
    :param truncated: Whether either stream was truncated.
    :param error: Empty when the command produced a result (any exit code);
        a message only for call-level failures (timeout, denied working
        directory, spawn failure) or a rejected argument.
    :returns: The result dict returned to the MCP wrapper.
    """
    return {
        "command": command,
        "working_directory": working_directory,
        "returncode": returncode,
        "stdout": stdout,
        "stderr": stderr,
        "truncated": truncated,
        "error": error,
    }


def max_timeout_seconds() -> float:
    """Return the effective timeout ceiling in seconds.

    Reads :data:`MAX_TIMEOUT_ENV_VAR` from the process environment (always a
    string) and casts it to a float; an unset, non-numeric, non-finite, or
    non-positive value falls back to :data:`DEFAULT_MAX_TIMEOUT_SECONDS` with
    a warning.

    :returns: The ceiling for ``timeout_seconds``.
    """
    raw = os.environ.get(MAX_TIMEOUT_ENV_VAR)
    if raw is None:
        return DEFAULT_MAX_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            f"Invalid {MAX_TIMEOUT_ENV_VAR}={raw!r}; "
            f"using {DEFAULT_MAX_TIMEOUT_SECONDS} s"
        )
        return DEFAULT_MAX_TIMEOUT_SECONDS
    if not math.isfinite(value) or value <= 0:
        logger.warning(
            f"Invalid (non-finite or non-positive) {MAX_TIMEOUT_ENV_VAR}={raw!r}; "
            f"using {DEFAULT_MAX_TIMEOUT_SECONDS} s"
        )
        return DEFAULT_MAX_TIMEOUT_SECONDS
    logger.debug(f"{MAX_TIMEOUT_ENV_VAR} = {value}")
    return value


async def _terminate_process_group(process: asyncio.subprocess.Process) -> None:
    """Terminate a process and its children (its process group).

    Sends SIGTERM to the group, waits :data:`KILL_GRACE_SECONDS`, then
    SIGKILLs if it is still alive.  Best-effort: a race where the process has
    already exited is ignored.

    :param process: The subprocess to terminate.
    """
    if process.returncode is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
        else:  # pragma: no cover - non-POSIX fallback
            process.terminate()
    except (ProcessLookupError, PermissionError):
        return
    try:
        await asyncio.wait_for(process.wait(), timeout=KILL_GRACE_SECONDS)
        return
    except TimeoutError:
        logger.warning("Command did not exit after SIGTERM; sending SIGKILL")
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        else:  # pragma: no cover - non-POSIX fallback
            process.kill()
    except (ProcessLookupError, PermissionError):
        return
    await process.wait()


async def run_command(
    command: str,
    working_directory: str | None = None,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS,
    project_root: str | None = None,
) -> dict[str, Any]:
    """Run a shell command and return its exit status and captured output.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool (see ``klea_utils.mcp.server.bundled_tools``).

    This is **not** a sandbox (ADR-0038): ``working_directory`` is checked
    against *project_root* like any other path argument, but the command can
    ignore it (``cd /``, absolute paths) and has the host user's filesystem,
    process, and network authority.  The command inherits the server's
    environment.  Access control relies on the tool's ``destructive`` /
    ``open_world`` annotations and the access level (ADR-0037).

    The process is run with ``start_new_session`` so it can be killed as a
    group on timeout, with stdin closed so an interactive command cannot
    hang.

    :param command: Shell command string to run.
    :param working_directory: Directory to run in; must resolve inside
        *project_root*.  Defaults to *project_root* (or the current working
        directory when *project_root* is ``None``).
    :param timeout_seconds: Seconds to wait before killing the process group.
        Must not exceed :func:`max_timeout_seconds`.
    :param max_output_chars: Maximum characters captured per stream.
    :param project_root: Boundary directory for the permission check.
        Defaults to the current working directory.
    :returns: dict with command, working_directory, returncode, stdout,
        stderr, truncated, error.  A non-zero ``returncode`` is a normal
        command result (many tools signal conditions with it); ``error`` is
        set only when the command could not be run or was killed (timeout,
        denied working directory, spawn failure).
    """
    logger.debug(
        f"Running command\n"
        f"{command = }\n"
        f"{working_directory = }\n"
        f"{timeout_seconds = }\n"
        f"{max_output_chars = }\n"
        f"{project_root = }"
    )

    if not command.strip():
        logger.warning("Empty command rejected")
        return _result(command, None, error="Empty command is not allowed.")

    if max_output_chars <= 0:
        max_output_chars = DEFAULT_MAX_OUTPUT_CHARS

    boundary = project_root
    cwd = (
        Path(working_directory)
        if working_directory
        else Path(boundary)
        if boundary
        else Path.cwd()
    )
    try:
        check_path_access(cwd, boundary)
    except PermissionDeniedError as exc:
        logger.warning(f"Permission denied for working directory {working_directory}")
        return _result(command, str(cwd), error=str(exc))
    cwd = cwd.expanduser().resolve()

    ceiling = max_timeout_seconds()
    if timeout_seconds <= 0:
        logger.warning(
            f"Invalid timeout {timeout_seconds}; using {DEFAULT_TIMEOUT_SECONDS}"
        )
        timeout_seconds = DEFAULT_TIMEOUT_SECONDS
    if timeout_seconds > ceiling:
        logger.warning(f"Requested timeout {timeout_seconds} exceeds ceiling {ceiling}")
        return _result(
            command,
            str(cwd),
            error=(
                f"timeout_seconds ({timeout_seconds:g}) exceeds the maximum "
                f"({ceiling:g} s). Lower it or raise {MAX_TIMEOUT_ENV_VAR}."
            ),
        )

    # Validate the working directory and start the process under one
    # ``OSError`` guard: ``is_dir()`` re-raises permission errors (only
    # ENOENT/ENOTDIR/EBADF/ELOOP are swallowed), so an unreadable path must
    # become a non-halting error like any other OS failure.
    try:
        if not cwd.is_dir():
            logger.warning(f"Not a directory: {cwd}")
            return _result(
                command,
                str(cwd),
                error=f"Working directory is not a directory: {cwd}",
            )
        process = await asyncio.create_subprocess_shell(
            command,
            cwd=str(cwd),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
    except OSError as exc:
        logger.warning(f"Could not use working directory or start command: {exc}")
        return _result(command, str(cwd), error=f"Could not start command: {exc}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            process.communicate(), timeout=timeout_seconds
        )
    except TimeoutError:
        logger.warning(f"Command timed out after {timeout_seconds} s: {command}")
        await _terminate_process_group(process)
        return _result(
            command,
            str(cwd),
            error=(
                f"Command timed out after {timeout_seconds:g} s and was killed. "
                "Retry with a larger timeout if it is expected to take longer."
            ),
        )

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    truncated = False
    if len(stdout) > max_output_chars:
        stdout = stdout[:max_output_chars]
        truncated = True
    if len(stderr) > max_output_chars:
        stderr = stderr[:max_output_chars]
        truncated = True

    returncode = process.returncode
    # A non-zero exit is a command *result*, not a tool failure: tools such as
    # ``diff``/``grep``/``test`` use exit codes semantically.  ``error`` (and
    # hence MCP ``isError`` via ``to_result``) is reserved for the command
    # never producing a result (timeout/denied/spawn), handled above; the
    # caller judges the outcome from ``returncode``/``stdout``/``stderr``.
    logger.debug(f"Command finished ({returncode}): {command}")

    return _result(
        command,
        str(cwd),
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        truncated=truncated,
        error="",
    )
