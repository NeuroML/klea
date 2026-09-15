#!/usr/bin/env python3
"""
Tool access levels for MCP tool invocation (ADR-0037).

Classification is derived from the standard MCP ``ToolAnnotations``
(``readOnlyHint`` / ``destructiveHint``) carried on ``ToolInfo``, with an
optional operator override (``ToolAccessOverride``).  Levels are
``read_only`` (only explicitly read-only, non-destructive tools) and
``full`` (every tool); an unannotated tool fails closed in ``read_only``.
See ``devdocs/adr/0037-tool-access-levels.md``.

File: klea_utils/mcp/access.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, Literal, cast

from pydantic import BaseModel

from klea_utils.mcp.schemas import ToolInfo

logger = logging.getLogger(__name__)

#: Tool invocation access levels (ADR-0037).
AccessLevel = Literal["read_only", "full"]

#: Default level: every tool is permitted (the pre-ADR-0037 behaviour).
DEFAULT_ACCESS_LEVEL: AccessLevel = "full"


class ToolAccessOverride(BaseModel):
    """Operator declaration of a tool's capability (ADR-0037).

    Configured per tool under ``general.tool_access`` and applied over the
    tool's own MCP annotations.  Either field may be left ``None`` to leave
    the annotated value unchanged; the operator who writes the config is the
    trust root for the declaration.
    """

    read_only: bool | None = None
    destructive: bool | None = None


def resolve_capability(
    read_only: bool | None,
    destructive: bool | None,
    override: ToolAccessOverride | None = None,
) -> tuple[bool | None, bool | None]:
    """Apply an operator override over a tool's annotated capability.

    Precedence is explicit ``tool_access`` override > tool annotation >
    ``None`` (which fails closed downstream).  A ``None`` override field
    leaves the annotated value unchanged.

    :param read_only: Annotated read-only hint, or ``None``.
    :param destructive: Annotated destructive hint, or ``None``.
    :param override: Operator override, or ``None``.
    :returns: The effective ``(read_only, destructive)`` pair.
    """
    if override is None:
        return read_only, destructive
    if override.read_only is not None:
        read_only = override.read_only
    if override.destructive is not None:
        destructive = override.destructive
    return read_only, destructive


def tool_permits(
    read_only: bool | None,
    destructive: bool | None,
    access_level: AccessLevel,
) -> bool:
    """Return whether *access_level* permits invoking this tool.

    ``full`` permits every tool.  ``read_only`` permits a tool only when it
    is explicitly read-only and not destructive; an unannotated tool
    (``None``) is **not** permitted (fail-closed).

    :param read_only: Tool's read-only capability.
    :param destructive: Tool's destructive capability.
    :param access_level: The active access level.
    :returns: True when the tool may be invoked.
    """
    if access_level == "full":
        return True
    return read_only is True and destructive is not True


def filter_tools_info(
    tools_info: dict[str, dict[str, ToolInfo]],
    access_level: AccessLevel,
) -> dict[str, dict[str, ToolInfo]]:
    """Return *tools_info* with tools disallowed by *access_level* removed.

    Domains are preserved as keys even when all their tools are filtered out.
    ``full`` returns the input unchanged; otherwise a new mapping is built.

    :param tools_info: Per-domain ``{name: ToolInfo}`` mapping.
    :param access_level: The active access level.
    :returns: A filtered mapping (the input is not mutated).
    """
    if access_level == "full":
        return tools_info
    filtered: dict[str, dict[str, ToolInfo]] = {}
    for domain, tools in tools_info.items():
        filtered[domain] = {
            name: info
            for name, info in tools.items()
            if tool_permits(info.read_only, info.destructive, access_level)
        }
    total = sum(len(tools) for tools in tools_info.values())
    allowed = sum(len(tools) for tools in filtered.values())
    dropped = total - allowed
    logger.debug(f"{access_level = }\n{total = }\n{allowed = }\n{dropped = }")
    return filtered


def check_tool_access(
    tool: str,
    read_only: bool | None,
    destructive: bool | None,
    access_level: AccessLevel,
) -> str | None:
    """Return a denial message when *tool* is not permitted, else ``None``.

    Mirrors :func:`tool_permits` but returns a human-readable message for the
    non-halting synthetic error result the caller produces (the ADR-0007
    error contract) instead of raising.

    :param tool: Tool name, used in the message.
    :param read_only: Tool's read-only capability.
    :param destructive: Tool's destructive capability.
    :param access_level: The active access level.
    :returns: A denial message, or ``None`` when the tool is permitted.
    """
    if tool_permits(read_only, destructive, access_level):
        return None
    reason = (
        "it is annotated destructive"
        if destructive is True
        else "it is not annotated read-only"
    )
    return (
        f"Tool '{tool}' is not available with read-only access: {reason}. "
        "Full access is required to invoke it."
    )


def resolve_access_level(value: Any) -> AccessLevel:
    """Coerce a value to a valid access level, defaulting to ``full``.

    Used for untrusted inputs (env var strings); an unrecognised value is
    logged and replaced with :data:`DEFAULT_ACCESS_LEVEL`.

    :param value: Candidate value (e.g. from a request component or env).
    :returns: The matching level, or :data:`DEFAULT_ACCESS_LEVEL`.
    """
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in ("read_only", "full"):
            return cast(AccessLevel, candidate)
    if value is not None:
        logger.warning(
            f"Unknown access level {value!r}; defaulting to {DEFAULT_ACCESS_LEVEL}"
        )
    return DEFAULT_ACCESS_LEVEL
