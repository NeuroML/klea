"""Spawn configuration for the bundled Klea MCP server.

This module centralises how the bundled MCP server process is described to the
client/transport layer.  :func:`bundled_server_config` builds the mapping that
is handed to the process spawner.

The contract is deliberately conservative so that callers which previously
assembled this configuration by hand keep exactly the same behaviour:

* ``env`` is only emitted when the caller supplies one.  The inherited
  environment is the base and the caller's values win.
* ``VIRTUAL_ENV``, ``PYTHONHOME`` and ``PYTHONPATH`` are stripped from the
  *inherited* environment, so the server is never spawned inside whatever
  virtual environment happened to be active when Klea was started.
* ``cwd`` is only emitted when the caller passes it explicitly.
* The interpreter can be pinned explicitly; otherwise the interpreter running
  the current process is used.
* ``include_tags``/``exclude_tags`` are forwarded verbatim (as lists) so the
  server applies its own tag filter.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

#: Variables that would leak a host virtual environment into the spawned server.
STRIPPED_ENV_VARS: tuple[str, ...] = ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH")

#: Module executed to run the bundled server.
SERVER_MODULE = "klea_utils.mcp.server.bundled"


def _strip_host_venv(base: Mapping[str, str]) -> dict[str, str]:
    """Return ``base`` without the host virtual-environment variables."""
    return {key: value for key, value in base.items() if key not in STRIPPED_ENV_VARS}


def _merge_env(env: Mapping[str, str] | None) -> dict[str, str] | None:
    """Merge ``env`` on top of the stripped inherited environment.

    Caller-provided values win over inherited ones.  ``None`` is returned when
    no explicit environment was given, so callers can omit the key entirely and
    keep the process-spawning behaviour unchanged.
    """
    if env is None:
        return None
    merged = _strip_host_venv(os.environ)
    merged.update(env)
    return merged


def _as_optional_path(value: str | os.PathLike[str] | None) -> str | None:
    """Normalise ``value`` to ``str`` while preserving ``None``."""
    return None if value is None else os.fspath(value)


def bundled_server_config(
    *,
    env: Mapping[str, str] | None = None,
    cwd: str | os.PathLike[str] | None = None,
    include_tags: Sequence[str] | None = None,
    exclude_tags: Sequence[str] | None = None,
    interpreter: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Build the spawn configuration for the bundled MCP server.

    Args:
        env: Environment overrides for the server process.  They are merged on
            top of the inherited environment (minus the virtual-environment
            variables); the caller's values win.
        cwd: Working directory for the server process.  Emitted only when it is
            not ``None``.
        include_tags: Tool tags forwarded to the server's tag filter.
        exclude_tags: Tool tags excluded via the server's tag filter.
        interpreter: Python interpreter used to run the server.  Defaults to
            the interpreter running the current process.

    Returns:
        A mapping describing the process to spawn.  The ``env`` and ``cwd``
        keys are omitted when they are not provided, preserving the behaviour
        of callers that built this configuration by hand.
    """
    python = _as_optional_path(interpreter) or sys.executable

    config: dict[str, Any] = {
        "command": python,
        "args": ["-m", SERVER_MODULE],
    }

    merged_env = _merge_env(env)
    if merged_env is not None:
        config["env"] = merged_env

    working_dir = _as_optional_path(cwd)
    if working_dir is not None:
        config["cwd"] = working_dir

    if include_tags is not None:
        config["include_tags"] = list(include_tags)
    if exclude_tags is not None:
        config["exclude_tags"] = list(exclude_tags)

    return config
