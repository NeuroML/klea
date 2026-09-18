#!/usr/bin/env python3
"""
Shared MCP tools caller node.

File: klea_utils/nodes/tools_caller.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Callable
from typing import Any

from fastmcp.client.client import CallToolResult
from pydantic import BaseModel

from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL
from klea_utils.mcp.dispatch import dispatch_tool_calls
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.nodes.abstract import AbstractLangGraphNode, NodeStreamData


class ToolsCallerNode(AbstractLangGraphNode[BaseModel, dict[str, Any]]):
    """Node that gates and dispatches the selected MCP tool calls.

    Shared by Klea Agent and Klea RAG.  Reads ``state.tool_calls`` (a list of
    ``ToolCallSchema``), gates each call client-side through
    :func:`klea_utils.mcp.dispatch.dispatch_tool_calls` (permission layer),
    emits info/debug stream events, and writes ``state.tool_results``.

    Applications that need extra post-dispatch state updates (e.g. the
    agent's per-plan-step status) pass a *post_dispatch* callback that
    receives the state and the results and returns additional state updates.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        mcp_client: Any | None,
        tool_infos: dict[str, ToolInfo] | None = None,
        project_root: str | None = None,
        post_dispatch: Callable[[Any, list[CallToolResult]], dict[str, Any]]
        | None = None,
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
        :param post_dispatch: Optional ``(state, results) -> state_updates``
            callback for application-specific updates after dispatch.  The
            state is passed untyped so app-specific state schemas fit.
        """
        super().__init__(logger=logger, label=label)
        self._mcp_client = mcp_client
        self._tool_infos = tool_infos
        self._project_root = project_root
        self._post_dispatch = post_dispatch
        #: Last state/results, set by ``execute`` for the streaming hooks.
        self._last_state: BaseModel | None = None
        self._last_tool_results: list[CallToolResult] | None = None

    async def execute(self, state: BaseModel) -> dict[str, Any]:
        """Gate and dispatch the tool calls in ``state.tool_calls``.

        Always writes ``tool_results`` -- an empty list when there is nothing
        to dispatch -- so a previous batch's results are never left in state
        and re-evaluated.

        :param state: Current graph state (must carry ``tool_calls``).
        :returns: ``{"tool_results": [...]}`` plus any callback extras.
        """
        self._pre_exec_stream()

        if self._pre_exec(state):
            tool_calls = getattr(state, "tool_calls", [])
            access_level = getattr(state, "access_level", DEFAULT_ACCESS_LEVEL)
            self.logger.debug(
                f"{tool_calls = }\n{access_level = }\n"
                f"tool_infos_configured = {self._tool_infos is not None}"
            )
            results = await dispatch_tool_calls(
                self._mcp_client,
                [(tc.tool, tc.args) for tc in tool_calls],
                self._tool_infos,
                self._project_root,
                access_level=access_level,
            )
        else:
            self.logger.debug("No tool calls to dispatch; writing empty results")
            results = []
        self.logger.debug(f"{results =}")

        self._last_state = state
        self._last_tool_results = results
        self._post_exec_stream()

        updates: dict[str, Any] = {"tool_results": results}
        if self._post_dispatch:
            updates.update(self._post_dispatch(state, results))
        return updates

    def _pre_exec(self, state: BaseModel) -> bool:
        """Run only when there are tool calls and a client to dispatch to."""
        return bool(getattr(state, "tool_calls", None)) and self._mcp_client is not None

    def _get_inspect(self) -> NodeStreamData:
        """Return the inspection payload for the completed dispatch.

        The inspection pane shows the dispatch summary plus the full tool
        calls (arguments and reasons) and their results.  The status pane gets
        a compact ``ok``/``error`` view from ``_get_status``; renderable
        outputs (e.g. diffs) surface in the chat.
        """
        assert self._last_state is not None
        assert self._last_tool_results is not None
        tool_calls = getattr(self._last_state, "tool_calls", [])
        tool_names = [tc.tool for tc in tool_calls]
        success_count = sum(1 for r in self._last_tool_results if not r.is_error)
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
                    for i, r in enumerate(self._last_tool_results)
                ],
            },
        )

    def _get_status(self) -> NodeStreamData | None:
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
        assert self._last_state is not None
        assert self._last_tool_results is not None
        tool_calls = getattr(self._last_state, "tool_calls", [])
        if not tool_calls:
            return None
        lines: list[str] = []
        for tc, result in zip(tool_calls, self._last_tool_results, strict=False):
            info = self._tool_infos.get(tc.tool) if self._tool_infos else None
            title = info.title if info and info.title else tc.tool
            label = "error" if result.is_error else "ok"
            lines.append(f"- **{title}**: {label}")
        return NodeStreamData(
            heading="Tool Execution",
            summary=f"Called {len(tool_calls)} tool(s)",
            display="\n".join(lines),
        )
