# Copyright (c) 2024 Klea. All rights reserved.
"""Server-side toggle commands for path checking and human-in-the-loop.

This module exposes three workflow commands that persist into the graph
checkpoint:

* ``pathcheck``        - ``strict|declared|off``
* ``pathcheck-stance`` - ``deny|allow``
* ``hitl``             - ``on|off``

Each command validates its argument and refuses values that conflict with a
process-level ceiling (``KLEA_AGENT_PATH_CHECK``, ``KLEA_AGENT_PATH_CHECK_STANCE``
and ``KLEA_AGENT_HITL``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any

from klea_utils.commands.common import Command
from klea_utils.commands.graph import GraphCommandResult
from klea_utils.mcp.access import (
    parse_hitl_enabled,
    parse_path_check_mode,
    parse_path_check_stance,
)

logger = logging.getLogger(__name__)


PATH_CHECK_MODE_ENV = "KLEA_AGENT_PATH_CHECK"
PATH_CHECK_STANCE_ENV = "KLEA_AGENT_PATH_CHECK_STANCE"
HITL_ENV = "KLEA_AGENT_HITL"


PATHCHECK_COMMAND = Command(
    name="pathcheck",
    side="server",
    klass="workflow",
    persists="checkpoint",
    summary="Show or set the path-check mode used for filesystem access.",
    arg_hint="strict|declared|off",
)

PATHCHECK_STANCE_COMMAND = Command(
    name="pathcheck-stance",
    side="server",
    klass="workflow",
    persists="checkpoint",
    summary="Show or set whether undeclared paths are denied or allowed.",
    arg_hint="deny|allow",
)

HITL_COMMAND = Command(
    name="hitl",
    side="server",
    klass="workflow",
    persists="checkpoint",
    summary="Show or toggle the human-in-the-loop confirmation gate.",
    arg_hint="on|off",
)


TOGGLE_COMMANDS: list[Command] = [
    PATHCHECK_COMMAND,
    PATHCHECK_STANCE_COMMAND,
    HITL_COMMAND,
]


def _argument(args: Any) -> str:
    """Extract the raw argument string from the handler input."""
    if args is None:
        return ""
    if isinstance(args, str):
        return args.strip()
    if isinstance(args, dict):
        for key in ("argument", "arg", "value", "args"):
            candidate = args.get(key)
            if isinstance(candidate, str):
                return candidate.strip()
            if isinstance(candidate, (list, tuple)) and candidate:
                first = candidate[0]
                if isinstance(first, str):
                    return first.strip()
        return ""
    return str(args).strip()


def _result(
    updates: dict[str, Any],
    message: str,
    summary: str,
    details: dict[str, Any] | None = None,
) -> GraphCommandResult:
    """Build a graph command result for a toggle command."""
    return GraphCommandResult(
        updates=updates,
        message=message,
        summary=summary,
        details=details or {},
    )


def _error(message: str, summary: str, **details: Any) -> GraphCommandResult:
    """Build an error result without raising."""
    logger.debug(f"{message = }")
    return _result({}, message, summary, details)


def _parse(parser: Callable[[str], Any], raw: str) -> Any:
    """Run a parser, normalising the way it signals invalid input."""
    try:
        return parser(raw)
    except (ValueError, TypeError) as exc:
        raise ValueError(str(exc)) from exc


def _ceiling(variable: str, parser: Callable[[str], Any]) -> Any:
    """Return the parsed process-level ceiling, if one is set."""
    raw = os.environ.get(variable, "").strip()
    if not raw:
        return None
    try:
        return _parse(parser, raw)
    except ValueError:
        return raw


def _same(left: Any, right: Any) -> bool:
    """Compare two parsed toggle values without assuming their type."""
    return str(left).strip().lower() == str(right).strip().lower()


def _refusal(
    variable: str, requested: Any, enforced: Any, summary: str
) -> GraphCommandResult:
    """Return a refusal that names the variable and the enforced value."""
    message = (
        f"Cannot set {summary} to {requested!r}: "
        f"{variable}={enforced!r} is enforced for this process."
    )
    return _error(message, summary, variable=variable, enforced=enforced)


def handle_pathcheck(state: Any, args: Any = None) -> GraphCommandResult:
    """Handle the ``pathcheck`` command."""
    argument = _argument(args)
    logger.debug(f"{argument = }")
    if not argument:
        return _error(
            "pathcheck expects an argument: strict, declared or off.",
            "pathcheck",
            expected="strict|declared|off",
        )
    try:
        mode = _parse(parse_path_check_mode, argument)
    except ValueError as exc:
        return _error(
            f"Invalid path-check mode {argument!r}: {exc}",
            "pathcheck",
            expected="strict|declared|off",
        )
    if mode is None:
        return _error(
            f"Invalid path-check mode {argument!r}.",
            "pathcheck",
            expected="strict|declared|off",
        )

    enforced = _ceiling(PATH_CHECK_MODE_ENV, parse_path_check_mode)
    if enforced is not None and not _same(enforced, mode):
        return _refusal(PATH_CHECK_MODE_ENV, mode, enforced, "pathcheck")

    return _result(
        {"path_check_mode": mode},
        f"Path-check mode set to {mode}.",
        "pathcheck",
        {"path_check_mode": mode},
    )


def handle_pathcheck_stance(state: Any, args: Any = None) -> GraphCommandResult:
    """Handle the ``pathcheck-stance`` command."""
    argument = _argument(args)
    logger.debug(f"{argument = }")
    if not argument:
        return _error(
            "pathcheck-stance expects an argument: deny or allow.",
            "pathcheck-stance",
            expected="deny|allow",
        )
    try:
        stance = _parse(parse_path_check_stance, argument)
    except ValueError as exc:
        return _error(
            f"Invalid path-check stance {argument!r}: {exc}",
            "pathcheck-stance",
            expected="deny|allow",
        )
    if stance is None:
        return _error(
            f"Invalid path-check stance {argument!r}.",
            "pathcheck-stance",
            expected="deny|allow",
        )

    enforced = _ceiling(PATH_CHECK_STANCE_ENV, parse_path_check_stance)
    if enforced is not None and not _same(enforced, stance):
        return _refusal(PATH_CHECK_STANCE_ENV, stance, enforced, "pathcheck-stance")

    return _result(
        {"path_check_stance": stance},
        f"Path-check stance set to {stance}.",
        "pathcheck-stance",
        {"path_check_stance": stance},
    )


def handle_hitl(state: Any, args: Any = None) -> GraphCommandResult:
    """Handle the ``hitl`` command."""
    argument = _argument(args)
    logger.debug(f"{argument = }")
    if not argument:
        return _error(
            "hitl expects an argument: on or off.",
            "hitl",
            expected="on|off",
        )
    try:
        enabled = _parse(parse_hitl_enabled, argument)
    except ValueError as exc:
        return _error(
            f"Invalid hitl value {argument!r}: {exc}",
            "hitl",
            expected="on|off",
        )
    if enabled is None:
        return _error(
            f"Invalid hitl value {argument!r}.",
            "hitl",
            expected="on|off",
        )

    enforced = _ceiling(HITL_ENV, parse_hitl_enabled)
    if enforced is not None and not _same(enforced, enabled):
        return _refusal(HITL_ENV, enabled, enforced, "hitl")

    return _result(
        {"hitl_enabled": enabled},
        f"Human-in-the-loop set to {enabled}.",
        "hitl",
        {"hitl_enabled": enabled},
    )


TOGGLE_HANDLERS: dict[str, Callable[..., GraphCommandResult]] = {
    PATHCHECK_COMMAND.name: handle_pathcheck,
    PATHCHECK_STANCE_COMMAND.name: handle_pathcheck_stance,
    HITL_COMMAND.name: handle_hitl,
}


__all__ = [
    "HITL_COMMAND",
    "PATHCHECK_COMMAND",
    "PATHCHECK_STANCE_COMMAND",
    "TOGGLE_COMMANDS",
    "TOGGLE_HANDLERS",
    "handle_hitl",
    "handle_pathcheck",
    "handle_pathcheck_stance",
]
