#!/usr/bin/env python3
"""
Client-side MCP tool-call dispatch with permission gating.

File: klea_utils/mcp/dispatch.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
import math
import os
from typing import Any, cast

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL, AccessLevel, check_tool_access
from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo
from klea_utils.mcp.tool_impls.permission import check_tool_arguments_permissions

logger = logging.getLogger(__name__)

#: Default per-call wall-clock cap for tool calls.  A generous backstop that
#: only fires when a tool hangs; individual tools own their semantic timeouts
#: (e.g. ``run_command``), so raise this above any tool's own maximum.
DEFAULT_TOOL_CALL_TIMEOUT_SECONDS = 900.0

#: Process environment variable overriding the backstop (seconds).  A value
#: of ``0`` or less disables it.
TOOL_CALL_TIMEOUT_ENV_VAR = "KLEA_TOOL_CALL_TIMEOUT"


def tool_call_timeout_seconds() -> float | None:
    """Return the per-call tool timeout in seconds, or ``None`` when disabled.

    Reads :data:`TOOL_CALL_TIMEOUT_ENV_VAR`; an unset, non-numeric, or
    non-finite value falls back to :data:`DEFAULT_TOOL_CALL_TIMEOUT_SECONDS`,
    while a non-positive value disables the backstop.

    :returns: The timeout ceiling, or ``None`` when disabled.
    """
    raw = os.environ.get(TOOL_CALL_TIMEOUT_ENV_VAR)
    if raw is None:
        return DEFAULT_TOOL_CALL_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        logger.warning(
            f"Invalid {TOOL_CALL_TIMEOUT_ENV_VAR}={raw!r}; "
            f"using {DEFAULT_TOOL_CALL_TIMEOUT_SECONDS} s"
        )
        return DEFAULT_TOOL_CALL_TIMEOUT_SECONDS
    if not math.isfinite(value):
        logger.warning(
            f"Invalid (non-finite) {TOOL_CALL_TIMEOUT_ENV_VAR}={raw!r}; "
            f"using {DEFAULT_TOOL_CALL_TIMEOUT_SECONDS} s"
        )
        return DEFAULT_TOOL_CALL_TIMEOUT_SECONDS
    if value <= 0:
        logger.debug(f"{TOOL_CALL_TIMEOUT_ENV_VAR} disables the tool-call timeout")
        return None
    logger.debug(f"{TOOL_CALL_TIMEOUT_ENV_VAR} = {value}")
    return value


def _is_timeout_error(exc: BaseException) -> bool:
    """Return whether *exc* represents a tool-call timeout.

    fastmcp surfaces a read timeout as an ``McpError`` whose text mentions the
    timeout, so both ``TimeoutError`` and a text check are covered.

    :param exc: The exception raised by the tool call.
    :returns: True when the exception looks like a timeout.
    """
    if isinstance(exc, TimeoutError):
        return True
    text = str(exc).lower()
    return "timed out" in text or "timeout" in text


def _denied_result(denials: list[str]) -> CallToolResult:
    """Build a non-halting error result for a permission-denied tool call."""
    return CallToolResult(
        content=[TextContent(type="text", text="\n".join(denials))],
        structured_content=None,
        meta=None,
        is_error=True,
    )


def _unknown_tool_error(tool_name: str) -> CallToolResult:
    """Build a non-halting error for an empty or unknown tool name.

    A weak model can emit a well-formed but empty ``tool`` name, or a name
    that is not in the disclosed catalogue.  Such a call is never dispatched
    to the server; the error is returned instead so the failure is visible to
    the retry/evaluation loop (which can feed it back to the picker).

    :param tool_name: The offending tool name (possibly empty).
    :returns: An ``is_error`` result explaining the rejection.
    """
    shown = tool_name if tool_name else "(empty)"
    return _denied_result([f"Unknown tool: {shown!r} (not available to the picker)"])


def resource_key(
    call: ToolCallSchema, tool_infos: dict[str, ToolInfo] | None
) -> frozenset[str]:
    """Return the resource identifiers a tool call touches (ADR-0041).

    Best-effort and deterministic: reads the tool's declared ``checkpaths``
    (from ``ToolInfo.checkpaths`` or the folded ``ToolInfo.meta['checkpaths']``)
    and normalises the string value of each declared argument lexically with
    ``os.path.normpath``.  The caller uses this to serialise calls that share a
    resource, so a missed dependency edge cannot cause a lost update.

    An empty set means no identifiable resource: the tool is unknown, declares
    no ``checkpaths``, or the declared argument is absent/not a string.  Such a
    call is left to run concurrently.

    :param call: The bound tool call.
    :param tool_infos: Mapping of tool name to :class:`ToolInfo`, or ``None``.
    :returns: A frozenset of normalised resource identifiers (possibly empty).
    """
    if tool_infos is None:
        return frozenset()
    info = tool_infos.get(call.tool)
    if info is None:
        return frozenset()
    checkpaths = info.checkpaths
    if not checkpaths and info.meta:
        folded = info.meta.get("checkpaths")
        if isinstance(folded, list):
            checkpaths = folded
    if not checkpaths:
        return frozenset()
    args = call.args or {}
    resources: set[str] = set()
    for arg_name in checkpaths:
        value = args.get(arg_name)
        if isinstance(value, str) and value:
            resources.add(os.path.normpath(value))
    return frozenset(resources)


async def dispatch_tool_calls(
    mcp_client: Any,
    tool_calls: list[tuple[str, dict[str, Any]]],
    tool_infos: dict[str, ToolInfo] | None = None,
    project_root: str | None = None,
    access_level: AccessLevel = DEFAULT_ACCESS_LEVEL,
    call_timeout: float | None = None,
) -> list[CallToolResult]:
    """Gate and dispatch tool calls against an MCP server.

    For each ``(tool name, arguments)`` pair a gate runs before the call
    reaches the server, each producing a synthetic non-halting error when
    rejected:

    * an invalid tool name (empty, or -- when *tool_infos* is given -- not in
      the disclosed catalogue) is rejected without contacting the server, so
      a hallucinated name cannot be dispatched; and
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
    :param call_timeout: Per-call wall-clock timeout in seconds, as a backstop
        against a hung tool.  ``None`` (the default) resolves from
        :data:`TOOL_CALL_TIMEOUT_ENV_VAR` / :data:`DEFAULT_TOOL_CALL_TIMEOUT_SECONDS`;
        ``0`` or less disables the backstop.  A timed-out call becomes a
        synthetic non-halting error.
    :returns: One :class:`CallToolResult` per input call, in input order.
    """
    n = len(tool_calls)
    results: list[CallToolResult | None] = [None] * n
    pending: list[tuple[int, str, dict[str, Any], Any]] = []
    timeout = call_timeout if call_timeout is not None else tool_call_timeout_seconds()
    if timeout is not None and timeout <= 0:
        timeout = None
    logger.debug(
        f"{len(tool_calls) = }\n"
        f"{[name for name, _ in tool_calls] = }\n"
        f"{access_level = }\n"
        f"tool_gate_enabled = {tool_infos is not None}\n"
        f"{timeout = }"
    )

    async with mcp_client:
        for i, (tool_name, args) in enumerate(tool_calls):
            # Reject empty/unknown names before any gate or server call.  A
            # known-name check is only possible when the catalogue is given;
            # with ``tool_infos=None`` only the empty-name case is caught.
            stripped = tool_name.strip()
            if not stripped or (tool_infos is not None and stripped not in tool_infos):
                logger.warning(
                    f"Rejecting invalid tool name before dispatch\n{tool_name = }"
                )
                results[i] = _unknown_tool_error(tool_name)
                continue
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
                        tool_name,
                        args,
                        mcp_client.call_tool(
                            name=tool_name,
                            arguments=args,
                            raise_on_error=False,
                            timeout=timeout,
                        ),
                    )
                )

        if pending:
            # Dispatch concurrently, but run calls that target the same
            # resource sequentially in call order (ADR-0041): two writes to one
            # file must not race, so a missed dependency edge costs parallelism,
            # never a lost update.  Calls with no identifiable resource run
            # concurrently, as before.  Wrappers are created in input order so
            # the dispatch order stays stable.
            groups: dict[frozenset[str], list[tuple[int, Any]]] = {}
            ordered: list[tuple[int, Any, frozenset[str]]] = []
            for idx, name, args, coro in pending:
                key = resource_key(ToolCallSchema(tool=name, args=args), tool_infos)
                ordered.append((idx, coro, key))
                if key:
                    groups.setdefault(key, []).append((idx, coro))

            async def _await_one(idx: int, coro: Any) -> tuple[int, Any]:
                try:
                    return idx, await coro
                except BaseException as exc:  # noqa: BLE001 - surfaced as a result
                    return idx, exc

            async def _run_sequential(
                items: list[tuple[int, Any]],
            ) -> list[tuple[int, Any]]:
                return [await _await_one(idx, coro) for idx, coro in items]

            wrappers: list[Any] = []
            added_groups: set[frozenset[str]] = set()
            for idx, coro, key in ordered:
                if not key:
                    wrappers.append(_await_one(idx, coro))
                elif key not in added_groups:
                    added_groups.add(key)
                    wrappers.append(_run_sequential(groups[key]))
            logger.debug(
                f"Resource grouping\n"
                f"{ {tuple(sorted(k)): [i for i, _ in v] for k, v in groups.items()} = }\n"
                f"{[idx for idx, _, key in ordered if not key] = }\n"
                f"{len(wrappers) = }"
            )
            gathered = await asyncio.gather(*wrappers)
            for item in gathered:
                pairs = item if isinstance(item, list) else [item]
                for idx, res in pairs:
                    if isinstance(res, BaseException):
                        tool_name, args = tool_calls[idx]
                        logger.warning(
                            f"Tool call failed\n{tool_name = }\n{idx = }\n{args = }\n{res = }"
                        )
                        if _is_timeout_error(res) and timeout is not None:
                            text = (
                                f"Tool '{tool_name}' timed out after {timeout:g} s "
                                "and was cancelled; the server-side operation may "
                                "still be running."
                            )
                        else:
                            text = f"{res.__class__.__name__}: {res}"
                        results[idx] = CallToolResult(
                            content=[TextContent(type="text", text=text)],
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
