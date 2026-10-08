#!/usr/bin/env python3
"""
Shared MCP tools caller node.

See ``devdocs/system/streams.md`` (chat-renderable ``tool`` events) and
``devdocs/system/mcp-permissions.md``; ADRs 0020, 0034 and 0040.

File: klea_utils/nodes/tools_caller.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Callable
from typing import Any, Literal

from fastmcp.client.client import CallToolResult
from langgraph.types import interrupt
from mcp.types import (
    AudioContent,
    BlobResourceContents,
    EmbeddedResource,
    ImageContent,
    ResourceLink,
)
from pydantic import BaseModel

from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL
from klea_utils.mcp.dispatch import dispatch_tool_calls
from klea_utils.mcp.path_detect import PathRequest
from klea_utils.mcp.permission_hitl import (
    PermissionResolution,
    PermissionResponse,
    build_permission_payload,
    resolve_decisions,
)
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)
from klea_utils.nodes.context import ToolCallerContext


class ToolsCallerNode(
    AbstractLangGraphNode[BaseModel, dict[str, Any], ToolCallerContext]
):
    """Node that gates and dispatches the selected MCP tool calls.

    Shared by Klea Agent and Klea RAG.  Reads ``state.tool_calls`` (a list of
    ``ToolCallSchema``), gates each call client-side through
    :func:`klea_utils.mcp.dispatch.dispatch_tool_calls` (permission layer),
    emits info/debug stream events, and writes ``state.tool_results``.

    Applications that need extra post-dispatch state updates (e.g. the
    agent's per-plan-step status) pass a *post_dispatch* callback that
    receives the state, the results, and a per-result "displayed" flag, and
    returns additional state updates.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        mcp_client: Any | None,
        tool_infos: dict[str, ToolInfo] | None = None,
        project_root: str | None = None,
        post_dispatch: Callable[[Any, list[CallToolResult], list[bool]], dict[str, Any]]
        | None = None,
        permission_policy: Literal["deny", "ask"] = "deny",
    ):
        """Initialise the tools caller node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param mcp_client: MCP client instance (None skips tool calls).
            Typed as ``Any`` for the same reason as
            :func:`klea_utils.mcp.dispatch.dispatch_tool_calls`: fastmcp's
            ``call_tool`` signature does not cleanly match a structural
            protocol, so tests substitute a fake.
        :param tool_infos: Mapping of tool name to its :class:`ToolInfo`, used
            for the client-side gates (``meta`` ``checkpaths`` and the
            ADR-0037 ``read_only``/``destructive`` capability).  Built by the
            orchestrator from ``BaseLangGraph.tools_info``.  ``None`` disables
            both gates.
        :param project_root: Boundary directory for the client-side permission
            gate.  Defaults to the current working directory.
        :param post_dispatch: Optional ``(state, results, displayed) -> state_updates``
            callback for application-specific updates after dispatch.  The
            state is passed untyped so app-specific state schemas fit, and
            ``displayed`` is a per-result flag (aligned with *results*) for
            results the node streamed a display event for.
        :param permission_policy: ``deny`` (default) rejects a call that
            touches a path outside the permitted roots; ``ask`` pauses the run
            for a per-path allow-now / allow-session / deny decision via the
            shared HITL interrupt (ADR-0007).  RAG keeps the default.
        """
        super().__init__(logger=logger, label=label)
        self._mcp_client = mcp_client
        self._tool_infos = tool_infos
        self._project_root = project_root
        self._post_dispatch = post_dispatch
        self._permission_policy = permission_policy

    async def execute(self, state: BaseModel) -> dict[str, Any]:
        """Gate and dispatch the tool calls in ``state.tool_calls``.

        Skipped entirely -- no dispatch, no streaming, no state update -- when
        there is nothing to dispatch (no tool calls, or no MCP client).  An
        empty round is routed around upstream (the agent's picker router sends
        it to replan; RAG resets ``tool_results`` at graph entry), so a
        dispatched round always overwrites ``tool_results`` and no stale batch
        survives.

        :param state: Current graph state (must carry ``tool_calls``).
        :returns: ``{"tool_results": [...]}`` plus any callback extras.
        """
        # Per-run context (values local to the run, not on the shared node).
        ctx = ToolCallerContext()

        if not self._pre_exec(state, ctx):
            self.logger.debug("No tool calls to dispatch; skipping")
            return {}

        self._pre_exec_stream(ctx)
        tool_calls = getattr(state, "tool_calls", [])
        access_level = getattr(state, "access_level", DEFAULT_ACCESS_LEVEL)
        self.logger.debug(
            f"{tool_calls = }\n{access_level = }\n"
            f"tool_infos_configured = {self._tool_infos is not None}"
        )
        # Signal the round start in the status pane (coarse: every call is
        # marked ``running`` together); the end-of-round ``_get_status``
        # replaces the section with ``ok``/``error``.
        running = self._get_running_status(state)
        if running:
            self.write_custom_stream(
                NodeStreamEvent(
                    type="state", node=self.label, data=running
                ).model_dump()
            )
        allowed_dirs = [
            str(directory) for directory in (getattr(state, "allowed_dirs", []) or [])
        ]
        allowed_files = [
            str(path) for path in (getattr(state, "allowed_files", []) or [])
        ]
        resolver = (
            self._make_permission_resolver(ctx)
            if self._permission_policy == "ask"
            else None
        )
        results = await dispatch_tool_calls(
            self._mcp_client,
            tool_calls,
            self._tool_infos,
            self._project_root,
            access_level=access_level,
            allowed_dirs=allowed_dirs,
            allowed_files=allowed_files,
            permission_resolver=resolver,
        )
        self.logger.debug(f"{results =}")
        ctx.tool_results = results

        self._post_exec_stream(state, ctx)

        updates: dict[str, Any] = {"tool_results": results}
        if ctx.allowed_session_dirs:
            merged = list(dict.fromkeys([*allowed_dirs, *ctx.allowed_session_dirs]))
            self.logger.info(
                f"Session directories approved\n{ctx.allowed_session_dirs = }"
            )
            updates["allowed_dirs"] = merged
        if ctx.allowed_session_files:
            merged_files = list(
                dict.fromkeys([*allowed_files, *ctx.allowed_session_files])
            )
            self.logger.info(f"Session files approved\n{ctx.allowed_session_files = }")
            updates["allowed_files"] = merged_files
        if self._post_dispatch:
            updates.update(self._post_dispatch(state, results, ctx.display_flags))
        return updates

    def _make_permission_resolver(
        self, ctx: ToolCallerContext
    ) -> Callable[[list[PathRequest]], PermissionResolution]:
        """Return a resolver that pauses the run for per-path user approval.

        Injected into :func:`dispatch_tool_calls`: it issues the shared HITL
        ``interrupt`` (ADR-0046), records session-approved directories on
        *ctx* (persisted by :meth:`execute`), and returns the resolution so
        dispatch can enforce it.
        """

        def resolver(requests: list[PathRequest]) -> PermissionResolution:
            self.logger.info(f"Requesting path permission\n{len(requests) = }")
            response = interrupt(
                build_permission_payload(requests),
                response_schema=PermissionResponse,
            )
            resolution = resolve_decisions(requests, response)
            kind_by_key = {request.approval_key: request.kind for request in requests}
            ctx.allowed_session_dirs = [
                key
                for key in resolution.allowed_session
                if kind_by_key.get(key) != "sensitive"
            ]
            ctx.allowed_session_files = [
                key
                for key in resolution.allowed_session
                if kind_by_key.get(key) == "sensitive"
            ]
            self.logger.debug(
                f"Permission resolved\n{resolution = }\n"
                f"{ctx.allowed_session_dirs = }\n{ctx.allowed_session_files = }"
            )
            self._emit_permission_inspect(requests, resolution)
            return resolution

        return resolver

    def _emit_permission_inspect(
        self, requests: list[PathRequest], resolution: PermissionResolution
    ) -> None:
        """Emit an ``inspect`` entry summarising a resolved permission ask.

        Gives the inspection pane a record of what was asked and decided
        (the chat form is transient), so the user can audit a path decision
        after the run continues.
        """
        allowed = len(resolution.allowed_now) + len(resolution.allowed_session)
        summary = f"Path permission: {allowed} allowed, {len(resolution.denied)} denied"
        details = {
            "requests": [
                {
                    "kind": request.kind,
                    "key": request.approval_key,
                    "confidence": request.confidence,
                    "source": request.source,
                    "tool": request.tool,
                    "argument": request.argument,
                }
                for request in requests
            ],
            "resolution": resolution.model_dump(),
        }
        event = NodeStreamEvent(
            type="inspect",
            node=self.label,
            data=NodeStreamData(
                heading="Path permission", summary=summary, details=details
            ),
        )
        self.write_custom_stream(event.model_dump())

    def _pre_exec(self, state: BaseModel, ctx: ToolCallerContext) -> bool:
        """Run only when there are tool calls and a client to dispatch to."""
        return bool(getattr(state, "tool_calls", None)) and self._mcp_client is not None

    def _post_exec_stream(self, state: BaseModel, ctx: ToolCallerContext) -> None:
        """Emit the shared events, then any chat-renderable tool output."""
        super()._post_exec_stream(state, ctx)
        entries, flags = self._compute_displays(state, ctx)
        ctx.display_flags = flags
        if entries:
            self.write_custom_stream(
                {"type": "tool", "node": self.label, "data": {"tools": entries}}
            )

    def _compute_displays(
        self, state: BaseModel, ctx: ToolCallerContext
    ) -> tuple[list[dict[str, Any]], list[bool]]:
        """Return the display entries and a per-result displayed flag.

        The entries are the chat-renderable payloads (one per result at most);
        the flags are aligned with ``ctx.tool_results`` so a caller can record
        which results were streamed to the user.
        """
        tool_calls = getattr(state, "tool_calls", [])
        per_result = [
            self._display_entry_for(tc, result)
            for tc, result in zip(tool_calls, ctx.tool_results, strict=False)
        ]
        flags = [entry is not None for entry in per_result]
        return [entry for entry in per_result if entry is not None], flags

    def _tool_display_entries(
        self, tool_calls: list[Any], results: list[CallToolResult]
    ) -> list[dict[str, Any]]:
        """Return chat-renderable per-tool entries for the last round.

        Each entry is ``{tool, title, header, mime, data, meta, display}``:
        ``mime`` is the single type vocabulary (mirroring MCP's ``mimeType``),
        ``data`` the payload (text or base64), ``meta`` extras (path, language,
        additions/deletions, uri, ...) and ``display`` a preformatted text
        fallback for clients that cannot render ``mime``.

        Sources, in order: typed MCP content blocks (``ImageContent`` /
        ``AudioContent`` / ``EmbeddedResource`` / ``ResourceLink`` - the
        standard, third-party-friendly path); then structured conventions
        (``diff`` -> ``text/x-diff``, ``code`` -> ``text/x-<language>``,
        ``display`` -> ``text/markdown``); then a self-describing
        ``display`` dict (``{"mime", "data", "meta"}``); then, for a
        destructive tool with none of the above, a minimal ``text/x-shell``
        call line so a third-party destructive call is still visible.  Plain
        ``TextContent`` is not surfaced (ours is the JSON dump).  An errored
        destructive result is still surfaced; other results with nothing to
        show (and non-destructive errors) are skipped.
        """
        per_result = [
            self._display_entry_for(tc, result)
            for tc, result in zip(tool_calls, results, strict=False)
        ]
        return [entry for entry in per_result if entry is not None]

    def _display_entry_for(
        self, tc: Any, result: CallToolResult
    ) -> dict[str, Any] | None:
        """Build a display entry for one call/result, or ``None``.

        Precedence: a typed MCP content block first, then a structured
        display convention (``display``/``diff``/``code``).  Failing both, a
        destructive tool (ADR-0037) gets a minimal ``text/x-shell`` fallback
        showing the call, so a third-party destructive tool that declares no
        display convention is never silently invisible.

        Errored results are shown for destructive tools (a failed
        ``run_command`` is exactly what the user must see) but skipped for
        non-destructive ones: their failure is already in the inspect pane
        and drives Triage, so rendering every read-only error would only add
        chat noise.
        """
        info = self._tool_infos.get(tc.tool) if self._tool_infos else None
        destructive = info is not None and info.destructive
        if result.is_error and not destructive:
            return None
        title = info.title if info and info.title else tc.tool
        entry = self._display_from_content(result, tc.tool, title)
        if entry is None and isinstance(result.structured_content, dict):
            entry = self._display_from_structured(
                result.structured_content, tc.tool, title
            )
        if entry is None and destructive:
            entry = self._display_from_destructive_call(tc, title)
        if entry is not None:
            # Carried to the frontend so it can style an errored block.
            entry["is_error"] = bool(result.is_error)
        return entry

    @staticmethod
    def _display_from_destructive_call(tc: Any, title: str) -> dict[str, Any]:
        """Build a minimal shell-call entry for a destructive tool.

        Used as the last-resort display for a destructive tool that provided
        no typed content block and no display convention (third-party MCP
        servers are not required to follow the Klea convention).  Shows the
        call as ``tool(arg=...)`` with each argument value summarised, so the
        user can see what was invoked without a long argument dump.
        """
        args = getattr(tc, "args", None) or {}
        rendered = ", ".join(
            f"{key}={ToolsCallerNode._summarise_arg(value)}"
            for key, value in args.items()
        )
        call = f"{tc.tool}({rendered})" if args else f"{tc.tool}()"
        return ToolsCallerNode._display_entry(tc.tool, title, "text/x-shell", call)

    @staticmethod
    def _summarise_arg(value: Any, limit: int = 80) -> str:
        """Return a short single-line rendering of one argument value."""
        if isinstance(value, str):
            text = value.replace("\n", "\\n")
            if len(text) > limit:
                text = text[:limit] + "..."
            return f'"{text}"'
        text = repr(value).replace("\n", "\\n")
        if len(text) > limit:
            text = text[:limit] + "..."
        return text

    def _display_from_content(
        self, result: CallToolResult, tool: str, title: str
    ) -> dict[str, Any] | None:
        """Build an entry from a typed MCP content block, if any."""
        for block in result.content or []:
            if isinstance(block, (ImageContent, AudioContent)):
                return self._display_entry(
                    tool, title, block.mimeType, block.data, {"binary": True}
                )
            if isinstance(block, EmbeddedResource):
                resource = block.resource
                mime = resource.mimeType
                if not mime:
                    continue
                meta: dict[str, Any] = {"uri": str(resource.uri)}
                if isinstance(resource, BlobResourceContents):
                    meta["binary"] = True
                    return self._display_entry(tool, title, mime, resource.blob, meta)
                return self._display_entry(tool, title, mime, resource.text, meta)
            if isinstance(block, ResourceLink) and block.mimeType:
                return self._display_entry(
                    tool, title, block.mimeType, "", {"uri": str(block.uri)}
                )
        return None

    def _display_from_structured(
        self, structured: dict[str, Any], tool: str, title: str
    ) -> dict[str, Any] | None:
        """Build an entry from a structured result's display convention."""
        declared = structured.get("display")
        if isinstance(declared, dict):
            # Self-describing hook for tools: {"mime", "data", "meta"}.
            return self._display_entry(
                tool,
                title,
                declared.get("mime", "text/markdown"),
                declared.get("data", ""),
                declared.get("meta", {}),
            )
        if structured.get("diff"):
            path = structured.get("path", tool)
            adds = structured.get("additions", 0)
            dels = structured.get("deletions", 0)
            return self._display_entry(
                tool,
                title,
                "text/x-diff",
                structured["diff"],
                {"path": path, "additions": adds, "deletions": dels},
                header=f"{title}: {path} (+{adds}/-{dels})",
            )
        if structured.get("code"):
            language = structured.get("language", "")
            mime = f"text/x-{language}" if language else "text/plain"
            return self._display_entry(
                tool, title, mime, structured["code"], {"language": language}
            )
        if isinstance(declared, str) and declared.strip():
            return self._display_entry(tool, title, "text/markdown", declared)
        return None

    @staticmethod
    def _display_entry(
        tool: str,
        title: str,
        mime: str,
        data: Any,
        meta: dict[str, Any] | None = None,
        header: str | None = None,
    ) -> dict[str, Any]:
        """Assemble one display entry, including the text fallback."""
        return {
            "tool": tool,
            "title": title,
            "header": header if header is not None else title,
            "mime": mime,
            "data": data,
            "meta": meta or {},
            "display": ToolsCallerNode._fallback_display(mime, data),
        }

    @staticmethod
    def _fallback_display(mime: str, data: Any) -> str:
        """Return a client-agnostic text rendering of a display entry."""
        text = "" if data is None else str(data)
        if mime in ("text/x-diff", "text/x-patch"):
            return f"```diff\n{text}\n```"
        if mime.startswith(("image/", "audio/")):
            return f"[{mime} data, {len(text)} bytes]" if text else f"[{mime}]"
        if mime == "text/markdown":
            return text
        if mime.startswith("text/x-"):
            language = mime.split("/", 1)[1][2:]
            return f"```{language}\n{text}\n```"
        return text

    def _get_inspect(self, state: BaseModel, ctx: ToolCallerContext) -> NodeStreamData:
        """Return the inspection payload for the completed dispatch.

        The inspection pane shows the dispatch summary plus the full tool
        calls (arguments and reasons) and their results.  The status pane gets
        a compact ``ok``/``error`` view from ``_get_status``; renderable
        outputs (e.g. diffs) surface in the chat.
        """
        tool_calls = getattr(state, "tool_calls", []) or []
        tool_names = [tc.tool for tc in tool_calls]
        success_count = sum(1 for r in ctx.tool_results if not r.is_error)
        return NodeStreamData(
            heading="Tool Execution",
            summary=f"Called {len(tool_names)} tool(s), {success_count} succeeded",
            details={
                "tool_names": tool_names,
                "total_calls": len(tool_names),
                "successful_calls": success_count,
                "failed_calls": len(tool_names) - success_count,
                "tool_calls": [
                    {"tool": tc.tool, "arguments": tc.args, "reason": tc.reason}
                    for tc in tool_calls
                ],
                "tool_results": [
                    {
                        "tool": tool_names[i] if i < len(tool_names) else f"tool_{i}",
                        "is_error": r.is_error,
                        "content": str(r.content) if r.content else None,
                        "structured_content": r.structured_content,
                    }
                    for i, r in enumerate(ctx.tool_results)
                ],
            },
        )

    def _get_running_status(self, state: BaseModel) -> NodeStreamData | None:
        """Return the status-pane section for a round that is about to run.

        Emitted before dispatch so the status pane shows each tool as
        ``running``; the end-of-round :meth:`_get_status` replaces the same
        section with ``ok``/``error``.  Coarse by design: every dispatched
        call is marked running together, so a same-resource call that is
        serialised (ADR-0041) or a gated call will read ``running`` until the
        round ends.  Calls with no usable name are skipped, matching
        :meth:`_get_status`.

        :returns: A status section, or ``None`` when there is nothing to run.
        """
        tool_calls = getattr(state, "tool_calls", []) or []
        lines: list[str] = []
        for tc in tool_calls:
            if not tc.tool.strip():
                continue
            info = self._tool_infos.get(tc.tool) if self._tool_infos else None
            title = info.title if info and info.title else tc.tool
            lines.append(f"- **{title}**: running")
        if not lines:
            return None
        return NodeStreamData(
            heading="Tool Execution",
            summary=f"Running {len(tool_calls)} tool(s)",
            display="\n".join(lines),
        )

    def _get_status(
        self, state: BaseModel, ctx: ToolCallerContext
    ) -> NodeStreamData | None:
        """Return per-tool execution status for the status pane.

        The status pane shows ground truth -- which tools actually ran and
        whether each succeeded -- as a generic ``ok``/``error`` label per tool
        (frontends may map the labels to icons).  The selected args, the
        picker's reasons and the full results stay in the inspection pane
        (``_get_inspect``), and renderable outputs (e.g. diffs) surface in the
        chat.  Returns ``None`` for a round with no calls so empty rounds
        leave the pane unchanged.

        :returns: A status section, or ``None`` when nothing was called.
        """
        tool_calls = getattr(state, "tool_calls", []) or []
        # Skip calls with no usable name: rendering the empty title would show
        # a meaningless "****: error".  Their failure is still visible in the
        # inspection pane and drives Triage.
        lines: list[str] = []
        for tc, result in zip(tool_calls, ctx.tool_results, strict=False):
            if not tc.tool.strip():
                continue
            info = self._tool_infos.get(tc.tool) if self._tool_infos else None
            title = info.title if info and info.title else tc.tool
            label = "error" if result.is_error else "ok"
            lines.append(f"- **{title}**: {label}")
        if not lines:
            return None
        return NodeStreamData(
            heading="Tool Execution",
            summary=f"Called {len(tool_calls)} tool(s)",
            display="\n".join(lines),
        )
