#!/usr/bin/env python3
"""
Shared MCP tools picker node.

File: klea_utils/nodes/tools_picker.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, override

from pydantic import BaseModel

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.mcp.access import DEFAULT_ACCESS_LEVEL, filter_tools_info
from klea_utils.mcp.schemas import ToolCallSchema, ToolCallsSchema, ToolInfo
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.tools import last_tool_error_text

#: Maximum plan steps bound in one picker invocation (ADR-0041 batch cap).
MAX_BATCH_STEPS = 8
#: Maximum tool calls bound in one picker invocation (ADR-0041 batch cap).
MAX_BATCH_CALLS = 16


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
        on_unusable: Callable[[Any, str], dict[str, Any]] | None = None,
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
        :param on_unusable: Optional app callback ``(state, reason) ->
            state_updates`` invoked when the picker returns no usable call but
            explains why (a single empty-name call with a ``reason``).  The app
            records that as a synthetic failure observation and a
            ``replan_reason`` (agent); RAG passes nothing.
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
        self._on_unusable = on_unusable
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
        render_observations = getattr(state, "observations_text", None)
        if callable(render_observations):
            # Agent state: the accumulated per-step outputs (tool results and
            # reasoning conclusions), so the picker can bind arguments from
            # earlier steps - not just the last batch.
            variables["observations"] = render_observations()
        elif hasattr(state, "tool_results"):
            variables["observations"] = state.tool_results
        plan = getattr(state, "plan", None)
        if plan is not None:
            batch = (
                plan.next_batch(MAX_BATCH_STEPS) if hasattr(plan, "next_batch") else []
            )
            variables["current_step"] = (
                "\n".join(step.render(current=True) for step in batch)
                if batch
                else "(no plan)"
            )
        # Appended last (its own prompt section) so the stable prefix above
        # stays cache-friendly; omitted entirely on a normal pick (prompt
        # conventions).  Derived from the incoming state (thread-isolated), not
        # instance fields (ADR-0033).
        attempts = int(getattr(state, "picker_attempts", 0) or 0)
        last_error = last_tool_error_text(getattr(state, "tool_results", None))
        if last_error:
            feedback = (
                "Your previous tool call failed. The error was:\n"
                f"{last_error}\n"
                "Fix the arguments of the same tool so the call can succeed. "
                "Do not switch to a different tool and do not add new calls. "
                "If the error shows the step cannot be completed with this "
                "tool, return a single entry with an empty `tool` and the "
                "reason in `reason`."
            )
        elif attempts > 0:
            feedback = (
                "Your previous selection contained no usable tool call. Use "
                "only the tools named in the current step. If none can carry "
                "out the step, return a single entry with an empty `tool` and "
                "the reason in `reason`."
            )
        else:
            feedback = ""
        variables["picker_feedback"] = self._optional_section(
            "Feedback on your previous selection", feedback
        )
        return variables

    @staticmethod
    def _selection_key(call: ToolCallSchema) -> tuple[str, str]:
        """Return a canonical ``(tool, args)`` key for comparing selections."""
        return (
            call.tool,
            json.dumps(call.args or {}, sort_keys=True, default=str),
        )

    def _is_identical_failed_retry(
        self, tool_calls: list[ToolCallSchema], state: BaseModel
    ) -> bool:
        """Return whether *tool_calls* repeat the just-failed batch exactly.

        A deterministic anti-waste guard: re-emitting the same call with the
        same arguments cannot repair a call-level error, so the picker should
        escalate to the app (which replans) rather than loop.  Only active when
        an ``on_unusable`` callback is installed (the agent has a replan edge;
        RAG does not), and only when the immediately-previous batch actually
        errored -- a repeat after a successful round, or on a new step, is left
        alone.

        :param tool_calls: The picker's new selection.
        :param state: The incoming state (still holding the previous batch).
        :returns: ``True`` when the new selection repeats the failed batch.
        """
        if self._on_unusable is None or not tool_calls:
            return False
        previous_calls = getattr(state, "tool_calls", None) or []
        previous_results = getattr(state, "tool_results", None) or []
        if not previous_calls:
            return False
        if not any(getattr(r, "is_error", False) for r in previous_results):
            return False
        if len(tool_calls) != len(previous_calls):
            return False
        return sorted(self._selection_key(c) for c in tool_calls) == sorted(
            self._selection_key(c) for c in previous_calls
        )

    @override
    def _update_state(
        self, result: ToolCallsSchema, state: BaseModel
    ) -> dict[str, Any]:
        """Write the selected calls, the failure callback and the retry counter.

        A usable selection is written as-is.  An unusable selection with an
        explicit ``reason`` (a single empty-name call carrying the reason) is a
        *deliberate* failure: the app's ``on_unusable`` callback records it as a
        synthetic observation and a replan reason.  An empty/malformed
        selection with no reason is an emission glitch: the counter is kept in
        graph state (thread-isolated, ADR-0033) so the orchestrator can retry
        the picker a bounded number of times before escalating.

        A selection that *exactly repeats the previous failed batch* is
        converted to a deliberate failure (with an explanatory reason) so it is
        escalated instead of dispatched again: identical retries cannot succeed
        and only waste a round.
        """
        tool_calls = result.tool_calls
        usable = any(tc.tool.strip() for tc in tool_calls)

        if usable and self._is_identical_failed_retry(tool_calls, state):
            reason = (
                "The picker repeated the previous tool call with identical "
                "arguments after it failed; repeating the same call cannot "
                "succeed. Replan the step or correct the approach."
            )
            self.logger.warning(
                f"picker repeated the identical failed call; escalating\n{reason = }"
            )
            tool_calls = [ToolCallSchema(tool="", args={}, reason=reason)]
            usable = False

        plan = getattr(state, "plan", None)
        batch = (
            plan.next_batch(MAX_BATCH_STEPS)
            if plan is not None and hasattr(plan, "next_batch")
            else []
        )
        batch_numbers = [step_item.step_number for step_item in batch]
        step = batch_numbers[0] if batch_numbers else -1

        # Attribute each usable call to its originating batch step (ADR-0041).
        # The model tags calls with `step`; a call with an absent/invalid step
        # falls back to the first batch step.  RAG has no plan, so its calls
        # keep ``step = 0``.
        if batch_numbers:
            for call in tool_calls:
                if not call.tool.strip():
                    continue
                if call.step not in batch_numbers:
                    self.logger.warning(
                        f"picker call step is not in the current batch; "
                        f"attributing it to the first batch step\n"
                        f"{call.step = }\n{batch_numbers = }"
                    )
                    call.step = batch_numbers[0]
            usable_calls = [c for c in tool_calls if c.tool.strip()]
            if len(usable_calls) > MAX_BATCH_CALLS:
                self.logger.warning(
                    f"picker emitted more calls than the batch cap; truncating\n"
                    f"{len(usable_calls) = }\n{MAX_BATCH_CALLS = }"
                )
                keep = {id(c) for c in usable_calls[:MAX_BATCH_CALLS]}
                tool_calls = [
                    c for c in tool_calls if not c.tool.strip() or id(c) in keep
                ]

        update: dict[str, Any] = {"tool_calls": tool_calls}

        # Deliberate failure: no usable call, but the picker explained why.  The
        # app records it (synthetic is_error result + replan_reason) so the
        # graph replans with a concrete reason instead of dispatching nothing.
        if not usable and self._on_unusable is not None:
            reason = next(
                (
                    tc.reason.strip()
                    for tc in tool_calls
                    if not tc.tool.strip() and tc.reason.strip()
                ),
                "",
            )
            if reason:
                update.update(self._on_unusable(state, reason))

        # The retry counter only exists on the agent state; RAG has no picker
        # retry edge, so omit the keys there rather than writing unknown state.
        if not hasattr(state, "picker_attempts"):
            return update

        # An empty list and a list whose calls all have empty/whitespace names
        # are the same failure: no usable tool call.  Unknown-but-non-empty
        # names are left to dispatch (which reports a clear error).
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
        update["picker_attempts"] = attempts
        update["picker_step"] = step
        return update

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
