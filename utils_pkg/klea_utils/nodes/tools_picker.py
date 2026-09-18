#!/usr/bin/env python3
"""
Shared MCP tools picker node.

File: klea_utils/nodes/tools_picker.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path
from typing import Any, ClassVar, override

from pydantic import BaseModel

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL, filter_tools_info
from klea_utils.mcp.schemas import ToolCallsSchema, ToolInfo
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.tools import last_tool_error_text


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
        Tools the state's ``access_level`` does not permit are always excluded
        (ADR-0037), so a disallowed tool is never disclosed to the model.

        :param state: Current graph state.
        :returns: Descriptions joined into one block, or ``""`` when none.
        """
        access_level = getattr(state, "access_level", DEFAULT_ACCESS_LEVEL)
        tools_info = filter_tools_info(self._tools_info, access_level)
        self.logger.debug(
            f"{access_level = }\n"
            f"disclosed_tools = "
            f"{[name for domain in tools_info.values() for name in domain]}"
        )
        domains = getattr(state, "query_domains", None)
        if domains:
            parts: list[str] = []
            for d in domains:
                if d in tools_info:
                    parts.extend(
                        info.description or "" for info in tools_info[d].values()
                    )
        else:
            parts = [
                info.description or ""
                for domain_tools in tools_info.values()
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
            current = plan.current_step()
            variables["current_step"] = (
                current.render(current=True) if current else "(no plan)"
            )
        # Appended last (its own prompt section) so the stable prefix above
        # stays cache-friendly; empty on a normal pick.  Derived from the
        # incoming state (thread-isolated), not instance fields (ADR-0033).
        attempts = int(getattr(state, "picker_attempts", 0) or 0)
        last_error = last_tool_error_text(getattr(state, "tool_results", None))
        if last_error:
            variables["picker_feedback"] = (
                "Your previous tool call failed. The error was:\n"
                f"{last_error}\n"
                "Adjust the call so it can succeed."
                "Do not add new tool calls."
                "If the error suggests the call cannot be completed, return an empty `tool_calls` list."
            )
        elif attempts > 0:
            variables["picker_feedback"] = (
                "Your previous selection contained no usable tool call. "
                "Only pick tools from the provided list; if the step cannot be "
                "carried out with them, return an empty `tool_calls` list."
            )
        else:
            variables["picker_feedback"] = ""
        return variables

    @override
    def _update_state(
        self, result: ToolCallsSchema, state: BaseModel
    ) -> dict[str, Any]:
        """Write the selected calls and the empty-selection retry counter.

        A picker that returns no calls on a step it was asked to execute is a
        picker failure, not a planner failure.  The counter is kept in graph
        state (thread-isolated, ADR-0033), not on the shared node instance:
        it increments on each consecutive empty selection and resets when a
        non-empty selection is produced or the plan step changes.  The
        orchestrator's picker router reads it to retry the picker a bounded
        number of times before letting the empty round proceed to the
        evaluator.
        """
        tool_calls = result.tool_calls
        # The retry counter only exists on the agent state; RAG has no picker
        # retry edge, so omit the keys there rather than writing unknown state.
        if not hasattr(state, "picker_attempts"):
            return {"tool_calls": tool_calls}
        # An empty list and a list whose calls all have empty/whitespace names
        # are the same failure: no usable tool call.  Unknown-but-non-empty
        # names are left to dispatch (which reports a clear error).
        usable = any(tc.tool.strip() for tc in tool_calls)
        plan = getattr(state, "plan", None)
        step = getattr(plan, "current_step_index", -1) if plan is not None else -1
        prev_step = getattr(state, "picker_step", -1)
        prev_attempts = int(getattr(state, "picker_attempts", 0) or 0)
        if usable or step != prev_step:
            attempts = 0 if usable else 1
        else:
            attempts = prev_attempts + 1
        self.logger.debug(
            f"{step = }\n{prev_step = }\n{prev_attempts = }\n"
            f"{attempts = }\nusable = {usable}\nselected = {len(tool_calls)}"
        )
        return {
            "tool_calls": tool_calls,
            "picker_attempts": attempts,
            "picker_step": step,
        }

    @override
    def _get_default_error_result(self) -> ToolCallsSchema:
        """Return default result when processing fails."""
        return ToolCallsSchema()

    @override
    def _get_inspect(self) -> NodeStreamData:
        """Return the inspection payload: selection, prompt and raw output."""
        assert self._last_state is not None
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        assert self._last_state_updates is not None
        tool_calls = self._last_state_updates.get("tool_calls", [])
        tool_names = [tc.tool for tc in tool_calls]
        if tool_names:
            summary = f"Selected {len(tool_names)} tool(s): {', '.join(tool_names)}"
        else:
            summary = "No tools selected"
        details: dict[str, Any] = {
            "tool_names": tool_names,
            "tool_count": len(tool_names),
            "input_prompt": prompt_value_to_messages(self._last_prompt),
            "unprocessed_output": extract_llm_output_content(self._last_output),
            "processed_output": str(self._last_result),
        }
        if tool_calls:
            details["tool_calls"] = [
                {"name": tc.tool, "arguments": tc.args, "reason": tc.reason}
                for tc in tool_calls
            ]
        return NodeStreamData(
            heading="Tool Selection", summary=summary, details=details
        )
