#!/usr/bin/env python3
"""
Privilege guard for Klea-authored MCP tools.

Klea-authored tools (the bundled server and the NeuroML server) refuse to run
when the server process has root privileges, unless the operator explicitly
opts in via an environment variable.  This is defense-in-depth for deployments
that accidentally run the server as uid 0 (including containers, which default
to root): a tool running as root can read/write/execute far beyond the
project.  Third-party MCP servers do not use this guard; their privileges are
the operator's choice (ADR-0007 trust model).  See ADR-0038.

File: klea_utils/mcp/privilege.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os

logger = logging.getLogger(__name__)

#: Process environment variable that allows Klea-authored tools to run as
#: root.  Must be truthy (``1``/``true``/``yes``/``on``); anything else (or
#: unset) refuses root execution.  A process env var, so it must reach the
#: MCP server subprocess (it is inherited from the app's environment).
ALLOW_ROOT_ENV_VAR = "KLEA_ALLOW_ROOT_TOOLS"

_TRUTHY = frozenset({"1", "true", "yes", "on"})


def running_as_root() -> bool:
    """Return whether the current process has root (effective uid 0).

    Windows-safe: non-POSIX systems have no ``geteuid`` and are treated as
    non-root.

    :returns: True when ``os.geteuid() == 0``.
    """
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)


def allow_root_tools() -> bool:
    """Return whether root execution is explicitly allowed.

    Reads :data:`ALLOW_ROOT_ENV_VAR`; only a truthy value
    (``1``/``true``/``yes``/``on``, case-insensitive) opts in.

    :returns: True when the override is set to a truthy value.
    """
    raw = os.environ.get(ALLOW_ROOT_ENV_VAR)
    return bool(raw and raw.strip().lower() in _TRUTHY)


def root_denial_message() -> str:
    """Return the non-halting denial message for a root-refused tool call.

    :returns: A message naming the override variable.
    """
    return (
        "Refusing to run this tool as root (uid 0): Klea-authored tools do "
        f"not run with root privileges by default. Set {ALLOW_ROOT_ENV_VAR}=1 "
        "to override (only on a trusted, isolated host)."
    )


def warn_if_root(logger: logging.Logger) -> None:
    """Log a warning when the current process runs as root.

    Called once at app/server startup so operators are aware that tools will
    either be refused or execute with full root privileges.

    :param logger: Logger to emit the warning on.
    """
    if not running_as_root():
        return
    if allow_root_tools():
        logger.warning(
            "Running as root (uid 0) with %s set: tools execute with full "
            "root privileges.",
            ALLOW_ROOT_ENV_VAR,
        )
    else:
        logger.warning(
            "Running as root (uid 0): Klea-authored tools will refuse to run. "
            "Prefer a non-root user, or set %s=1 to override.",
            ALLOW_ROOT_ENV_VAR,
        )
