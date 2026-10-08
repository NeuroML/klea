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
from collections.abc import Callable, Mapping, Sequence
from typing import Any, cast

from fastmcp.client.client import CallToolResult
from mcp.types import TextContent

from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL, AccessLevel, check_tool_access
from klea_utils.mcp.path_detect import PathRequest, detect_path_requests
from klea_utils.mcp.permission_hitl import PermissionResolution
from klea_utils.mcp.schemas import ToolCallSchema, ToolInfo

logger = logging.getLogger(__name__)

#: Resolves detected outside-path requests to allow/deny decisions.  The app
#: supplies one that pauses the run for user approval (``interrupt``, ADR-0046);
#: with no resolver (the default) every request is denied.
PermissionResolver = Callable[[list[PathRequest]], PermissionResolution]

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


def _as_tool_call(call: Any) -> ToolCallSchema:
    """Normalize a dispatch input to a :class:`ToolCallSchema`."""
    if isinstance(call, ToolCallSchema):
        return call
    name, args = call
    return ToolCallSchema(tool=str(name), args=dict(args or {}))


def _dedupe_requests(requests: Sequence[PathRequest]) -> list[PathRequest]:
    """Deduplicate requests by approval key, keeping the first (strongest)."""
    by_key: dict[str, PathRequest] = {}
    for request in requests:
        by_key.setdefault(request.approval_key, request)
    return sorted(by_key.values(), key=lambda request: request.approval_key)


def _request_allowed(
    request: PathRequest, approved_dirs: set[str], approved_files: set[str]
) -> bool:
    """Return whether an approval for *request* was granted for this dispatch."""
    if request.kind == "sensitive":
        return request.path in approved_files
    return request.directory in approved_dirs


def _permission_denied_result(
    tool: str, requests: Sequence[PathRequest], denied: set[str]
) -> CallToolResult:
    """Build the non-halting result for a call blocked by the permission gate.

    A path the user explicitly denied gets a "denied by the user" message; an
    unapproved outside path or sensitive file gets the standard message.
    """
    user_denied = [request for request in requests if request.approval_key in denied]
    if user_denied:
        paths = ", ".join(sorted({request.approval_key for request in user_denied}))
        text = (
            f"Access to {paths} was denied by the user. Do not retry it; choose "
            "another approach or ask the user."
        )
    else:
        paths = ", ".join(sorted({str(request.path) for request in requests}))
        text = (
            "Access is denied by the client-side permission gate (outside the "
            f"project directory or a sensitive file): {paths}"
        )
    logger.warning(f"Permission denied\n{tool = }\n{paths = }\n{user_denied = }")
    return CallToolResult(
        content=[TextContent(type="text", text=text)],
        structured_content=None,
        meta=None,
        is_error=True,
    )


