#!/usr/bin/env python3
"""
Shared MCP tools picker node.

See ``devdocs/system/mcp-permissions.md`` and ``devdocs/system/streams.md``;
ADRs 0020, 0034 and 0044.

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
from klea_utils.mcp.call_schema import NO_TOOL_TAG, build_tool_call_schema
from klea_utils.mcp.schemas import ToolCallSchema, ToolCallsSchema, ToolInfo
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.nodes.context import LLMNodeContext, NodeContext
from klea_utils.tools import last_tool_error_text

#: Maximum plan steps bound in one picker invocation (ADR-0041 batch cap).
MAX_BATCH_STEPS = 8
#: Maximum steps bound in one binder invocation when steps share a tool set.
MAX_BIND_GROUP = 4
#: Maximum tool calls bound in one picker invocation (ADR-0041 batch cap).
MAX_BATCH_CALLS = 16


class ToolsPicker(BaseLLMNode[BaseModel, ToolCallsSchema]):
    """Node that selects MCP tools for the current step or query.

    Shared by Klea Agent and Klea RAG.  The two applications differ only in
    the prompt files, the model role, and which context fields exist in the
    state, so all of that is configuration:

    - *prompt_registry_location* points at the application's ``prompts/``
      directory.  RAG uses ``ToolsPicker_*``; the agent's ``ToolCallBinder``
      uses ``ToolCallBinder_*``.
    - *model_role* selects the ``llm_models`` role (``"tool_picker"`` for the
      agent binder, ``"chat"`` for RAG).
    - *tools_info* is the per-domain ``BaseLangGraph.tools_info``; when the
      state carries ``query_domains`` the descriptions are filtered to those
      domains (RAG), otherwise all tools are offered (agent).

    ``_get_prompt_variables`` returns a superset of variables; each prompt
    file uses only the ones it declares (``ChatPromptTemplate`` ignores the
    rest), so one class serves both prompts.

    With ``bind_planned_steps=True`` (the agent's ``ToolCallBinder``) the node
    binds the Planner's chosen tools instead of choosing tools: it processes
    one contiguous group of plan steps that share the same suggested-tool set,
    exposes only those tools, and accumulates calls in ``binding_calls`` until
    the batch is fully bound.  RAG keeps the default picker behavior.
    """

    model_role = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.01,
    }
    #: Binder mode retries an off-contract selection before failing closed.
    #: Picker mode does not validate selections, so this is inert for RAG.
    max_validation_retries: ClassVar[int] = 2

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        tools_info: dict[str, dict[str, ToolInfo]] | None = None,
        model_role: str = "chat",
        prompt_prefix: str = "ToolsPicker",
        prompt_registry_location: str | Path | None = None,
        on_unusable: Callable[[Any, str], dict[str, Any]] | None = None,
        bind_planned_steps: bool = False,
    ):
        """Initialise the tools picker node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param tools_info: Per-domain tool metadata (``BaseLangGraph.tools_info``).
        :param model_role: Model role key into ``llm_models`` (``"plan"`` for
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
        :param bind_planned_steps: Bind the plan's suggested tools instead of
            choosing tools.  When true, each invocation covers one contiguous
            group of batch steps sharing the same ``suggested_tools`` set, and
            calls accumulate in ``state.binding_calls`` until the batch is
            bound.  The agent's ``ToolCallBinder`` sets this; RAG leaves it
            false.
        """
        # Must be set before AbstractLLMNode.__init__ reads it to pick the
        # right entry from llm_models.
        self.model_role = model_role
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
        self._bind_planned_steps = bind_planned_steps
        if prompt_registry_location is not None:
            self.prompt_registry_location = Path(prompt_registry_location)

    def _binding_enabled(self) -> bool:
        """Return whether this instance binds planned steps.

        Read via ``getattr`` so lightweight test doubles built with
        ``object.__new__`` still behave as normal pickers.

        :returns: True when binder mode is enabled.
        """
        return bool(getattr(self, "_bind_planned_steps", False))

    def _disclosed_tools(self, state: BaseModel) -> dict[str, ToolInfo]:
        """Return the tools disclosed to the model for *state*.

        Access-level filtered (ADR-0037); a RAG state additionally filters to
        its ``query_domains``.  Shared by the description text and the dynamic
        call schema so both always cover the same tool set.

        :param state: Current graph state.
        :returns: ``{tool_name: ToolInfo}`` for the disclosed tools.
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
            return {
                name: info
                for d in domains
                if d in tools_info
                for name, info in tools_info[d].items()
            }
        return {
            name: info
            for domain_tools in tools_info.values()
            for name, info in domain_tools.items()
        }

    def _batch_steps(self, state: BaseModel) -> list[Any]:
        """Return the current plan batch, or ``[]`` for a planless state.

        :param state: Current graph state.
        :returns: The ordered batch steps.
        """
        plan = getattr(state, "plan", None)
        if plan is None or not hasattr(plan, "next_batch"):
            return []
        return list(plan.next_batch(MAX_BATCH_STEPS))

    def _bound_step_numbers(self, state: BaseModel) -> set[int]:
        """Return the step numbers already bound in this batch.

        A call with an empty tool name is a failure signal, not a binding.

        :param state: Current graph state.
        :returns: Bound 1-based step numbers.
        """
        bound: set[int] = set()
        for call in getattr(state, "binding_calls", None) or []:
            if not str(getattr(call, "tool", "") or "").strip():
                continue
            try:
                number = int(getattr(call, "step", 0) or 0)
            except (TypeError, ValueError):
                continue
            if number > 0:
                bound.add(number)
        return bound

    def _binding_group(self, state: BaseModel) -> list[Any]:
        """Return the next contiguous group of steps sharing a tool set.

        Binding mode processes the batch one tool-set group at a time: the
        first unbound step, followed by contiguous unbound steps whose
        ``suggested_tools`` are an identical set, capped at
        :data:`MAX_BIND_GROUP`.  Non-contiguous steps are a separate group even
        when their tool sets match, so binding order stays predictable.

        :param state: Current graph state.
        :returns: The steps to bind in this invocation, or ``[]`` when binding
            is disabled, there is no batch, or every step is already bound.
        """
        if not self._binding_enabled():
            return []
        batch = self._batch_steps(state)
        if not batch:
            return []
        bound = self._bound_step_numbers(state)
        start = next(
            (
                index
                for index, step in enumerate(batch)
                if int(getattr(step, "step_number", 0) or 0) not in bound
            ),
            None,
        )
        if start is None:
            return []
        first = batch[start]
        group_key = frozenset(getattr(first, "suggested_tools", None) or [])
        group: list[Any] = []
        for step in batch[start:]:
            number = int(getattr(step, "step_number", 0) or 0)
            if number in bound:
                break
            if frozenset(getattr(step, "suggested_tools", None) or []) != group_key:
                break
            group.append(step)
            if len(group) >= MAX_BIND_GROUP:
                break
        return group

    def _binding_allowed_tools(self, state: BaseModel) -> dict[str, ToolInfo]:
        """Return the tools the current invocation may use.

        In picker mode this is the access/domain-filtered catalogue.  In
        binder mode it is narrowed to the current binding group's shared
        suggested-tool set; an empty group yields no tools, so the binder must
        escalate rather than choose from the wider catalogue.

        :param state: Current graph state.
        :returns: ``{tool_name: ToolInfo}`` available to this invocation.
        """
        disclosed = self._disclosed_tools(state)
        if not self._binding_enabled():
            return disclosed
        if not self._batch_steps(state):
            return disclosed
        group = self._binding_group(state)
        if not group:
            return {}
        allowed: set[str] = set()
        for step in group:
            allowed.update(getattr(step, "suggested_tools", None) or [])
        return {name: info for name, info in disclosed.items() if name in allowed}

    def _get_tool_descriptions(self, state: BaseModel) -> str:
        """Return the combined tool descriptions relevant to *state*.

        :param state: Current graph state.
        :returns: Descriptions joined into one block, or ``""`` when none.
        """
        return "\n\n".join(
            info.description or ""
            for info in self._binding_allowed_tools(state).values()
        )

    @override
    def _get_output_schema(self, state: BaseModel, ctx: Any) -> Any:
        """Build the picker/binder per-tool call schema for this run.

        A discriminated union over the same tool set as the prompt
        descriptions, so every tool's parameters are real, typed fields (not a
        free-form ``args`` object that strict structured-output modes close).
        """
        tools = self._binding_allowed_tools(state)
        if not tools:
            if self._binding_enabled():
                # A group with no available suggested tools may only escalate.
                return build_tool_call_schema({})
            return super()._get_output_schema(state, ctx)
        self.logger.debug(f"picker output schema\n{len(tools) = }\n{list(tools) = }")
        return build_tool_call_schema(tools)

    @override
    def _pre_exec(self, state: BaseModel, ctx: NodeContext) -> bool:
        """Skip only when a picker has nothing to offer.

        A binder must still run for a planned group with no available tool so
        it can return ``no_tool``; skipping would route back to the picker
        without advancing the retry counter.
        """
        if self._binding_enabled() and self._batch_steps(state):
            return True
        return bool(self._get_tool_descriptions(state))

    @override
    def _get_prompt_variables(self, state: BaseModel, ctx: Any) -> dict:
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
            batch = self._batch_steps(state)
            steps = self._binding_group(state) if self._binding_enabled() else batch
            self.logger.debug(
                f"picker batch\n{len(steps) = }\n"
                f"{[step.step_number for step in steps] = }"
            )
            variables["current_step"] = (
                "\n".join(step.render(current=True) for step in steps)
                if steps
                else "(no plan)"
            )
        # A conditional user-prompt section, composed whole and omitted
        # entirely on a normal pick (prompt conventions).  The volatile
        # content belongs in the user prompt, not the cacheable system prefix.
        # Derived from the incoming state (thread-isolated), not instance
        # fields (ADR-0033).
        attempts = int(getattr(state, "picker_attempts", 0) or 0)
        last_error = last_tool_error_text(getattr(state, "tool_results", None))
        validation = str(getattr(ctx, "validation_feedback", "") or "").strip()
        allowed_names = (
            sorted(self._binding_allowed_tools(state))
            if self._binding_enabled()
            else []
        )
        if validation:
            feedback = (
                "Your previous selection was rejected:\n"
                f"{validation}\n"
                "Bind only the suggested tools for the current step(s), or "
                "return a single entry with an empty `tool` and the reason in "
                "`reason`."
            )
        elif last_error and self._binding_enabled():
            suggested = ", ".join(allowed_names) or "(no suggested tool available)"
            feedback = (
                "Your previous tool call failed. The error was:\n"
                f"{last_error}\n"
                "Fix the arguments of the suggested tool(s) and do not choose "
                "a different tool. If none can carry out the step, return a "
                "single entry with an empty `tool` and the reason in `reason`.\n"
                f"The current step suggests: {suggested}."
            )
        elif last_error:
            feedback = (
                "Your previous tool call failed. The error was:\n"
                f"{last_error}\n"
                "Fix the arguments of the same tool so the call can succeed. "
                "Do not switch to a different tool and do not add new calls. "
                "If the error shows the step cannot be completed with this "
                "tool, return a single entry with an empty `tool` and the "
                "reason in `reason`."
            )
        elif attempts > 0 and self._binding_enabled():
            suggested = ", ".join(allowed_names) or "(no suggested tool available)"
            feedback = (
                "Your previous selection contained no usable tool call. Bind "
                "the suggested tool(s) for the current step(s), or return a "
                "single entry with an empty `tool` and the reason in `reason`. "
                f"The current step suggests: {suggested}."
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

    def _normalize_calls(self, result: Any) -> list[ToolCallSchema]:
        """Normalize the picker result to the dispatch ``ToolCallSchema`` list.

        The output schema is dynamic - a discriminated union of per-tool call
        models (plus a ``NoTool`` branch), or the legacy ``ToolCallsSchema``
        when no tools are disclosed.  Normalize either form to the stable
        interchange type consumed by the caller/triage layers.

        :param result: The processed LLM result.
        :returns: The calls as :class:`ToolCallSchema` instances.
        """
        normalized: list[ToolCallSchema] = []
        for item in getattr(result, "tool_calls", None) or []:
            if isinstance(item, ToolCallSchema):
                normalized.append(item)
                continue
            call = getattr(item, "call", None)
            if call is None:
                continue
            step = int(getattr(item, "step", 0) or 0)
            reason = getattr(item, "reason", "") or ""
            # Paths the picker declared for the call (the ``expected``
            # permission tier); absent on the legacy static schema.
            paths = [str(path) for path in (getattr(item, "paths", None) or [])]
            tool_name = str(getattr(call, "tool", "") or "")
            if tool_name == NO_TOOL_TAG:
                normalized.append(
                    ToolCallSchema(
                        tool="",
                        args={},
                        reason=getattr(call, "reason", "") or reason,
                        paths=paths,
                        step=step,
                    )
                )
                continue
            args = (
                call.model_dump(by_alias=True, exclude={"tool"})
                if hasattr(call, "model_dump")
                else {}
            )
            normalized.append(
                ToolCallSchema(
                    tool=tool_name,
                    args=args,
                    reason=reason,
                    paths=paths,
                    step=step,
                )
            )
        return normalized

    @override
    def _validate_result(self, result: Any, state: BaseModel, ctx: Any) -> str | None:
        """Reject a binder selection outside the current group's tool sets.

        The dynamic schema already excludes tools outside the group, but a
        structured-output fallback (plain invoke) can still emit one, and the
        group may contain steps with different suggested sets.  A rejected
        selection is retried with the errors as feedback, then fails closed.

        :param result: The processed picker result.
        :param state: Current graph state.
        :param ctx: Per-run node context.
        :returns: An error description, or ``None`` when valid.
        """
        if not self._binding_enabled():
            return None
        group = self._binding_group(state)
        if not group:
            return None
        group_numbers = {int(getattr(step, "step_number", 0) or 0) for step in group}
        default_step = int(getattr(group[0], "step_number", 0) or 0)
        errors: list[str] = []
        for call in self._normalize_calls(result):
            tool = call.tool.strip()
            if not tool:
                continue
            number = call.step if call.step in group_numbers else default_step
            step = next(
                (
                    item
                    for item in group
                    if int(getattr(item, "step_number", 0) or 0) == number
                ),
                group[0],
            )
            allowed = set(getattr(step, "suggested_tools", None) or [])
            if tool in allowed:
                continue
            suggested = ", ".join(sorted(allowed)) or "(none)"
            errors.append(
                f"step {number} suggests {suggested}; you selected '{tool}'. "
                "Bind a suggested tool or return no_tool with a reason."
            )
        return "; ".join(errors) if errors else None

    @override
    def _update_state(
        self, result: ToolCallsSchema, state: BaseModel, ctx: Any
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
        validation_feedback = str(getattr(ctx, "validation_feedback", "") or "").strip()
        if self._binding_enabled() and validation_feedback:
            reason = (
                f"The binder could not bind the suggested tools: {validation_feedback}"
            )
            self.logger.warning(f"{reason = }")
            failure_update: dict[str, Any] = {
                "tool_calls": [ToolCallSchema(tool="", args={}, reason=reason)]
            }
            if self._on_unusable is not None:
                failure_update.update(self._on_unusable(state, reason))
            return failure_update

        tool_calls = self._normalize_calls(result)
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
        if self._binding_enabled():
            target_steps = self._binding_group(state)
        else:
            target_steps = (
                plan.next_batch(MAX_BATCH_STEPS)
                if plan is not None and hasattr(plan, "next_batch")
                else []
            )
        target_numbers = [
            int(getattr(step_item, "step_number", 0) or 0) for step_item in target_steps
        ]
        step = target_numbers[0] if target_numbers else -1

        # Attribute each usable call to its originating batch/group step
        # (ADR-0041).  The model tags calls with `step`; a call with an
        # absent/invalid step falls back to the first step.  RAG has no plan,
        # so its calls keep ``step = 0``.
        if target_numbers:
            for call in tool_calls:
                if not call.tool.strip():
                    continue
                if call.step not in target_numbers:
                    self.logger.warning(
                        f"picker call step is not in the current batch; "
                        f"attributing it to the first batch step\n"
                        f"{call.step = }\n{target_numbers = }"
                    )
                    call.step = target_numbers[0]
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

        self.logger.debug(
            f"picker batch attribution\n{target_numbers = }\n"
            f"{[call.step for call in tool_calls if call.tool.strip()] = }\n"
            f"{len(tool_calls) = }"
        )

        prior_binding = (
            list(getattr(state, "binding_calls", None) or [])
            if self._binding_enabled()
            else []
        )
        update: dict[str, Any]
        if self._binding_enabled():
            usable_calls = [c for c in tool_calls if c.tool.strip()]
            remaining = max(0, MAX_BATCH_CALLS - len(prior_binding))
            if len(usable_calls) > remaining:
                self.logger.warning(
                    f"binder emitted more calls than the remaining batch cap; "
                    f"truncating\n{len(usable_calls) = }\n{remaining = }"
                )
                keep = {id(c) for c in usable_calls[:remaining]}
                tool_calls = [
                    c for c in tool_calls if not c.tool.strip() or id(c) in keep
                ]
            usable = any(c.tool.strip() for c in tool_calls)
            if usable:
                accumulated = [
                    *prior_binding,
                    *[c for c in tool_calls if c.tool.strip()],
                ]
                update = {
                    "tool_calls": accumulated,
                    "binding_calls": accumulated,
                }
            else:
                # Keep the failed/empty current selection visible to the
                # router, while preserving earlier groups for the retry.
                update = {
                    "tool_calls": tool_calls,
                    "binding_calls": prior_binding,
                }
        else:
            update = {"tool_calls": tool_calls}

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
    def _get_default_error_result(self, ctx: Any) -> ToolCallsSchema:
        """Return an empty result matching the current (possibly dynamic) schema.

        The static ``ToolCallsSchema`` is the fallback when no per-run schema
        was resolved; otherwise instantiate the dynamic one so its type and
        the downstream normalization stay consistent.
        """
        schema = ctx.output_schema
        if schema is not None:
            try:
                return schema(tool_calls=[])
            except (TypeError, ValueError) as exc:
                self.logger.warning(f"Fallback schema instantiation failed: {exc}")
        return ToolCallsSchema()

    @override
    def _get_inspect(
        self, state: BaseModel, ctx: LLMNodeContext[Any]
    ) -> NodeStreamData:
        """Return the inspection payload: selection, prompt and raw output."""
        assert ctx.prompt is not None
        assert ctx.output is not None
        assert ctx.result is not None
        assert ctx.state_updates is not None
        tool_calls = ctx.state_updates.get("tool_calls", [])
        tool_names = [tc.tool for tc in tool_calls]
        if tool_names:
            summary = f"Selected {len(tool_names)} tool(s): {', '.join(tool_names)}"
        else:
            summary = "No tools selected"
        details: dict[str, Any] = {
            "tool_names": tool_names,
            "tool_count": len(tool_names),
            "input_prompt": prompt_value_to_messages(ctx.prompt),
            "unprocessed_output": extract_llm_output_content(ctx.output),
            "processed_output": str(ctx.result),
        }
        return NodeStreamData(
            heading="Tool Selection", summary=summary, details=details
        )
