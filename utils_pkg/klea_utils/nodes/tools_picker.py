#!/usr/bin/env python3
"""
Shared MCP tools picker node.

File: klea_utils/nodes/tools_picker.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from pathlib import Path
from typing import Any, ClassVar, override

from pydantic import BaseModel

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.mcp.schemas import ToolCallsSchema, ToolInfo
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode


class ToolsPicker(BaseLLMNode[BaseModel, ToolCallsSchema]):
    """Node that selects MCP tools for the current step or query.

    Shared by Klea Agent and Klea RAG.  The two applications differ only in
    the prompt file, the model role, and which context fields exist in the
    state, so all of that is configuration:

    - *prompt_registry_location* points at the application's ``prompts/``
      directory (both apps name their picker prompt ``ToolsPicker_system.md``).
    - *model_type* selects the ``llm_models`` role (``"plan"`` for the agent,
      ``"chat"`` for RAG).
    - *tools_info* is the per-domain ``BaseLangGraph.tools_info``; when the
      state carries ``query_domains`` the descriptions are filtered to those
      domains (RAG), otherwise all tools are offered (agent).

    ``_get_prompt_variables`` returns a superset of variables; each prompt
    file uses only the ones it declares (``ChatPromptTemplate`` ignores the
    rest), so one class serves both prompts.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.01,
        "max_output_tokens": 2048,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        tools_info: dict[str, dict[str, ToolInfo]] | None = None,
        model_type: str = "chat",
        prompt_prefix: str = "ToolsPicker",
        prompt_registry_location: str | Path | None = None,
    ):
        """Initialise the tools picker node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param tools_info: Per-domain tool metadata (``BaseLangGraph.tools_info``).
        :param model_type: Model role key into ``llm_models`` (``"plan"`` for
            the agent, ``"chat"`` for RAG).
        :param prompt_prefix: Prompt file prefix (default ``ToolsPicker``).
        :param prompt_registry_location: Directory holding the prompt files.
            Must be set for apps: the sibling-``prompts`` fallback in
            ``BaseLLMNode`` resolves relative to this shared class file,
            not the application.
        """
        # Must be set before AbstractLLMNode.__init__ reads it to pick the
        # right entry from llm_models.
        self.model_type = model_type
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=ToolCallsSchema,
            memory=False,
        )
        self._tools_info = tools_info or {}
        self._prompt_prefix = prompt_prefix
        if prompt_registry_location is not None:
            self.prompt_registry_location = Path(prompt_registry_location)

    def _get_tool_descriptions(self, state: BaseModel) -> str:
        """Return the combined tool descriptions relevant to *state*.

        When the state carries ``query_domains`` (RAG), descriptions are
        filtered to those domains; otherwise all tools are included (agent).

        :param state: Current graph state.
        :returns: Descriptions joined into one block, or ``""`` when none.
        """
        domains = getattr(state, "query_domains", None)
        if domains:
            parts: list[str] = []
            for d in domains:
                if d in self._tools_info:
                    parts.extend(
                        info.description or "" for info in self._tools_info[d].values()
                    )
        else:
            parts = [
                info.description or ""
                for domain_tools in self._tools_info.values()
                for info in domain_tools.values()
            ]
        return "\n\n".join(parts)

    @override
    def _pre_exec(self, state: BaseModel) -> bool:
        """Skip when no tool description is available for this state."""
        return bool(self._get_tool_descriptions(state))

    @override
    def _get_human_prompt(self, state: BaseModel) -> str:
        """Return empty string -- this node only uses a system prompt."""
        return ""

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with state-specific variables.

        Returns a superset of variables; each prompt file (system + human)
        uses only the ones it declares, and ``ChatPromptTemplate`` ignores
        the rest, so one class serves both the plan-driven (agent) and
        query-driven (RAG) prompts.
        """
        variables: dict[str, Any] = {
            "tools_description": self._get_tool_descriptions(state),
        }
        if hasattr(state, "query"):
            variables["query"] = state.query
        if hasattr(state, "artefacts"):
            variables["artefacts"] = state.artefacts
        if hasattr(state, "tool_results"):
            variables["observations"] = state.tool_results
        plan = getattr(state, "plan", None)
        if plan is not None:
            current_step_index = plan.current_step_index
            variables["current_step"] = plan.step_list[current_step_index]
        return variables

    @override
    def _update_state(
        self, result: ToolCallsSchema, state: BaseModel
    ) -> dict[str, Any]:
        """Update state with the selected tool calls."""
        return {"tool_calls": result.tool_calls}

    @override
    def _get_default_error_result(self) -> ToolCallsSchema:
        """Return default result when processing fails."""
        return ToolCallsSchema()

    @override
    def _get_info(self) -> NodeStreamData:
        """Return the selected tools."""
        assert self._last_state_updates is not None
        tool_calls = self._last_state_updates.get("tool_calls", [])
        tool_names = [tc.tool for tc in tool_calls]
        if tool_names:
            summary = f"Selected {len(tool_names)} tool(s): {', '.join(tool_names)}"
        else:
            summary = "No tools selected"
        return NodeStreamData(
            heading="Tool Selection",
            summary=summary,
            details={
                "tool_names": tool_names,
                "tool_count": len(tool_names),
            },
        )

    @override
    def _get_debug(self) -> NodeStreamData:
        """Return info + input prompt, raw output, and full tool calls."""
        assert self._last_state is not None
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        assert self._last_state_updates is not None
        info = self._get_info()
        details = info.details.copy()
        details.update(
            {
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            }
        )
        # Add full tool calls with arguments
        tool_calls = self._last_state_updates.get("tool_calls", [])
        if tool_calls:
            details["tool_calls"] = [
                {"name": tc.tool, "arguments": tc.args, "reason": tc.reason}
                for tc in tool_calls
            ]
        return NodeStreamData(
            heading=info.heading, summary=info.summary, details=details
        )

    @override
    def _get_status(self) -> NodeStreamData:
        """Return human-readable selected tool calls."""
        assert self._last_state_updates is not None
        tool_calls = self._last_state_updates.get("tool_calls", [])

        display_parts: list[str] = []
        for tc in tool_calls:
            tool_info = next(
                (
                    info
                    for domain_tools in self._tools_info.values()
                    if (info := domain_tools.get(tc.tool)) is not None
                ),
                None,
            )
            title = tool_info.title if tool_info and tool_info.title else tc.tool
            display_parts.append(
                "**{title}**\n\n{arguments}".format(
                    title=title,
                    arguments="\n".join(
                        f"- `{key}`: "
                        f"`{value if isinstance(value, str) else json.dumps(value)}`"
                        for key, value in tc.args.items()
                    ),
                )
            )
        return NodeStreamData(
            heading="Tool Selection",
            summary=f"Tools selected: {len(tool_calls)}",
            display="\n\n".join(display_parts),
        )