async def dispatch_tool_calls(
    mcp_client: Any,
    tool_calls: Sequence[ToolCallSchema | tuple[str, Mapping[str, Any]]],
    tool_infos: dict[str, ToolInfo] | None = None,
    project_root: str | None = None,
    access_level: AccessLevel = DEFAULT_ACCESS_LEVEL,
    call_timeout: float | None = None,
    *,
    allowed_dirs: Sequence[str] | None = None,
    allowed_files: Sequence[str] | None = None,
    permission_resolver: PermissionResolver | None = None,
) -> list[CallToolResult]:
    """Gate and dispatch tool calls against an MCP server.

    For each call a gate runs before it reaches the server, each producing a
    synthetic non-halting error when rejected:

    * an invalid tool name (empty, or -- when *tool_infos* is given -- not in
      the disclosed catalogue) is rejected without contacting the server, so
      a hallucinated name cannot be dispatched; and
    * the permission gate
      (:func:`klea_utils.mcp.path_detect.detect_path_requests`), which finds
      the outside-project paths a call would touch (declared ``checkpaths``,
      picker-declared paths, and heuristics); *permission_resolver* turns
      those into allow/deny decisions (the app pauses for user approval) and
      any request left unapproved blocks the call; and
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
    :param tool_calls: The calls to invoke, as :class:`ToolCallSchema` (so the
        picker's declared ``paths`` travel) or legacy ``(name, args)`` pairs.
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
    :param allowed_dirs: Directories already approved for the session
        (``BaseGraphSchema.allowed_dirs``), checked alongside *project_root*.
    :param allowed_files: Sensitive files already approved for the session
        (``BaseGraphSchema.allowed_files``).
    :param permission_resolver: Called with the detected requests before
        dispatch; returns a :class:`PermissionResolution`.  The app supplies
        one that pauses for user approval; when it is ``None`` the outside
        paths are denied (the pre-ADR-0007 behaviour) and the sensitive second
        round is not run.
    :returns: One :class:`CallToolResult` per input call, in input order.
    """
    calls = [_as_tool_call(call) for call in tool_calls]
    n = len(calls)
    results: list[CallToolResult | None] = [None] * n
    timeout = call_timeout if call_timeout is not None else tool_call_timeout_seconds()
    if timeout is not None and timeout <= 0:
        timeout = None
    base_allowed = [str(entry) for entry in (allowed_dirs or [])]
    base_allowed_files = [str(entry) for entry in (allowed_files or [])]
    logger.debug(
        f"{len(calls) = }\n"
        f"{[call.tool for call in calls] = }\n"
        f"{access_level = }\n"
        f"tool_gate_enabled = {tool_infos is not None}\n"
        f"{timeout = }\n"
        f"{base_allowed = }\n"
        f"{base_allowed_files = }"
    )

    # 1. Static gates (name, access level): no server needed.  Calls that pass
    # are candidates for the permission gate and dispatch.
    gated: list[int] = []
    for i, call in enumerate(calls):
        # Reject empty/unknown names before any gate or server call.  A
        # known-name check is only possible when the catalogue is given; with
        # ``tool_infos=None`` only the empty-name case is caught.
        stripped = call.tool.strip()
        if not stripped or (tool_infos is not None and stripped not in tool_infos):
            logger.warning(
                f"Rejecting invalid tool name before dispatch\n{call.tool = }"
            )
            results[i] = _unknown_tool_error(call.tool)
            continue
        if tool_infos is not None:
            info = tool_infos.get(call.tool)
            denial = check_tool_access(
                call.tool,
                info.read_only if info else None,
                info.destructive if info else None,
                access_level,
            )
            if denial:
                logger.warning(f"Denied tool call before dispatch\n{call.tool = }")
                results[i] = _denied_result([denial])
                continue
        gated.append(i)

    # 2. Permission gate: detect the paths each call would touch -- outside the
    # roots, and (when a resolver is given) sensitive files inside them --
    # resolve them (an interrupt for user approval), then enforce per call.
    # Detection is layered (declared checkpaths + picker-declared paths +
    # heuristics); see ``klea_utils.mcp.path_detect``.
    call_requests: dict[int, list[PathRequest]] = {}
    if tool_infos is not None:
        for i in gated:
            call = calls[i]
            call_requests[i] = detect_path_requests(
                call.tool,
                call.args,
                tool_infos.get(call.tool),
                project_root=project_root,
                allowed_dirs=base_allowed,
                allowed_files=base_allowed_files,
                picker_paths=call.paths,
                include_sensitive=permission_resolver is not None,
            )
    all_requests = _dedupe_requests(
        [request for requests in call_requests.values() for request in requests]
    )
    resolution: PermissionResolution | None = None
    if all_requests and permission_resolver is not None:
        resolution = permission_resolver(all_requests)
    approved_dirs = set(base_allowed)
    approved_files = set(base_allowed_files)
    denied: set[str] = set()
    if resolution is not None:
        kind_by_key = {request.approval_key: request.kind for request in all_requests}
        for key in resolution.effective_keys():
            if kind_by_key.get(key) == "sensitive":
                approved_files.add(key)
            else:
                approved_dirs.add(key)
        denied = set(resolution.denied)

    logger.debug(
        f"Permission gate\n{len(all_requests) = }\n{resolution = }\n"
        f"{approved_dirs = }\n{approved_files = }\n{denied = }"
    )

    pending: list[tuple[int, ToolCallSchema]] = []
    for i in gated:
        leftover = [
            request
            for request in call_requests.get(i, [])
            if not _request_allowed(request, approved_dirs, approved_files)
        ]
        if leftover:
            results[i] = _permission_denied_result(calls[i].tool, leftover, denied)
        else:
            pending.append((i, calls[i]))

    # 3. Dispatch the permitted calls (needs the MCP client).
    if pending:
        async with mcp_client:
            # Dispatch concurrently, but run calls that target the same
            # resource sequentially in call order (ADR-0041): two writes to one
            # file must not race, so a missed dependency edge costs parallelism,
            # never a lost update.  Calls with no identifiable resource run
            # concurrently, as before.  Wrappers are created in input order so
            # the dispatch order stays stable.
            groups: dict[frozenset[str], list[tuple[int, Any]]] = {}
            ordered: list[tuple[int, Any, frozenset[str]]] = []
            for idx, call in pending:
                coro = mcp_client.call_tool(
                    name=call.tool,
                    arguments=call.args,
                    raise_on_error=False,
                    timeout=timeout,
                )
                key = resource_key(call, tool_infos)
                ordered.append((idx, coro, key))
                if key:
                    groups.setdefault(key, []).append((idx, coro))

            async def _await_one(idx: int, coro: Any) -> tuple[int, Any]:
                try:
                    return idx, await coro
                except Exception as exc:  # noqa: BLE001 - surfaced as a result
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
                        call = calls[idx]
                        logger.warning(
                            f"Tool call failed\n{call.tool = }\n{idx = }\n"
                            f"{call.args = }\n{res = }"
                        )
                        if _is_timeout_error(res) and timeout is not None:
                            text = (
                                f"Tool '{call.tool}' timed out after {timeout:g} s "
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
        offending = [(i, calls[i].tool) for i in missing]
        logger.error(f"dispatch left unfilled slots\n{offending = }")
        raise RuntimeError(f"dispatch internal error: unfilled results at {missing}")

    failed = [i for i, r in enumerate(results) if r is not None and r.is_error]
    logger.debug(f"Dispatch complete\n{len(results) = }\n{failed = }")
    return cast(list[CallToolResult], results)
