#!/usr/bin/env python3
"""
Client-side MCP tool-call dispatch with permission gating.

File: klea_utils/mcp/dispatch.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
from typing import Any, cast

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL, AccessLevel, check_tool_access
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.tool_impls.permission import check_tool_arguments_permissions

logger = logging.getLogger(__name__)


def _denied_result(denials: list[str]) -> CallToolResult:
    """Build a non-halting error result for a permission-denied tool call."""
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(denials))],
        structured_content=None,
        meta=None,
        is_error=True,
    )


async def dispatch_tool_calls(
    mcp_client: Any,
    tool_calls: list[tuple[str, dict[str, Any]]],
    tool_infos: dict[str, ToolInfo] | None = None,
    project_root: str | None = None,
    access_level: AccessLevel = DEFAULT_ACCESS_LEVEL,
) -> list[CallToolResult]:
    """Gate and dispatch tool calls against an MCP server.

    For each ``(tool name, arguments)`` pair two gates run before the call
    reaches the server, each producing a synthetic non-halting error when
    denied:

    * the path gate (:func:`check_tool_arguments_permissions`), reading a
      tool's ``checkpaths`` from ``ToolInfo.meta``; and
    * the tool access level (ADR-0037): a tool the level does not permit
      (``read_only`` permits only explicitly read-only, non-destructive tools)
      is rejected by :func:`check_tool_access`.

    Allowed calls are dispatched in parallel, and the returned list stays
    aligned with the input *tool_calls* order.

    :param mcp_client: MCP client used for ``call_tool``.  Its reentrant
        context is entered/exited by this helper.  Typed as ``Any`` because
        :class:`fastmcp.Client.call_tool` has a complex signature (optional
        ``arguments``, keyword-only ``raise_on_error``, ``CallToolResult |
        ToolTask`` return) that a structural protocol would not cleanly
        match; tests substitute a fake implementing the subset used here.
    :param tool_calls: ``(tool name, arguments)`` pairs to invoke.
    :param tool_infos: Mapping of tool name to its :class:`ToolInfo`, carrying
        both the annotated ``read_only``/``destructive`` capability and (in
        ``meta``) the ``checkpaths`` declarations.  ``None`` disables both
        gates (backward compatible); for ``read_only`` a tool missing from the
        map is denied (fail-closed).
    :param project_root: Boundary directory for the path gate.  Defaults to
        the current working directory.
    :param access_level: Active tool access level (ADR-0037).
    :returns: One :class:`CallToolResult` per input call, in input order.
    """
    n = len(tool_calls)
    results: list[CallToolResult | None] = [None] * n
    pending: list[tuple[int, Any]] = []
    logger.debug(
        f"{len(tool_calls) = }\n"
        f"{[name for name, _ in tool_calls] = }\n"
        f"{access_level = }\n"
        f"tool_gate_enabled = {tool_infos is not None}"
    )

    async with mcp_client:
        for i, (tool_name, args) in enumerate(tool_calls):
            tool_info = tool_infos.get(tool_name) if tool_infos is not None else None
            denials = check_tool_arguments_permissions(
                tool_info.meta if tool_info else None, args, project_root
            )
            if tool_infos is not None:
                read_only = tool_info.read_only if tool_info else None
                destructive = tool_info.destructive if tool_info else None
                denial = check_tool_access(
                    tool_name, read_only, destructive, access_level
                )
                if denial:
                    denials.append(denial)
            if denials:
                logger.warning(
                    f"Denied tool call before dispatch\n{tool_name = }\n{denials = }"
                )
                results[i] = _denied_result(denials)
            else:
                logger.debug(f"Dispatching tool call\n{tool_name = }\n{args = }")
                pending.append(
                    (
                        i,
                        mcp_client.call_tool(
                            name=tool_name,
                            arguments=args,
                            raise_on_error=False,
                        ),
                    )
                )

        if pending:
            indices, coros = zip(*pending)
            gathered = await asyncio.gather(*coros, return_exceptions=True)
            for idx, res in zip(indices, gathered):
                if isinstance(res, BaseException):
                    tool_name, args = tool_calls[idx]
                    logger.warning(
                        f"Tool call failed\n{tool_name = }\n{idx = }\n{args = }\n{res = }"
                    )
                    results[idx] = CallToolResult(
                        content=[
                            TextContent(
                                type="text",
                                text=f"{res.__class__.__name__}: {res}",
                            )
                        ],
                        structured_content=None,
                        meta=None,
                        is_error=True,
                    )
                else:
                    results[idx] = res  # type: ignore[assignment]

    if any(r is None for r in results):
        missing = [i for i, r in enumerate(results) if r is None]
        offending = [(i, tool_calls[i]) for i in missing]
        logger.error(f"dispatch left unfilled slots\n{offending = }")
        raise RuntimeError(f"dispatch internal error: unfilled results at {missing}")

    failed = [i for i, r in enumerate(results) if r is not None and r.is_error]
    logger.debug(f"Dispatch complete\n{len(results) = }\n{failed = }")
    return cast(list[CallToolResult], results)
