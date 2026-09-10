#!/usr/bin/env python3
"""
Base node classes for LangGraph processing nodes

File: klea_utils/nodes/base.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from functools import cached_property
from pathlib import Path
from textwrap import dedent
from typing import Any, Literal, cast

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompt_values import PromptValue
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable, RunnableConfig
from langchain_core.utils.function_calling import convert_to_json_schema
from langgraph.runtime import get_runtime
from pydantic import BaseModel

from klea_utils.graph.context import model_overrides_from_context
from klea_utils.plogging import mask_sensitive

from ..errors import LLMInvocationErrorCategory, PromptTemplateError
from ..llm import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    add_memory_to_prompt,
    classify_llm_invocation_error,
    content_to_str,
    estimate_input_tokens,
    get_last_n_conversations,
    get_provider_allowed_fields,
    get_token_limit_param,
    is_output_truncated,
    load_prompt,
    parse_output_with_thought,
    resolve_langchain_endpoint,
    resolve_output_token_limit,
)
from ..models_catalog import probe_endpoint_model_limits
from .abstract import AbstractLLMNode

#: Max times to retry an invoke that overflowed the context window, each
#: time shrinking the reserved output window to free headroom.
MAX_CONTEXT_OVERFLOW_RETRIES = 3

#: Max times to retry an invoke whose output was truncated (``finish_reason
#: == "length"``), each time growing the reserved output window.  The
#: budget covers the full climb from the smallest node window to the
#: largest advertised context (262144) so heavy-thinking models are not
#: cut short, while still bounding pathological repeated truncations.
MAX_TRUNCATION_RETRIES = 15

#: Linear-phase step for truncation retries: the window grows by
#: ``TRUNCATION_LINEAR_STEP`` while below ``TRUNCATION_LINEAR_CAP``.
#: Covers the common case -- a model cut off a few hundred to a couple of
#: thousand tokens short of finishing -- with small, predictable
#: reservations.
TRUNCATION_LINEAR_STEP = 2048

#: Ceiling for the linear phase; above it the window grows by
#: :data:`TRUNCATION_PHASE2_STEP`.
TRUNCATION_LINEAR_CAP = 16384

#: Large-step phase for truncation retries: once the linear phase is
#: exhausted the window grows by this fixed step up to the output ceiling.
#: Fixed-size steps (not exponential) keep reservations predictable for
#: inference engines that allocate resources against ``max_tokens`` (e.g.
#: vLLM KV-cache), avoiding the spikes of a doubling ladder.
TRUNCATION_PHASE2_STEP = 32768

#: Output-window ceiling for truncation retries.  Reasoning-capable models
#: can spend a large, unpredictable number of tokens thinking before
#: producing a small final answer, so the grow ladder clamps at a generous
#: ceiling (mirrors opencode's ``OUTPUT_TOKEN_MAX``).  This is the
#: fallback when the model's context is unknown; for a custom endpoint
#: that advertises a larger ``max_model_len``,
#: :meth:`BaseLLMNode._jump_output_target` raises the ceiling to the
#: endpoint's remaining context.  It is still clamped to the model's
#: catalog output limit / total budget by ``resolve_output_token_limit``.
MAX_OUTPUT_TOKENS_CEILING = 32768

#: Floor for the reserved output window when shrinking it on overflow.
MIN_OUTPUT_TOKENS = 64


def _schema_to_example(schema: dict[str, Any]) -> Any:
    """Generate a placeholder example value from a JSON schema fragment.

    Walks a JSON Schema fragment (as produced by
    ``convert_to_json_schema``) and returns a placeholder value for each
    type, so the prompt can show the model a concrete instance to imitate
    instead of the abstract schema definition (which invites the model to
    echo the schema back verbatim instead of producing an instance).

    :param schema: JSON Schema fragment (a ``{"type": ...}`` dict)
    :returns: A placeholder value matching the schema's type
    """
    if schema.get("enum"):
        return schema["enum"][0]
    match schema.get("type"):
        case "string":
            return "text"
        case "integer" | "number":
            return 0
        case "boolean":
            return True
        case "array":
            return [_schema_to_example(schema.get("items", {}))]
        case "object":
            return {
                key: _schema_to_example(value)
                for key, value in schema.get("properties", {}).items()
            }
        case _:
            return None


def _is_empty_result(result: Any, schema: type[BaseModel] | None = None) -> bool:
    """Return True if *result* carries no usable content.

    A structured output that parsed to an all-default instance (the model
    echoed the schema back instead of producing an instance) compares equal
    to a freshly-constructed default; a non-structured response is empty
    when its content is blank.  Used to flag silently-degraded LLM output.

    :param result: Processed output from :meth:`BaseLLMNode._process_output`
    :param schema: The node's output schema, or ``None`` for non-structured
    :returns: True when nothing usable was produced
    """
    if schema is not None:
        return isinstance(result, schema) and result == schema()
    if isinstance(result, AIMessage):
        return not content_to_str(result.content).strip()
    return False


class BaseLLMNode[TState: BaseModel, TOutput: BaseModel](
    AbstractLLMNode[TState, TOutput]
):
    """Base class for LangGraph nodes that load prompts from files.

    Extends AbstractLLMNode with:
    - File-based prompt loading via load_prompt()
    - Optional memory support (appends memory content to system prompt)
    - Auto-derived prompt registry location from subclass file path

    Prompt files are expected to be named ``{prefix}_system.md`` and
    ``{prefix}_user.md``.

    Subclasses can override ``prompt_prefix`` or ``prompt_registry_location``
    via the setter if the defaults (lowercase class name / sibling ``prompts/``)
    are not appropriate.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        output_schema: type[TOutput] | None,
        memory: bool = False,
    ):
        """Initialize with file-based prompt loading and memory support.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param output_schema: Pydantic schema for structured output
        :param memory: Whether to append memory content to the system prompt
        """
        super().__init__(logger, label, llm_models, output_schema=output_schema)

        self._prompt_prefix: str | None = None
        self._prompt_registry_location: Path | None = None
        self.memory = memory

    @property
    def prompt_prefix(self) -> str:
        """Return the prompt file prefix.

        Falls back to the lowercase class name if not explicitly set.
        """
        if self._prompt_prefix is not None:
            return self._prompt_prefix
        return self.__class__.__name__

    @prompt_prefix.setter
    def prompt_prefix(self, value: str) -> None:
        """Set the prompt file prefix."""
        self._prompt_prefix = value

    @property
    def prompt_registry_location(self) -> Path:
        """Return path to the prompts directory.

        Falls back to a sibling ``prompts/`` directory relative to the
        subclass file if not explicitly set.
        """
        if self._prompt_registry_location is not None:
            return self._prompt_registry_location

        subclass_file = inspect.getfile(self.__class__)
        loc = Path(subclass_file).parent / "prompts"
        self.logger.debug(f"No prompt registry location set. Falling back to {loc}")
        return loc

    @prompt_registry_location.setter
    def prompt_registry_location(self, value: Path) -> None:
        """Set the prompts directory path."""
        self._prompt_registry_location = value

    @property
    def output_schema(self) -> type[TOutput] | None:
        """Return Pydantic schema for structured output if required"""
        return self._output_schema

    @output_schema.setter
    def output_schema(self, value: type[TOutput] | None) -> None:
        """Set Pydantic schema for structured output"""
        self._output_schema = value

    @cached_property
    def output_schema_json(self) -> dict[str, Any]:
        """Return JSON schema string for use in prompts."""
        return convert_to_json_schema(self.output_schema) if self.output_schema else {}

    def _configure_llm(self) -> tuple[Runnable, RunnableConfig]:
        """Configure LLM and build per-invoke config.

        Returns the raw ``instance`` (a ``_ConfigurableModel``) without
        wrapping it  ---  structured output is applied inside ``_invoke_llm``
        so that providers that reject ``response_format`` can fall back to
        prompt-based structured output.

        :returns: (llm_instance, config_dict) where config_dict is a
            ``RunnableConfig`` with ``configurable`` populated.
        """
        inst = self._llm_entry.instance
        config = self._build_invoke_config()
        self.logger.debug(f"{self.model_type = }\n{config = }")
        return inst, config

    def _build_invoke_config(self) -> RunnableConfig:
        """Build the per-invoke RunnableConfig.

        Delegates the full merge (role defaults -> context overrides ->
        node defaults -> provider defaults, including model-string parsing)
        to ``LLMModel.build_config()``, then resolves the bounded max-output
        token param (translated + clamped to the catalog's output/context
        limits) before applying provider field filtering to strip fields
        invalid for the resolved provider.
        """
        # Per-run model overrides come from the LangGraph Runtime context
        # (get_runtime().context, ADR-0033), not an ad-hoc contextvar.
        # Ambient access: no node signature takes a ``runtime`` parameter;
        # the value is static for the whole run.  Outside a graph run
        # (unit tests) get_runtime() raises, so tests drive the merge via
        # a Runtime-context harness instead of calling this directly.
        ctx_val = model_overrides_from_context(get_runtime().context)
        role_overrides = ctx_val.get(self.model_type, {})
        self.logger.debug(
            f"{mask_sensitive(ctx_val) = }\n"
            f"{self.model_type = }\n"
            f"{mask_sensitive(role_overrides) = }\n"
            f"{self.model_defaults = }"
        )

        # Delegate merge + model parsing to LLMModel.
        config = self._llm_entry.build_config(
            context_overrides=role_overrides,
            node_defaults=self.model_defaults,
        )

        # Get the merged configurable dict for provider field filtering.
        overrides: dict[str, Any] = config["configurable"]

        # A missing model (no env default and no per-chat override) cannot
        # invoke the LLM.  Raise a clear, actionable error instead of the
        # confusing provider-level "Missing credentials" error that an empty
        # model would otherwise produce.
        if not overrides.get("model"):
            raise RuntimeError(
                f"No model configured for role '{self.model_type}'. "
                f"Set the {self.model_type.upper()}_MODEL environment "
                "variable (e.g. KLEA_AGENT_CHAT_MODEL) or set a model for "
                "this chat from the web UI (Choose models)."
            )

        # --- Bounded output tokens ---
        # Guarantee a finite max-output token param for the resolved
        # provider, clamped to the model's catalog output limit and total
        # budget (input + output <= context).  Must run before provider
        # field filtering so the translated provider token param survives.
        # This is the normal (non-retry) path: use_endpoint stays False so
        # we use the fast, offline models.dev value (the purpose here is a
        # finite budget, mainly for the HuggingFace whole-window
        # reservation).  The retry path in _update_output_window opts into
        # the live endpoint for an accurate cap.
        input_chars = len(self._last_prompt.to_string()) if self._last_prompt else None
        resolve_output_token_limit(
            overrides,
            provider=overrides.get("model_provider") or "openai",
            role=self.model_type,
            input_chars=input_chars,
        )

        # --- Provider field filtering ---
        active_provider = overrides.get("model_provider") or "openai"
        provider_allowed = get_provider_allowed_fields(active_provider)
        allowed = provider_allowed | {"model", "model_provider"}
        overrides = {k: v for k, v in overrides.items() if k in allowed}
        self.logger.debug(
            f"After provider field filtering ({active_provider = }):\n{mask_sensitive(overrides) = }"
        )

        return cast(RunnableConfig, {"configurable": overrides})

    def _add_cache_control(
        self, prompt: PromptValue, config: RunnableConfig
    ) -> PromptValue:
        """Add Anthropic cache_control to the system message if applicable.

        Only Anthropic supports ``cache_control: ephemeral`` on system blocks,
        and it requires the ``model_provider`` to be known (resolved at
        invoke time, not at prompt creation). Other providers ignore it, so
        we only set it for ``anthropic``.

        Anthropic prompt caching needs ``cache_control`` *inside* the
        structured system content block.  langchain-anthropic serialises a
        plain-string ``SystemMessage`` as the bare ``system`` field and drops
        ``additional_kwargs``, so tagging there is a silent no-op  ---  the
        string content must be wrapped in a text block carrying the flag.
        """
        provider = config.get("configurable", {}).get("model_provider")
        if provider != "anthropic":
            return prompt
        # PromptValue -> messages -> move cache_control into the first
        # SystemMessage's structured content block
        try:
            messages = prompt.to_messages()
            if messages and messages[0].type == "system":
                system = messages[0]
                content = system.content
                if isinstance(content, str):
                    system.content = [
                        {
                            "type": "text",
                            "text": content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                elif isinstance(content, list):
                    for i in range(len(content) - 1, -1, -1):
                        block = content[i]
                        if isinstance(block, str):
                            content[i] = {
                                "type": "text",
                                "text": block,
                                "cache_control": {"type": "ephemeral"},
                            }
                            break
                        if isinstance(block, dict) and block.get("type") == "text":
                            content[i] = {
                                **block,
                                "cache_control": {"type": "ephemeral"},
                            }
                            break
                    else:
                        return prompt
                    system.content = content
                else:
                    return prompt
                self.logger.debug("Added cache_control for Anthropic call")
                # Rebuild PromptValue from modified messages
                from langchain_core.prompt_values import ChatPromptValue

                return ChatPromptValue(messages=messages)
        except Exception as exc:  # noqa: BLE001
            self.logger.debug(f"Failed to add cache_control: {exc}")
        return prompt

    async def _invoke_llm(
        self, llm: Runnable, prompt: PromptValue, config: RunnableConfig
    ) -> AIMessage | dict[str, Any]:
        """Async invoke LLM with optional structured output + fallback.

        Wraps the configurable model with ``with_structured_output``
        when an output schema exists.  If the provider rejects the
        ``response_format`` parameter (e.g. some custom OpenAI-compatible
        endpoints), falls back to a plain invoke  ---  the prompt already
        contains the JSON schema as text instructions.

        Both paths route through :meth:`_invoke_with_retries` for adaptive
        retries on context overflow / truncated output.
        """
        prompt = self._add_cache_control(prompt, config)
        self.logger.debug(f"{prompt = }")
        inst = self._llm_entry.instance
        if self.output_schema:
            llm_wrapped = inst.with_structured_output(
                self.output_schema, method="json_schema", include_raw=True
            )
            try:
                output = await self._invoke_with_retries(
                    llm_wrapped.ainvoke, prompt, config
                )
            except Exception as exc:
                if (
                    classify_llm_invocation_error(exc)
                    is LLMInvocationErrorCategory.STRUCTURED_OUTPUT_REJECTED
                ):
                    self.logger.warning(
                        "Structured output not supported, falling back to prompt-based"
                    )
                    output = await self._invoke_with_retries(
                        inst.ainvoke, prompt, config
                    )
                else:
                    raise
        else:
            output = await self._invoke_with_retries(inst.ainvoke, prompt, config)
        self.logger.debug(f"{output = }")
        return output

    def _update_output_window(
        self,
        config: RunnableConfig,
        direction: Literal["shrink", "grow"],
    ) -> bool:
        """Resize the reserved output window.

        Updates ``config["configurable"]`` in place.  ``"shrink"`` halves
        the window (context-overflow retry); ``"grow"`` extends it
        (truncation retry) in two bounded phases: a linear step-up
        (:data:`TRUNCATION_LINEAR_STEP` while below
        :data:`TRUNCATION_LINEAR_CAP`) for models cut off just short of
        finishing, then fixed large steps (:data:`TRUNCATION_PHASE2_STEP`)
        climbing toward the output ceiling (:meth:`_jump_output_target`).
        No exponential doubling: inference engines allocate resources
        against ``max_tokens``, so doubling spikes KV-cache usage,
        overshoots the real need, and overpays hallucination loops.  Both
        grow paths re-clamp to the model's catalog output limit and total
        budget via ``resolve_output_token_limit`` (endpoint-first context
        on these retries).

        :param config: The per-invoke RunnableConfig to update in place.
        :param direction: ``"shrink"`` or ``"grow"``.
        :returns: True if the window actually changed, False if it was
            already at a bound (no point retrying).
        """
        overrides = config["configurable"]
        # Native providers (``mistral:``, ``anthropic:``, ``deepseek:``,
        # ...) carry no ``base_url`` in the configurable dict -- the
        # provider resolves its own default endpoint internally.  Resolve
        # it by materialising the concrete model (already built on every
        # invoke, no network) and reading its resolved endpoint attribute,
        # so the endpoint-aware retry probes target the right server.
        # Storing it back in *overrides* is safe: every provider that
        # exposes one of the endpoint attributes aliases ``base_url``, so
        # the retried invoke accepts it.
        if not overrides.get("base_url"):
            resolved_base_url = resolve_langchain_endpoint(
                self._llm_entry.instance, config
            )
            if resolved_base_url:
                overrides["base_url"] = resolved_base_url
        provider = overrides.get("model_provider") or "openai"
        token_param = get_token_limit_param(provider)
        current = int(overrides.get(token_param, DEFAULT_MAX_OUTPUT_TOKENS))

        if direction == "shrink":
            target = max(MIN_OUTPUT_TOKENS, current // 2)
            phase = "-"
        else:
            # Two-phase grow (see docstring).  Clamp to the output ceiling
            # so the ladder never exceeds what the endpoint (or the fixed
            # fallback) allows; the resolver below re-applies the catalog
            # output / total-budget clamps.
            if current < TRUNCATION_LINEAR_CAP:
                step = TRUNCATION_LINEAR_STEP
                phase = "linear"
            else:
                step = TRUNCATION_PHASE2_STEP
                phase = "large-step"
            target = min(current + step, self._jump_output_target(overrides))

        # Set the provider token param directly (rather than the generic
        # key) so the resolver's "explicit value wins" precedence does not
        # pick up a stale explicit value; resolve then re-applies the
        # catalog output / total-budget clamps to *target*.  Shrink and
        # grow are retries, so use the live endpoint for an accurate
        # context cap (falling back to models.dev), unlike the normal path.
        overrides[token_param] = target
        last_prompt = getattr(self, "_last_prompt", None)
        input_chars = len(last_prompt.to_string()) if last_prompt else None
        resolve_output_token_limit(
            overrides,
            provider=provider,
            role=self.model_type,
            input_chars=input_chars,
            use_endpoint=True,
        )
        new_value = int(overrides[token_param])
        self.logger.warning(
            "Output window %s (%s): %d -> %d (%s)",
            direction,
            phase,
            current,
            new_value,
            provider,
        )
        return new_value != current

    def _jump_output_target(self, overrides: dict[str, Any]) -> int:
        """Return the output-window ceiling for truncation retries.

        The ceiling is :data:`MAX_OUTPUT_TOKENS_CEILING` by default, but
        for an OpenAI-compatible custom endpoint that advertises a larger
        ``max_model_len`` we raise it to the endpoint's remaining context
        (``max_model_len - estimated input``) so a long reasoning trace is
        not capped by the fixed fallback.  The grow ladder in
        :meth:`_update_output_window` clamps to this ceiling; the live
        endpoint is trusted as the source of truth for the retry ceiling,
        so no models.dev ``output`` clamp is applied below it -- the
        resolver re-applies that clamp to the *target*, not the ceiling.

        :param overrides: The merged ``configurable`` dict.
        :returns: The ceiling for the grow ladder.
        """
        target = MAX_OUTPUT_TOKENS_CEILING
        provider = overrides.get("model_provider") or "openai"
        limits = probe_endpoint_model_limits(
            provider,
            overrides.get("model", ""),
            overrides.get("base_url"),
            overrides.get("api_key"),
        )
        if limits and limits.context:
            last_prompt = getattr(self, "_last_prompt", None)
            input_chars = len(last_prompt.to_string()) if last_prompt else None
            if input_chars is not None:
                headroom = limits.context - estimate_input_tokens(input_chars)
                target = max(target, headroom)
        self.logger.debug(
            f"Output ceiling {target = } "
            f"(endpoint_context = {limits.context if limits else None}, "
            f"{provider = })"
        )
        return target

    async def _invoke_with_retries(
        self,
        invoke: Callable[..., Awaitable[Any]],
        prompt: PromptValue,
        config: RunnableConfig,
    ) -> AIMessage | dict[str, Any]:
        """Invoke an LLM with adaptive retries on length-related failures.

        Two retry behaviours, both bounded:

        * ``context_overflow`` errors (request rejected because input plus
          the reserved output exceeds the window) retry up to
          :data:`MAX_CONTEXT_OVERFLOW_RETRIES` times, shrinking the
          output window each time.
        * Truncation retries by growing the output window in two bounded
          phases -- a linear step-up, then fixed large steps up to the
          output ceiling (see :meth:`_update_output_window`) -- capped at
          :data:`MAX_TRUNCATION_RETRIES` attempts.  Truncation is detected
          two ways: a successful call that was cut off (``finish_reason ==
          "length"``), or a raised ``length_truncation`` exception (some
          SDKs, e.g. the OpenAI streaming / structured-output path, raise
          instead of returning a truncated message).

        All other failures (rate limits, auth, model-not-found, ...) are
        re-raised immediately.  Retrying stops early if resizing the
        window makes no progress (already at a bound).

        :param invoke: Async callable ``(prompt, config) -> output``.
        :param prompt: The prompt to invoke.
        :param config: Per-invoke RunnableConfig (mutated between attempts).
        :returns: The (non-truncated) LLM output.
        """
        overflow_retries = 0
        truncation_retries = 0

        while True:
            try:
                output = await invoke(prompt, config=config)
            except Exception as exc:
                self.logger.error(f"{exc = }")
                category = classify_llm_invocation_error(exc)
                if (
                    category is LLMInvocationErrorCategory.CONTEXT_OVERFLOW
                    and overflow_retries < MAX_CONTEXT_OVERFLOW_RETRIES
                ):
                    overflow_retries += 1
                    if not self._update_output_window(config, "shrink"):
                        self.logger.warning(
                            "Context overflow but output window cannot shrink further"
                        )
                        raise
                    self.logger.warning(
                        "Context overflow, retrying with smaller output window (%d/%d)",
                        overflow_retries,
                        MAX_CONTEXT_OVERFLOW_RETRIES,
                    )
                    continue

                if category is LLMInvocationErrorCategory.LENGTH_TRUNCATION:
                    grow, message = self._next_truncation_retry(
                        config, truncation_retries
                    )
                    if grow:
                        truncation_retries += 1
                        self.logger.warning(message)
                        continue
                    raise
                raise

            if is_output_truncated(output):
                grow, message = self._next_truncation_retry(config, truncation_retries)
                if grow:
                    truncation_retries += 1
                    self.logger.warning(message)
                    continue
                return output

            return output

    def _next_truncation_retry(
        self, config: RunnableConfig, truncation_retries: int
    ) -> tuple[bool, str]:
        """Return whether to retry a truncated invoke, and the log message.

        Grows the reserved output window on each truncation -- linear
        steps while below :data:`TRUNCATION_LINEAR_CAP`, then
        :data:`TRUNCATION_PHASE2_STEP` steps up to the output ceiling
        (:meth:`_jump_output_target`) -- capped at
        :data:`MAX_TRUNCATION_RETRIES` attempts.  Stops retrying once the
        window cannot grow further (already at the ceiling) or the retry
        budget is spent.

        :param config: The per-invoke RunnableConfig to update in place.
        :param truncation_retries: Number of truncation retries so far.
        :returns: ``(grow, message)``; ``grow`` is False when the window
            cannot grow further or the budget is exhausted.
        """
        if truncation_retries >= MAX_TRUNCATION_RETRIES:
            self.logger.warning(
                "Output truncated but truncation retry budget exhausted"
            )
            return False, ""
        if self._update_output_window(config, "grow"):
            return (
                True,
                (
                    "Output truncated, retrying with larger output window "
                    f"({truncation_retries + 1}/{MAX_TRUNCATION_RETRIES})"
                ),
            )
        self.logger.warning("Output truncated but output window cannot grow further")
        return False, ""

    def _process_output(self, output: AIMessage | dict[str, Any]) -> Any:
        """Common output processing with error handling.

        NOTE: structured output is best-effort.  A model can return a valid
        JSON object that is not an instance of the schema (e.g. it echoes the
        schema definition back, or returns only defaults); the parser then
        yields an all-default instance without any ``parsing_error``.  An
        empty result here is therefore a possible failure mode, not a normal
        "the model had nothing to say" response.  The prompt (example instance
        + directive) reduces the odds; if empty results recur for a model,
        revisit the prompt/model rather than expecting a loud invocation
        error.
        """
        result: TOutput | AIMessage | None = None
        schema = self.output_schema

        if schema:
            # but answer is returned as message instead of json/dict
            if isinstance(output, AIMessage):
                result, _ = parse_output_with_thought(output, schema)
                if isinstance(result, dict):
                    result = schema(**result)
            else:
                assert isinstance(output, dict)
                if output["parsing_error"]:
                    self.logger.warning(
                        f"LLM parsing error, using fallback: {output['parsing_error']}"
                    )
                    result, _ = parse_output_with_thought(output["raw"], schema)
                else:
                    result = output["parsed"]
                    if isinstance(result, dict):
                        result = schema(**result)
                    else:
                        if not isinstance(result, schema):
                            self.logger.critical(
                                f"Unexpected output type: {type(result)}"
                            )
                            result = self._get_default_error_result()

            self.logger.debug(f"Processed output: {result}")
        else:
            assert isinstance(output, AIMessage)
            result = output
            self.logger.debug(
                f"No output schema. Returning unprocessed output: {result}"
            )

        if _is_empty_result(result, self.output_schema):
            self.logger.warning(
                f"Empty LLM output from {self.label}: nothing usable was "
                f"produced (all-default structured result or blank message)"
            )

        return result

    def _invoke_prompt(
        self, prompt_template: ChatPromptTemplate, variables: Any | dict[str, Any]
    ) -> PromptValue:
        """Format prompt with state-specific parameters"""
        prompt = prompt_template.invoke(variables)
        self.logger.debug(f"{prompt =}")
        return prompt

    def _format_output_schema_prompt(self) -> str:
        """Return the ``Output schema (strict)`` prompt block.

        The raw JSON Schema (``title``/``type``/``properties``) invites
        models to echo the schema definition back instead of producing an
        instance (the observed failure mode), so the prompt shows a
        sanitized schema (top-level ``title``/``description`` dropped), an
        explicit directive, and a generated example instance.

        :returns: Prompt text describing the required JSON output
        """
        schema = {
            key: value
            for key, value in self.output_schema_json.items()
            if key not in ("title", "description")
        }
        example = _schema_to_example(self.output_schema_json)
        return dedent(
            f"""
            ## Output schema (strict)

            Respond in JSON following this schema:

            {json.dumps(schema).replace("{", "{{").replace("}", "}}")}

            The response must be a raw, valid JSON object like this example
            (replace the placeholder values with real content):

            {json.dumps(example).replace("{", "{{").replace("}", "}}")}

            Do not output the schema definition itself, or notes/comments, or
            the 'title'/'type'/'properties' keys.
            """
        )

    def _get_system_prompt(self, state: TState) -> str | list:
        """Load system prompt from file, optionally adding memory and schema.

        When memory is enabled, returns a list of ``("system", text)`` plus
        the recent conversation as real message objects (in interleaved
        order), so ``_create_prompt_template`` can place them between the
        system and human prompts.  Otherwise returns the system prompt text
        as a plain string.

        :param state: Graph state.
        :returns: System prompt text, or a list of system text + history
            message objects when memory is enabled.
        """
        system_prompt = self._load_prompt_file(f"{self.prompt_prefix}_system")

        if self.output_schema:
            system_prompt += self._format_output_schema_prompt()

        if self.memory:
            memory_addition = self._get_memory_addition(state)
            system_prompt += memory_addition

            system_messages: list[Any] = [("system", system_prompt)]
            system_messages += self._get_recent_memory_messages(state)
            self.logger.debug(f"{system_messages =}")
            return system_messages

        self.logger.debug(f"{system_prompt =}")
        return system_prompt

    def _get_recent_memory_messages(self, state: TState) -> list[BaseMessage]:
        """Return the recent verbatim history messages for the prompt.

        Forward-stable: ``messages[summarised_till - N:]`` keeps the cached
        prefix stable (``messages[summarised_till]`` stays for ``5`` turns)
        vs ``get_recent_messages`` ``last N`` which drifts every turn. ``N=2``
        keeps the last ``human``/``ai`` pair for continuity even after
        ``context_summary`` has absorbed it.

        :param state: Graph state.
        :returns: Ordered recent human/ai messages.
        """
        # Fallback: if state has no summarised_till (e.g. legacy checkpoint),
        # include all messages and skip summarisation (caller handles).
        if not hasattr(state, "summarised_till"):
            return [
                m  # type: ignore[return-value]
                for m in state.messages  # type: ignore
                if isinstance(m, (HumanMessage, AIMessage))
            ]
        try:
            till = int(getattr(state, "summarised_till", 0) or 0)
        except (TypeError, ValueError):
            till = 0
        N = 2  # one human+ai pair overlap for continuity
        start = max(0, till - N)
        # get_last_n_conversations filters to Human/AI and preserves order
        _, recent = get_last_n_conversations(state.messages, start=start)  # type: ignore
        return recent

    def _get_human_prompt(self, state: TState) -> str:
        """Load human prompt from file."""
        human_prompt = self._load_prompt_file(f"{self.prompt_prefix}_user")

        self.logger.debug(f"{human_prompt =}")
        return human_prompt

    def _load_prompt_file(self, prompt_name: str) -> str:
        """Load a prompt file from the registry.

        :param prompt_name: Prompt file name (without extension)
        :returns: Prompt text content
        """
        return load_prompt(
            prompt_name=prompt_name,
            prompt_registry_location=self.prompt_registry_location,
        )

    def _create_prompt_template(
        self, system_prompt: str | list[Any], human_prompt: str
    ) -> ChatPromptTemplate:
        """Create ChatPromptTemplate with system and human messages.

        *system_prompt* may be a plain string (memory disabled) or a list of
        ``("system", text)`` plus recent history message objects (memory
        enabled); the human prompt is appended after it.

        :param system_prompt: System prompt text or a system-side list
            including recent history messages.
        :param human_prompt: Human prompt text.
        """
        system_messages = (
            [("system", system_prompt)]
            if isinstance(system_prompt, str)
            else system_prompt
        )

        if len(system_messages) and len(human_prompt):
            prompt_template = ChatPromptTemplate(
                [*system_messages, ("human", human_prompt)]
            )
        elif len(system_messages) and not len(human_prompt):
            prompt_template = ChatPromptTemplate(system_messages)
        elif len(human_prompt) and not len(system_messages):
            prompt_template = ChatPromptTemplate([("human", human_prompt)])
        else:
            raise PromptTemplateError(
                "No prompts provided. Cannot create prompt template!"
            )

        self.logger.debug(f"{prompt_template =}")
        return prompt_template

    def _get_memory_addition(self, state: TState) -> str:
        """Hook for subclasses to append memory content into the system prompt.

        Override this method to provide memory-specific content.
        The default implementation returns an empty string.
        """
        return add_memory_to_prompt(
            context_summary=state.context_summary,  # type: ignore
        )
