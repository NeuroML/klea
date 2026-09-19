#!/usr/bin/env python3
"""
LLM related utils

File: klea_rag/llm.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import sys
import time
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import Any, NamedTuple, cast

from json_repair import repair_json
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompt_values import PromptValue
from langgraph.types import RunnableConfig
from pydantic import BaseModel, ValidationError

from .errors import LLMInvocationErrorCategory
from .imports import require_extra
from .models_catalog import (
    MODELS_DEV_PROVIDERS_IGNORED,
    get_catalog_model_limits,
    get_provider_endpoint,
    probe_endpoint_model_limits,
)
from .plogging import mask_sensitive

logger = logging.getLogger(__name__)


class ParsedModelName(NamedTuple):
    """Parsed components of a model name string."""

    provider: str | None
    model_name: str
    suffix: str | None


def parse_model_name(raw: str) -> ParsedModelName:
    """Split a model name into provider, model identifier, and suffix.

    Follows the ``provider:model_id`` convention.  The provider is
    expected to be explicitly included; no provider inference is done.

    With three segments the third is treated as a ``suffix``
    (provider hint, model tag, base URL, etc.) *unless* the provider
    is ``ollama``, for which the second and third segments form the
    model name (``model_name:tag``).

    Examples:

    * ``ollama:bge-m3:latest`` -> provider=ollama, model=bge-m3:latest, suffix=None
    * ``huggingface:org/model:auto`` -> provider=huggingface, model=org/model, suffix=auto
    * ``custom:model:https://example.com/v1`` -> provider=custom, model=model, suffix=https://example.com/v1
    * ``openai:gpt-4o`` -> provider=openai, model=gpt-4o, suffix=None
    * ``bge-m3`` -> provider=None, model=bge-m3, suffix=None

    :param raw: Model name with optional provider prefix
    :returns: Parsed model name components
    """
    parts = raw.split(":", 2)

    if len(parts) == 1:
        return ParsedModelName(provider=None, model_name=raw, suffix=None)

    provider = parts[0].lower()
    if not provider:
        raise ValueError(f"Invalid model name {raw!r}: missing provider before ':'")
    if not parts[1]:
        raise ValueError(f"Invalid model name {raw!r}: missing model after ':'")

    if len(parts) == 2:
        return ParsedModelName(provider=provider, model_name=parts[1], suffix=None)

    if provider == "ollama":
        return ParsedModelName(
            provider=provider, model_name=f"{parts[1]}:{parts[2]}", suffix=None
        )

    return ParsedModelName(provider=provider, model_name=parts[1], suffix=parts[2])


#: Recognised wire-API endpoint suffixes for ``custom:`` model URLs, mapped to
#: the resolution target ``(model_provider, use_responses_api)``.  A ``None``
#: means the flag is not applicable (or is the provider default) and should be
#: left unset.  Detection is purely URL-path based so no external model list or
#: catalog is needed.
_CUSTOM_ENDPOINT_SURFACES: dict[str, tuple[str, bool | None]] = {
    "/chat/completions": ("openai", False),
    "/responses": ("openai", True),
    "/v1/messages": ("anthropic", None),
}

#: models.dev npm packages that are a known **non-OpenAI** wire surface Klea
#: does not implement (e.g. Google Gemini).  Catalog providers whose npm is
#: one of these are left to LangChain.  Everything else with an ``api`` is
#: treated as OpenAI-shaped -- ``@ai-sdk/openai-compatible``, the vendor
#: packages (``@openrouter/ai-sdk-provider``, ...), and the long tail -- which
#: is the catalog's dominant convention.
_CATALOG_NON_OPENAI_NPM: frozenset[str] = frozenset(
    {
        "@ai-sdk/google",
        "@ai-sdk/google-vertex",
        "@ai-sdk/google-vertex/anthropic",
        "@ai-sdk/amazon-bedrock",
        "@ai-sdk/amazon-bedrock/mantle",
        "@ai-sdk/cohere",
        "@ai-sdk/azure",
    }
)


class CustomEndpoint(NamedTuple):
    """Resolved API surface for a ``custom:`` model endpoint URL.

    ``base_url`` is the URL to hand to the provider SDK (the endpoint resource
    path is stripped, because the SDK appends it again).  ``model_provider`` is
    the LangChain provider to use, and ``use_responses_api`` selects the OpenAI
    Responses API when ``True`` (``None`` leaves the SDK default).
    """

    base_url: str
    model_provider: str
    use_responses_api: bool | None = None


def resolve_custom_endpoint(url: str) -> CustomEndpoint:
    """Detect the wire API surface from a ``custom:`` model endpoint URL.

    The suffix of *url* selects the API surface so that one ``custom:`` model
    string works for any OpenAI-compatible endpoint, the OpenAI Responses API,
    or the Anthropic Messages API (e.g. opencode Go serves different models on
    different surfaces).  Because each provider SDK appends its own resource
    path, the matched suffix is stripped from the returned ``base_url``:

    * ``.../chat/completions`` -> OpenAI Chat Completions (``openai``)
    * ``.../responses``        -> OpenAI Responses API (``openai``, responses)
    * ``.../v1/messages``      -> Anthropic Messages API (``anthropic``;
      the SDK re-appends ``/v1/messages``, so ``.../zen/go`` is returned)

    Any other URL is treated as a plain base URL and defaults to the standard
    OpenAI Chat Completions surface (``openai``, flag unset), preserving the
    behaviour for self-hosted OpenAI-compatible endpoints.

    :param url: The ``custom:`` model string suffix (a base URL or a full
        endpoint URL).
    :returns: The resolved :class:`CustomEndpoint`.
    :raises ValueError: If *url* is empty.
    """
    if not url:
        raise ValueError("Empty custom endpoint URL")

    stripped = url.rstrip("/")
    for suffix, (provider, use_responses_api) in _CUSTOM_ENDPOINT_SURFACES.items():
        if stripped.endswith(suffix):
            base_url = stripped[: -len(suffix)]
            if not base_url:
                raise ValueError(f"Custom endpoint URL {url!r} has no base path")
            logger.debug(
                f"Resolved custom endpoint {url!r} -> provider="
                f"{provider!r}, use_responses_api={use_responses_api!r}, "
                f"{base_url = }"
            )
            return CustomEndpoint(base_url, provider, use_responses_api)

    logger.debug(f"No known endpoint suffix in {url!r}; treating as an OpenAI base URL")
    return CustomEndpoint(url, "openai", None)


def resolve_catalog_provider_endpoint(
    provider: str, model_name: str | None = None
) -> CustomEndpoint | None:
    """Resolve a catalog provider to a wire surface via its models.dev entry.

    Lets a user write ``provider:model`` (e.g. ``openrouter:...``) without an
    explicit URL: the catalog's ``api`` supplies the base URL and its ``npm``
    package identifies the wire protocol.  The wire surface is resolved from
    the npm:

    * ``@ai-sdk/anthropic`` -> Anthropic Messages API
      (``/v1/messages``);
    * ``@ai-sdk/openai`` -> OpenAI Responses API (``/responses``);
    * ``@ai-sdk/openai-compatible`` (and anything else with an ``api``) ->
      OpenAI Chat Completions, the catalog's overwhelming default.

    The npm is resolved *per model* when a model name is given, so gateway
    providers that serve different models on different surfaces (OpenCode,
    OpenRouter, ...) pick the right one -- the catalog records this as a
    per-model ``provider.npm`` override.

    The returned ``base_url`` is what the SDK appends its resource path to.
    For the Anthropic surface the catalog ``api`` typically already ends in
    ``/v1`` (e.g. ``.../anthropic/v1``) while ``ChatAnthropic`` re-appends
    ``/v1/messages``, so a trailing ``/v1`` is stripped here; a full
    ``.../v1/messages`` is handled by :func:`resolve_custom_endpoint`.

    Returns ``None`` when the provider is not in the catalog, has no ``api``
    (native SDK providers resolve their own endpoint), uses a surface Klea
    does not implement (e.g. ``@ai-sdk/google``), or the catalog is
    unavailable -- callers then leave the model string to LangChain.

    :param provider: Klea provider id (e.g. ``"openrouter"``).
    :param model_name: Model identifier within the provider, or ``None``.
    :returns: The resolved :class:`CustomEndpoint`, or ``None``.
    """
    entry = get_provider_endpoint(provider, model_name)
    if entry is None or not entry.api:
        return None

    if entry.npm == "@ai-sdk/anthropic":
        # ``ChatAnthropic`` appends ``/v1/messages`` to ``anthropic_api_url``;
        # drop a trailing ``/v1`` (and any full ``/v1/messages``) first.
        base_url = entry.api.rstrip("/")
        for suffix in ("/v1/messages", "/v1"):
            if base_url.endswith(suffix):
                base_url = base_url[: -len(suffix)]
                break
        logger.debug(
            f"Catalog provider {provider!r} -> anthropic surface, {base_url = }"
        )
        return CustomEndpoint(base_url, "anthropic", None)

    if entry.npm == "@ai-sdk/openai":
        # The native OpenAI SDK surface is the Responses API; its base URL is
        # used as-is (the SDK appends ``/responses``).
        logger.debug(f"Catalog provider {provider!r} -> openai responses surface")
        return CustomEndpoint(entry.api, "openai", True)

    if entry.npm in _CATALOG_NON_OPENAI_NPM:
        # A known non-OpenAI wire surface Klea does not implement (e.g.
        # ``@ai-sdk/google``): leave the model string to LangChain rather than
        # guessing an OpenAI wire.  Everything else is assumed OpenAI-shaped
        # (``@ai-sdk/openai-compatible`` and vendor packages such as
        # ``@openrouter/ai-sdk-provider``), which is the catalog's default.
        logger.debug(
            f"Catalog provider {provider!r} uses unsupported npm {entry.npm!r}; "
            "leaving to LangChain"
        )
        return None

    resolved = resolve_custom_endpoint(entry.api)
    logger.debug(f"Catalog provider {provider!r} -> {resolved}")
    return resolved


def check_ollama_model(logger, model, exit=False):
    """Check if ollama model is available

    :param logger: logger instance
    :type logger: logging
    :param model: ollama model name
    :type model: str
    :param exit: if we should call sys.exit if check fails
    :type exit: bool
    :returns: None

    :throws ollama.ResponseError: if `model` is not available
    :throws ConnectionError: if cannot connect to an Ollama server

    """
    import ollama

    try:
        _ = ollama.show(model)
    except ollama.ResponseError:
        logger.error(f"Could not find ollama model: {model}")
        logger.error("Please ensure you have pulled the model")
        if exit:
            sys.exit(-1)
    except ConnectionError:
        logger.error("Could not connect to Ollama.")
        if exit:
            sys.exit(-1)


def parse_output_with_thought[TSchema: BaseModel](
    message: AIMessage, schema: type[TSchema]
) -> tuple[TSchema, str]:
    """Parse AI message with thought to a dict based on given schema"""
    # Lazy: JsonOutputParser pulls in langchain parsers
    from langchain_core.output_parsers import JsonOutputParser

    thought = ""
    answer = ""

    if isinstance(message.content, str):
        if "</think>" in message.content:
            splits = message.content.split("</think>")
            thought = splits[0].strip()
            answer = splits[1].strip()
        else:
            answer = message.content

        parser = JsonOutputParser()
        parser.pydantic_object = schema()
        try:
            result = parser.parse(answer)
        except OutputParserException:
            logger.debug(f"Handling OutputParserException. {answer = }")
            cleaned = repair_json(answer)
            logger.debug(f"{cleaned = }")
            result = parser.parse(cleaned)

    else:
        message.content = content_to_str(message.content)
        # Now a string -- re-run the string parsing above.
        if "</think>" in message.content:
            splits = message.content.split("</think>")
            thought = splits[0].strip()
            answer = splits[1].strip()
        else:
            answer = message.content

        parser = JsonOutputParser()
        parser.pydantic_object = schema()
        try:
            result = parser.parse(answer)
        except OutputParserException:
            logger.debug(f"Handling OutputParserException. {answer = }")
            cleaned = repair_json(answer)
            logger.debug(f"{cleaned = }")
            result = parser.parse(cleaned)

    logger.debug(f"{thought = }")
    logger.debug(f"{answer = }")

    return result, thought


def split_output_by_section(
    text: str, section_start_marker: str, section_end_marker: str | None = None
):
    """Split out thoughts and actual responses from AI responses"""
    if not text:
        logger.warning("Empty message.content. Nothing to do.")
        return "", ""

    if not section_start_marker:
        logger.warning("No starting marker. Nothing to do.")
        return "", ""

    if not section_end_marker:
        section_end_marker = None

    delimited, other = [], []

    # prepare pattern
    markers = [re.escape(section_start_marker)]
    if section_end_marker:
        markers.append(re.escape(section_end_marker))
    pattern = f"({'|'.join(markers)})"

    # split
    splits = re.split(pattern, text)

    # process splits
    # by default, we're outside the delimiters to begin with
    is_in = False

    # do we have both markers?
    found_start_marker = section_start_marker in text
    found_end_marker = section_end_marker in text if section_end_marker else False

    if not found_start_marker and not found_end_marker:
        logger.debug("No markers found. Nothing to do.")
        return "", text

    # end marker, but no start: we start inside the delimted region
    if found_end_marker and not found_start_marker:
        is_in = True

    for part in splits:
        if part == section_start_marker:
            is_in = True
        elif part == section_end_marker:
            is_in = False
        else:
            if is_in:
                delimited.append(part)
            else:
                other.append(part)

    delimited_text = "".join(delimited).strip()
    other_text = "".join(other).strip()

    # Add notes
    if found_start_marker and (section_end_marker and not found_end_marker):
        other_text += "\nNOTE: NO END MARKER FOUND"
    elif found_end_marker and not found_start_marker:
        other_text += "\nNOTE: NO START MARKER FOUND"

    return delimited_text, other_text


def content_to_str(
    content: str | list[dict | str] | None,
) -> str:
    """Normalise an ``AIMessage.content`` value to a plain string.

    AIMessage.content can be a plain string, a list of content blocks
    (when the LLM returns tool calls or structured output), or ``None``.
    This helper always returns a string suitable for downstream text
    processing (regex, ``in`` checks, prompt interpolation, etc.).

    :param content: The raw ``.content`` value from an AIMessage.
    :returns: A plain string.
    """
    if content is None:
        return ""
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return str(content)


def format_alert(text: str, level: str = "warning") -> str:
    """Wrap *text* as a GitHub-style markdown alert (e.g. ``> [!WARNING]``).

    Multi-line text is prefixed per line so the whole thing stays inside the
    blockquote.  Renderers with the markdown2 ``alerts`` extra (the NiceGUI
    speech bubbles) show it as a styled callout; others fall back to a plain
    blockquote.

    :param text: Alert body text
    :param level: Alert level (note, tip, important, warning, caution)
    :returns: Markdown alert blockquote
    """
    body = text.strip().replace("\n", "\n> ")
    return f"> [!{level.upper()}]\n> {body}"


def prompt_value_to_messages(prompt: PromptValue) -> list[dict]:
    """Convert a ``PromptValue`` to a clean list of message dicts.

    Each dict has ``role`` and ``content`` keys, suitable for JSON
    serialisation in the inspector debug panel.

    :param prompt: The LangChain ``PromptValue`` (filled, variables
        already substituted).
    :returns: A list of ``{"role": "...", "content": "..."}`` dicts.
    """
    return [
        {"role": msg.type, "content": content_to_str(msg.content)}
        for msg in prompt.to_messages()
    ]


def extract_llm_output_content(output: AIMessage | dict) -> str:
    """Extract plain-text content from an LLM output.

    Handles both ``AIMessage`` (non-structured output) and
    ``dict`` (structured output with ``raw`` / ``parsed`` keys):

    * ``AIMessage`` -- returns ``content_to_str(message.content)``.
    * ``dict`` -- extracts the ``raw`` ``AIMessage`` from a structured
      output response and returns its content; falls back to
      ``output["parsed"]`` and finally ``str(output)``.

    :param output: The raw output from ``llm.invoke()``.
    :returns: A plain-text string.
    """
    if isinstance(output, AIMessage):
        return content_to_str(output.content)
    if isinstance(output, dict):
        raw = output.get("raw")
        if isinstance(raw, AIMessage):
            return content_to_str(raw.content)
        parsed = output.get("parsed")
        if parsed is not None:
            return str(parsed)
    return str(output)


def is_output_truncated(output: AIMessage | dict[str, Any]) -> bool:
    """Return True if an LLM output was truncated by the max-token limit.

    Providers signal truncation via ``finish_reason == "length"`` on the
    message metadata.  Handles both plain ``AIMessage`` outputs and
    structured-output dicts (``{"raw": AIMessage, ...}``), and the
    list-form ``finish_reason`` some providers return.

    :param output: The raw output from ``llm.invoke()``.
    :returns: True when the model stopped because it hit the output cap.
    """
    if isinstance(output, dict):
        raw = output.get("raw")
        if isinstance(raw, AIMessage):
            output = raw
    if not isinstance(output, AIMessage):
        return False
    metadata = output.response_metadata or {}
    finish_reason = metadata.get("finish_reason")
    if isinstance(finish_reason, list):
        finish_reason = finish_reason[-1] if finish_reason else None
    return str(finish_reason).lower() == "length"


def is_output_empty(output: AIMessage | dict[str, Any]) -> bool:
    """Return True if an LLM output carries no usable text content.

    Some providers (notably HuggingFace) intermittently return a successful
    response with blank content.  Handles both plain ``AIMessage`` outputs
    and structured-output dicts (``{"raw": AIMessage, "parsed": ...}``); for
    the structured form the ``raw`` message is inspected, so a blank raw
    response is flagged even when the parser produced an all-default
    instance.

    :param output: The raw output from ``llm.invoke()``.
    :returns: True when the output has no non-whitespace text.
    """
    if isinstance(output, dict):
        raw = output.get("raw")
        if isinstance(raw, AIMessage):
            return not content_to_str(raw.content).strip()
        # No raw message to inspect: empty unless the parser still produced
        # a non-None structured result.
        return output.get("parsed") is None
    if isinstance(output, AIMessage):
        return not content_to_str(output.content).strip()
    return False


def is_empty_structured_parse_error(exc: BaseException) -> bool:
    """Return True if *exc* is a structured-output parse error on blank text.

    On the structured path, ``with_structured_output(...).ainvoke`` **raises**
    :class:`OutputParserException` for a blank model response instead of
    returning a dict, so :func:`is_output_empty` never sees it and the
    empty-response retry in ``BaseLLMNode._invoke_with_retries`` is skipped.
    LangChain's parsers phrase the failure as ``"Invalid json output: ..."``
    followed by the (here empty) raw text, so match that shape rather than
    treating every parser error as retryable: a non-empty invalid payload is
    a real schema mismatch that should not be retried.

    :param exc: The exception raised by the structured-output invoke.
    :returns: True when the parser failed because the response was blank.
    """
    if not isinstance(exc, OutputParserException):
        return False
    text = str(exc)
    marker = "invalid json output:"
    lower = text.lower()
    if marker not in lower:
        return False
    payload = text[lower.index(marker) + len(marker) :]
    # Strip the LangChain troubleshooting URL/whitespace before judging.
    payload = re.split(
        r"for troubleshooting", payload, maxsplit=1, flags=re.IGNORECASE
    )[0]
    return not payload.strip()


def is_structured_output_failure(exc: BaseException) -> bool:
    """Return True if *exc* means the structured call produced no usable output.

    The structured path (``with_structured_output(...).ainvoke``) can fail in
    ways the plain invoke cannot: the provider rejects the ``response_format``
    parameter, or the model returns content that does not satisfy the schema.
    Some SDKs surface the latter as a pydantic :class:`ValidationError` (the
    OpenAI client validates JSON inside its own parser) rather than a LangChain
    parser error, so both types are treated as a structured failure.

    Detection is by exception type wherever possible, so a provider's new error
    wording does not need a new pattern; the one message-classified case is
    :data:`~klea_utils.errors.LLMInvocationErrorCategory.STRUCTURED_OUTPUT_REJECTED`
    (parameter refusal), which has no distinct exception type.

    Empty responses are deliberately included: the caller decides whether to
    retry them on the structured path or fall back to plain.

    :param exc: Exception raised by the structured invoke.
    :returns: True when the structured call should give way to a plain invoke.
    """
    if isinstance(exc, (ValidationError, OutputParserException, json.JSONDecodeError)):
        return True
    return (
        classify_llm_invocation_error(exc)
        is LLMInvocationErrorCategory.STRUCTURED_OUTPUT_REJECTED
    )


def get_token_limit_param(provider: str) -> str:
    """Return the max-output token parameter name for a provider.

    Providers disagree on the parameter name for the maximum number of
    output tokens: Ollama uses ``num_predict``, while HuggingFace's
    ``ChatHuggingFace`` (which internally maps it to ``max_new_tokens``)
    and other OpenAI-compatible providers all use ``max_tokens``.

    .. note:: Known benign warning

       When Klea resolves ``max_tokens`` for HuggingFace, the inner
       ``HuggingFaceEndpoint`` constructed by ``ChatHuggingFace.from_model_id``
       (which declares ``max_new_tokens``, not ``max_tokens``) logs
       ``WARNING! max_tokens is not default parameter`` and shuffles it
       into ``model_kwargs``.  This is a false positive: the limit is still
       delivered correctly as ``max_tokens`` to ``InferenceClient.chat_completion``
       via the outer ``ChatHuggingFace``, which is the parameter the
       HuggingFace Inference API actually accepts.  Do not "fix" it by
       switching to ``max_new_tokens`` here.

    :param provider: Klea provider id (``huggingface``, ``ollama``, ...)
    :returns: The token parameter name to send in the invoke config.
    """
    if provider == "ollama":
        return "num_predict"
    return "max_tokens"


#: Fallback max output tokens used when no node/role default provides a value.
DEFAULT_MAX_OUTPUT_TOKENS = 4096

#: Built-in per-role max output token defaults.  Nodes may override these
#: with the generic ``max_output_tokens`` key in ``model_defaults``, and
#: admins may override them per provider via the ``providers`` config
#: section.
_ROLE_MAX_OUTPUT_TOKENS: dict[str, int] = {
    "chat": 4096,
    "plan": 4096,
    "guard": 1024,
}

#: The three max-output token parameter names used across providers.
_TOKEN_PARAMS = ("max_tokens", "max_new_tokens", "num_predict")


#: Safety factor applied on top of the char-based token estimate so the
#: reserved output window keeps headroom for tokenizer variance: the
#: ``~4 chars/token`` rule can undercount the real token count (e.g. 9736
#: chars estimate 2434 tokens while the server tokenizes 2435), which
#: pushes input + output one token over the model's context window and
#: fails the request.
INPUT_TOKEN_ESTIMATE_SAFETY_FACTOR = 1.05


def estimate_input_tokens(input_chars: int) -> int:
    """Conservative token estimate for a prompt's character count.

    Used to keep the reserved output window within a model's total budget
    (input + output <= context).  ~4 characters per token is a reasonable
    average for mixed English/code text; an exact count would require
    provider-specific tokenizers.  The estimate is deliberately inflated
    by :data:`INPUT_TOKEN_ESTIMATE_SAFETY_FACTOR` (rounded up) so a
    tokenizer that counts more tokens than the average never pushes the
    request over the context window.

    :param input_chars: Number of characters in the prompt.
    :returns: Conservative estimated token count.
    """
    return math.ceil(input_chars / 4 * INPUT_TOKEN_ESTIMATE_SAFETY_FACTOR)


def resolve_output_token_limit(
    overrides: dict[str, Any],
    provider: str,
    role: str | None = None,
    input_chars: int | None = None,
    *,
    use_endpoint: bool = False,
) -> None:
    """Ensure a bounded max-output token param is set in *overrides*.

    HuggingFace-style providers apply a *total budget*: the reserved
    output window (``max_new_tokens``) is accounted against the model's
    context window alongside the input, and an unset value makes them
    reserve the entire remaining window (causing spurious usage limits
    and rate limiting).  This helper guarantees a finite, clamped value.

    Resolution precedence:

    1. An explicit provider token param (``max_tokens`` /
       ``max_new_tokens`` / ``num_predict``) already present in
       ``overrides`` (user/node/role value).
    2. The generic ``max_output_tokens`` key (provider-agnostic count).
    3. The built-in per-role fallback for *role*.

    The resolved value is clamped to ``min(value, catalog limit.output)``
    and, when the catalog exposes a context window and *input_chars* is
    given, to the remaining budget (``context - estimated input tokens``)
    so HuggingFace's total-budget check is never exceeded.

    The *context* source depends on *use_endpoint*: for endpoints with a
    known ``base_url`` (custom OpenAI-compatible endpoints, or a native
    provider endpoint resolved via :func:`resolve_langchain_endpoint`),
    ``max_model_len`` from the live ``/models`` probe is authoritative
    (models.dev's values are per-deployment and may under-report).  The
    normal (non-retry) path passes ``use_endpoint=False`` so it stays on
    the fast, offline models.dev value -- its purpose is only to produce a
    finite budget (mainly for the HuggingFace whole-window reservation),
    not an exact cap.  The retry path passes ``use_endpoint=True`` to
    clamp against the real server context, falling back to models.dev when
    the endpoint probe fails.  The ``output`` clamp always uses models.dev
    (the endpoint exposes no separate output cap).

    :param overrides: The merged ``configurable`` dict to update in place.
    :param provider: Klea provider id (``huggingface``, ``ollama``, ...).
    :param role: Model role (e.g. ``"chat"``), used for the built-in
        per-role fallback.
    :param input_chars: Character count of the prompt, to bound the output
        within the total budget.
    :param use_endpoint: When True, prefer the live endpoint's
        ``max_model_len`` as the context source, falling back to models.dev.
        When False (default), use models.dev only and never query the
        endpoint.
    """
    token_param = get_token_limit_param(provider)

    # Resolve the configured value, preferring an explicit provider-specific
    # token param over the generic provider-agnostic key.
    value: int | None = None
    for param in _TOKEN_PARAMS:
        if param in overrides:
            value = overrides[param]
            break
    if value is None:
        value = overrides.get("max_output_tokens")
    if value is None:
        value = _ROLE_MAX_OUTPUT_TOKENS.get(role or "", DEFAULT_MAX_OUTPUT_TOKENS)
    try:
        value = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        logger.warning(f"Invalid token limit {value!r}: {exc}, using fallback")
        value = _ROLE_MAX_OUTPUT_TOKENS.get(role or "", DEFAULT_MAX_OUTPUT_TOKENS)

    # Clamp to the model's output limit and total budget, if known.
    # models.dev's limits are per-deployment config and may under-report a
    # specific server's real context window, so on the retry path
    # (use_endpoint=True) we prefer the live endpoint's max_model_len and
    # fall back to models.dev.  The normal path (use_endpoint=False) uses
    # models.dev only -- no endpoint query -- since its purpose is just to
    # produce a finite budget (mainly the HuggingFace whole-window
    # reservation), not an exact cap.
    limits = None
    if use_endpoint and overrides.get("base_url"):
        limits = probe_endpoint_model_limits(
            provider,
            overrides.get("model", ""),
            overrides.get("base_url"),
            overrides.get("api_key"),
        )
    if limits is None:
        limits = get_catalog_model_limits(provider, overrides.get("model", ""))
    if limits and limits.output:
        value = min(value, limits.output)
    if limits and limits.context and input_chars is not None:
        headroom = limits.context - estimate_input_tokens(input_chars)
        if headroom > 0:
            value = min(value, headroom)

    # Remove the generic and any stale token params; set the provider one.
    overrides.pop("max_output_tokens", None)
    for param in _TOKEN_PARAMS:
        overrides.pop(param, None)
    overrides[token_param] = value
    logger.debug(
        f"Resolved {token_param = } for {provider = } {role = }: "
        f"{value = } (input_chars = {input_chars})"
    )


def check_model_works(model, timeout=30, retries=5):
    """Check if a model works since it is not tested when loaded"""
    assert timeout >= 0

    # Pick the right token-limit param for the provider so we keep the health
    # check cheap without triggering warnings about unknown kwargs.
    llm_type = getattr(model, "_llm_type", "")
    if "huggingface" in llm_type:
        provider = "huggingface"
    elif "ollama" in llm_type:
        provider = "ollama"
    else:
        provider = "openai"
    token_param = get_token_limit_param(provider)

    configurable = {token_param: 5}

    # One-shot probe for response_format/json_schema support.  A transient
    # failure here is harmless  ---  we just fall back to prompt-based structured
    # output (already in _get_system_prompt).  Every model will pass the plain
    # ping retry loop below even if this probe fails.
    try:
        from pydantic import BaseModel

        class _ProbeSchema(BaseModel):
            answer: str

        probe = model.with_structured_output(
            _ProbeSchema, method="json_schema", include_raw=False
        )
        probe.invoke(
            "ping",
            config={"timeout": timeout, "configurable": configurable},
        )
        model._supports_structured_output = True
        logger.info("Model supports structured output")
    except Exception:  # noqa: BLE001
        model._supports_structured_output = False

    for attempt in range(retries):
        logger.info(f"Checking model. Attempt #{attempt + 1}/{retries}")
        try:
            result = model.invoke(
                "ping",
                config={
                    "timeout": timeout,
                    "configurable": configurable,
                },
            )
            logger.info(f"Model available (attempt {attempt + 1}/{retries}): {result}")
            return True, f"Model available (attempt {attempt + 1}/{retries})"
        except StopIteration as e:
            return (
                False,
                f"{e.__class__.__name__}: check if any inference providers are available for the selected model",
            )
        except Exception as e:  # noqa: BLE001
            error_msg = f"{e.__class__.__name__}: {e.__str__()}"
            logger.warning(
                f"Attempt #{attempt + 1}/{retries}: model unavailable: {error_msg}"
            )
            if attempt < retries - 1:
                time.sleep(2**attempt)
            else:
                logger.error(f"Model unavailable after {retries} attempts: {error_msg}")
                return (
                    False,
                    f"Model unavailable after {retries} attempts: {error_msg}",
                )

    return False, "Unknown error"


def setup_embedding(model_name_full, logger):
    # Lazy: init_embeddings and HuggingFaceEndpointEmbeddings pull in
    # langchain provider packages that may not be installed
    from langchain.embeddings import init_embeddings

    parsed = parse_model_name(model_name_full)

    if parsed.provider == "custom":
        logger.debug(
            "Using openai-compatible embedding model %s at endpoint %s",
            parsed.model_name,
            parsed.suffix,
        )
        model_var = init_embeddings(
            parsed.model_name,
            provider="openai",
            base_url=parsed.suffix,
        )
    elif parsed.provider == "huggingface":
        # "local" suffix -> HuggingFaceEmbeddings (local sentence-transformers).
        # Any other suffix -> HuggingFaceEndpointEmbeddings (HF Inference API).
        if parsed.suffix == "local":
            logger.debug(
                "Using huggingface local embedding model: %s",
                parsed.model_name,
            )
            from langchain_huggingface import HuggingFaceEmbeddings

            model_var = HuggingFaceEmbeddings(model_name=parsed.model_name)
        else:
            logger.debug(
                "Using huggingface endpoint embedding model: %s (provider: %s)",
                parsed.model_name,
                parsed.suffix or "auto",
            )
            from langchain_huggingface import HuggingFaceEndpointEmbeddings

            model_var = HuggingFaceEndpointEmbeddings(
                model=parsed.model_name,
                provider=parsed.suffix or "auto",
                task="feature-extraction",
            )
    else:
        logger.debug("Using embedding model: %s", model_name_full)
        if parsed.provider == "ollama":
            check_ollama_model(logger, parsed.model_name)
        model_var = init_embeddings(model_name_full)

    assert model_var
    logger.info(f"Using embedding model: {model_name_full}")
    return model_var


#: Tolerant patterns for "the prompt/context does not fit" errors.  Based on
#: the error strings seen from OpenAI-compatible, HuggingFace, and vLLM
#: providers (and informed by opencode's ``provider-error.ts``).  Patterns
#: are matched case-insensitively over the whole exception message.
_CONTEXT_OVERFLOW_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"context[ _-]*length[ _-]*exceed", re.IGNORECASE),
    re.compile(r"context[ _-]*window[ _-]*exceed", re.IGNORECASE),
    re.compile(r"context_length_exceeded", re.IGNORECASE),
    re.compile(r"prompt is too long", re.IGNORECASE),
    re.compile(r"input is too long", re.IGNORECASE),
    re.compile(r"too many tokens", re.IGNORECASE),
    re.compile(r"token limit exceeded", re.IGNORECASE),
    re.compile(r"exceed[s]? the limit of \d+", re.IGNORECASE),
    re.compile(r"maximum context length", re.IGNORECASE),
    re.compile(r"maximum length is \d+", re.IGNORECASE),
    re.compile(r"must be <= \d+ tokens", re.IGNORECASE),
    re.compile(r"requested token count", re.IGNORECASE),
]

#: Tolerant patterns for "the model stopped because it hit the output
#: token cap" errors.  Providers report this either as a returned message
#: with ``finish_reason == "length"`` (handled by :func:`is_output_truncated`)
#: or as a raised exception (e.g. the OpenAI SDK's
#: ``LengthFinishReasonError`` on the streaming/structured-output path).
_LENGTH_TRUNCATION_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"length limit was reached", re.IGNORECASE),
    re.compile(r"finish[_ -]?reason[^\n]*length", re.IGNORECASE),
    re.compile(r"length[^\n]*finish[_ -]?reason", re.IGNORECASE),
    re.compile(r"output truncated", re.IGNORECASE),
]

#: Tolerant patterns for rate-limit / quota errors.
_RATE_LIMIT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b429\b", re.IGNORECASE),
    re.compile(r"rate[ _-]*limit", re.IGNORECASE),
    re.compile(r"rate_limit_exceeded", re.IGNORECASE),
    re.compile(r"too many requests", re.IGNORECASE),
    re.compile(r"quota", re.IGNORECASE),
    re.compile(r"usage limit", re.IGNORECASE),
]

#: Tolerant patterns for authentication / authorisation failures.
_AUTH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b401\b", re.IGNORECASE),
    re.compile(r"\b403\b", re.IGNORECASE),
    re.compile(r"unauthori[sz]ed", re.IGNORECASE),
    re.compile(r"invalid api key", re.IGNORECASE),
    re.compile(r"incorrect api key", re.IGNORECASE),
    re.compile(r"api key.*must be set", re.IGNORECASE),
    re.compile(r"authentication", re.IGNORECASE),
    re.compile(r"authorization", re.IGNORECASE),
    re.compile(r"forbidden", re.IGNORECASE),
]

#: Tolerant patterns for "model does not exist" errors.
_MODEL_NOT_FOUND_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b404\b", re.IGNORECASE),
    re.compile(r"model[ _-]*not[ _-]*found", re.IGNORECASE),
    re.compile(r"does not exist", re.IGNORECASE),
    re.compile(r"model.*not found", re.IGNORECASE),
    re.compile(r"not found", re.IGNORECASE),
    re.compile(r"a valid model was not found", re.IGNORECASE),
]

#: Tolerant patterns for timeout errors.
_TIMEOUT_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"timeout", re.IGNORECASE),
    re.compile(r"timed out", re.IGNORECASE),
]

#: Tolerant patterns for providers rejecting the structured-output
#: ``response_format`` / ``json_schema`` parameter.  Fall back to
#: prompt-based structured output when these match.
_STRUCTURED_OUTPUT_REJECTED_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"response_format", re.IGNORECASE),
    re.compile(r"json_schema", re.IGNORECASE),
    re.compile(r"structured output", re.IGNORECASE),
    re.compile(r"\b400\b[^\n]*invalid_request_error", re.IGNORECASE),
]


def _flatten_exception_messages(exc: BaseException) -> list[str]:
    """Collect the message from an exception and its cause chain.

    LangChain and httpx wrap underlying provider errors, so a bare
    ``str(exc)`` may miss the informative inner message.  This walks the
    ``__cause__`` / ``__context__`` chain and returns all messages.

    :param exc: The exception to flatten.
    :returns: A list of message strings, outermost first.
    """
    messages: list[str] = []
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        messages.append(str(current))
        current = current.__cause__ or current.__context__
    return messages


def _matches_any(patterns: list[re.Pattern[str]], text: str) -> bool:
    """Return True if any pattern matches anywhere in *text*."""
    return any(p.search(text) for p in patterns)


def classify_llm_invocation_error(exc: BaseException) -> LLMInvocationErrorCategory:
    """Classify an LLM invocation exception into a category.

    Providers report failures inconsistently, so this uses tolerant regex
    matching over the exception message and its cause chain.  Categories
    are checked in order of specificity (context overflow first), so a
    message matching several heuristics lands in the most actionable
    bucket.

    :param exc: The exception raised by an LLM invocation.
    :returns: The best-effort :class:`klea_utils.errors.LLMInvocationErrorCategory`.
    """
    text = "\n".join(_flatten_exception_messages(exc))

    if _matches_any(_CONTEXT_OVERFLOW_PATTERNS, text):
        return LLMInvocationErrorCategory.CONTEXT_OVERFLOW
    if _matches_any(_LENGTH_TRUNCATION_PATTERNS, text):
        return LLMInvocationErrorCategory.LENGTH_TRUNCATION
    if _matches_any(_RATE_LIMIT_PATTERNS, text):
        return LLMInvocationErrorCategory.RATE_LIMITED
    if _matches_any(_AUTH_PATTERNS, text):
        return LLMInvocationErrorCategory.AUTH_FAILED
    if _matches_any(_MODEL_NOT_FOUND_PATTERNS, text):
        return LLMInvocationErrorCategory.MODEL_NOT_FOUND
    if _matches_any(_TIMEOUT_PATTERNS, text):
        return LLMInvocationErrorCategory.TIMEOUT
    if _matches_any(_STRUCTURED_OUTPUT_REJECTED_PATTERNS, text):
        return LLMInvocationErrorCategory.STRUCTURED_OUTPUT_REJECTED
    return LLMInvocationErrorCategory.UNKNOWN


# Extra fields accepted by provider model constructors that are not part
# of the Pydantic model class (e.g. kwargs passed to a factory method).
_PROVIDER_EXTRA_FIELDS: dict[str, set[str]] = {
    # ChatHuggingFace.from_model_id() accepts backend, provider, etc.
    # which flow through to HuggingFaceEndpoint but are not fields on
    # ChatHuggingFace itself.
    "huggingface": {"backend", "provider"},
}


def get_provider_allowed_fields(provider: str) -> set[str]:
    """Return the set of init-param names accepted by a given provider's model class.

    Uses LangChain's internal provider registry to look up the Pydantic
    model class and introspect its fields (including aliases so that
    both ``api_key`` and ``openai_api_key`` pass through).

    Falls back to an empty set if the provider is not registered in
    LangChain's built-in providers.  Raises ``ImportError`` if the
    provider's integration package is not installed  ---  callers should
    handle this at configuration time, not silently fall through.

    The caller should always include ``{"model", "model_provider"}`` on
    top of the returned set since those are consumed by
    ``_ConfigurableModel`` before reaching the model constructor.
    """
    from langchain.chat_models.base import _get_chat_model_creator

    try:
        creator = _get_chat_model_creator(provider)
    except ValueError:
        # Provider not in LangChain's built-in registry  ---  not an error,
        # the caller will include model/model_provider which is sufficient.
        return set()

    cls = getattr(creator, "keywords", {}).get("cls")
    if cls is None:
        return set()

    fields: set[str] = set()
    for name, field in cls.model_fields.items():
        fields.add(name)
        if field.alias:
            fields.add(field.alias)
    return fields | _PROVIDER_EXTRA_FIELDS.get(provider, set())


def create_configurable_model(logger: logging.Logger):
    """Set up a configurable chat model.

    Creates a ``_ConfigurableModel`` with no default model.  Model,
    provider, and all other parameters (``base_url``, ``api_key``,
    ``temperature``, etc.) are specified per-invoke via the
    ``config["configurable"]`` dict passed to ``ainvoke()``.

    This enables runtime model switching  ---  each ``ainvoke()`` call
    creates a fresh underlying model instance for the given provider,
    so there is no stale configuration leakage between calls.

    The lookup function ``check_model_works`` is deliberately **not**
    called here  ---  we prefer a "leap before you look" approach so that
    startup is fast and model availability is checked only at query time.
    """
    from langchain.chat_models import init_chat_model

    model_var = init_chat_model(
        model=None,
        configurable_fields="any",
    )
    logger.info("Configurable chat model created (provider/model set per invoke)")

    return model_var


# ---------------------------------------------------------------------------
# Provider request headers
# ---------------------------------------------------------------------------
# Klea identifies itself to OpenAI-compatible inference endpoints with its own
# User-Agent, and sends a stable per-conversation session header to
# opencode-hosted endpoints so the backend can optimise routing and prompt
# caching.  Provider specifics live behind one hook so call sites stay clean.

#: Host suffix for opencode-hosted OpenAI-compatible endpoints.  These accept
#: an ``x-opencode-session`` header carrying the conversation/session id.
_OPENCODE_HOST_SUFFIX = "opencode.ai"


def resolve_user_agent(prefix: str) -> str:
    """Return ``<prefix>/<version>`` for the running application.

    Mirrors ``web_fetch._honest_user_agent``: the version is read from the
    installed distribution named *prefix* (``importlib.metadata`` normalises
    separators, so the graph name doubles as the distribution name --
    ``klea-agent`` -> ``klea_agent``) and falls back to ``dev`` when metadata
    is unavailable (e.g. an uninstalled checkout).

    :param prefix: Application name, e.g. ``"klea-agent"``.
    :returns: The User-Agent string, e.g. ``"klea-agent/0.0.1"``.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        version_str = version(prefix)
    except PackageNotFoundError:
        version_str = ""
    return f"{prefix}/{version_str or 'dev'}"


def _is_opencode_endpoint(base_url: str | None) -> bool:
    """Return ``True`` when *base_url* points at an opencode-hosted endpoint."""
    if not base_url:
        return False
    from urllib.parse import urlparse

    host = (urlparse(base_url).hostname or "").lower()
    return host == _OPENCODE_HOST_SUFFIX or host.endswith("." + _OPENCODE_HOST_SUFFIX)


def _default_request_headers(
    base_url: str | None, session_id: str | None, user_agent: str
) -> dict[str, str]:
    """Default request headers for chat providers (OpenAI, Anthropic).

    Identifies Klea with a User-Agent and, for opencode-hosted endpoints,
    sends the per-conversation ``x-opencode-session`` header so the backend can
    optimise routing and prompt caching.
    """
    headers: dict[str, str] = {}
    if user_agent:
        headers["User-Agent"] = user_agent
    if session_id and _is_opencode_endpoint(base_url):
        headers["x-opencode-session"] = session_id
    return headers


#: Per-provider request-header builders.  ``custom`` endpoints resolve to the
#: ``openai`` or ``anthropic`` provider in ``build_config`` (see
#: :func:`resolve_custom_endpoint`) and both share the default builder.  Add a
#: provider here (rather than branching in ``build_config``) if another
#: integration needs its own headers.
_PROVIDER_HEADER_BUILDERS: dict[str, Callable[..., dict[str, str]]] = {
    "openai": _default_request_headers,
    "anthropic": _default_request_headers,
}


def apply_provider_overrides(
    overrides: dict[str, Any],
    *,
    session_id: str | None = None,
    user_agent: str = "",
) -> None:
    """Add provider-specific request headers to a configurable dict (in place).

    Called once at the end of :meth:`LLMModel.build_config`.  Only providers
    with a registered builder are affected; any ``default_headers`` already in
    *overrides* win over ours.

    :param overrides: The merged ``config["configurable"]`` dict.
    :param session_id: Per-conversation session id (LangGraph ``thread_id``).
    :param user_agent: Klea User-Agent (see :func:`resolve_user_agent`).
    """
    builder = _PROVIDER_HEADER_BUILDERS.get(overrides.get("model_provider") or "openai")
    if builder is None:
        return
    # Anthropic stores its endpoint under ``anthropic_api_url``; every other
    # provider uses ``base_url``.
    endpoint = overrides.get("base_url") or overrides.get("anthropic_api_url")
    headers = builder(endpoint, session_id, user_agent)
    if not headers:
        return
    existing = overrides.get("default_headers") or {}
    overrides["default_headers"] = {**headers, **existing}


#: Base URL attribute names on the concrete chat model classes returned by
#: ``_ConfigurableModel._model()``.  Each provider exposes its resolved
#: endpoint under a different attribute (e.g. ``ChatOpenAI.openai_api_base``,
#: ``ChatMistralAI.endpoint``, ``ChatAnthropic.anthropic_api_url``), so we
#: cycle through the list and take the first one set rather than keeping a
#: per-provider map.  ``ChatDeepSeek`` redefines the inherited field as
#: ``api_base``, so its ``openai_api_base`` stays ``None`` and the cycle
#: picks up ``api_base``.
_LANGCHAIN_BASE_URL_ATTRS = (
    "openai_api_base",
    "api_base",
    "endpoint",
    "anthropic_api_url",
)


def resolve_langchain_endpoint(instance: Any, config: RunnableConfig) -> str | None:
    """Resolve the base URL/endpoint of a configurable chat model.

    ``create_configurable_model`` returns a generic ``_ConfigurableModel``
    whose concrete provider instance is only built at invoke time (via its
    private ``_model(config)`` method, which is already called on every
    ``ainvoke``).  Native providers (``mistral:``, ``anthropic:``,
    ``deepseek:``, ...) do not carry a ``base_url`` in the configurable
    dict -- the provider resolves its own default endpoint internally --
    so to probe such an endpoint we materialise the concrete instance and
    read its resolved endpoint attribute.

    Cheap by construction: ``_model(config)`` only parses the model string
    and constructs the provider object (module import is cached per
    provider); no network or API calls happen at construction.

    :param instance: The configurable model (``_ConfigurableModel``) whose
        concrete instance to materialise.
    :param config: Per-invoke RunnableConfig carrying the merged
        ``configurable`` dict.
    :returns: The resolved base URL, or ``None`` when the concrete model
        exposes no known endpoint attribute (e.g. plain ``openai:`` models,
        whose default URL lives inside the OpenAI SDK client, not on the
        model object).
    """
    try:
        model = instance._model(config)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"Failed to materialise concrete model for endpoint: {e}")
        return None
    for attr in _LANGCHAIN_BASE_URL_ATTRS:
        value = getattr(model, attr, None)
        if isinstance(value, str) and value:
            logger.debug(f"Resolved endpoint for model via {attr}: {value}")
            return value
    logger.debug("No base URL attribute found on concrete model instance")
    return None


class LLMModel(BaseModel):
    """Container for a single LLM model instance and its runtime configuration.

    ``instance`` holds the model object (typically a ``_ConfigurableModel``
    returned by ``init_chat_model``).  ``role_defaults`` stores role-wide
    default parameters (e.g. ``max_tokens``, ``temperature``) that apply to
    every node sharing this role, unless overridden by node or user config.

    ``build_config()`` performs a five-layer merge:

    **Layer 0**  ---  ``role_defaults``: role-wide parameters (e.g.
    ``{"max_tokens": 4096}``).

    **Layer 1**  ---  ``model_name``: the default model identifier from
    the graph config.

    **Layer 2**  ---  ``context_overrides``: per-request fields from the API
    (``model``, ``api_key``, etc.).  Only applied when ``modifiable=True``,
    and skipping any keys frozen by node defaults.

    **Layer 3**  ---  ``node_defaults``: frozen per-node defaults (always win).

    **Layer 4**  ---  ``provider_defaults``: per-provider defaults from the
    graph config (e.g. HuggingFace role budgets), applied *after* the model
    string is parsed so the resolved provider is known.  Applied with
    ``setdefault`` so explicit role/context/node values always win.

    ``modifiable`` controls whether the model can be changed at runtime
    (both the API and web UI reject modifications to locked roles).
    Set to ``False`` to lock a role (e.g. guard) against user overrides
    in managed deployments.

    ``required`` marks roles that need a default model for the app to
    function (e.g. ``chat``).  At startup, required roles with an empty
    model trigger a warning (not a failure) listing the environment
    variables to set.  Optional roles (e.g. ``guard``) are skipped when
    their model is empty.
    """

    model_name: str = ""
    instance: Any
    role_defaults: dict[str, Any] = {}
    provider_defaults: dict[str, dict[str, Any]] = {}
    modifiable: bool = True
    required: bool = True
    #: Klea User-Agent sent to OpenAI-compatible endpoints (``klea-agent/<v>``).
    user_agent: str = ""

    def build_config(
        self,
        context_overrides: dict[str, Any] | None = None,
        node_defaults: dict[str, Any] | None = None,
        session_id: str | None = None,
    ) -> RunnableConfig:
        """Merge up to five layers of model configuration into a ``RunnableConfig``.

        Layer order (lowest -> highest priority):

        0. ``self.role_defaults``   ---   role-wide parameters
        1. ``self.model_name``      ---   role model from graph config
        2. ``context_overrides``    ---   per-request user overrides
        3. ``node_defaults``        ---   frozen per-node defaults
        4. ``self.provider_defaults`` ---  per-provider defaults (``setdefault``)

        :param context_overrides: Per-request fields from the API
            (e.g. ``model``, ``api_key``).  Only applied when
            ``self.modifiable is True``, and skipping any keys
            present in ``node_defaults``.
        :param node_defaults: Frozen per-node defaults (e.g.
            ``{"temperature": 0.3}``).  Always win.
        :param session_id: Per-conversation session id (LangGraph ``thread_id``)
            used for the opencode ``x-opencode-session`` header.
        :returns: A ``RunnableConfig`` with the ``configurable`` key
            populated.
        """
        logger.debug(
            f"{self.modifiable = }\n"
            f"{self.role_defaults = }\n"
            f"{self.model_name = }\n"
            f"{mask_sensitive(context_overrides or {}) = }\n"
            f"{node_defaults = }"
        )

        # Layer 0: role-wide defaults from graph config
        overrides: dict[str, Any] = dict(self.role_defaults)
        logger.debug(f"Layer 0 (role defaults):\n{mask_sensitive(overrides) = }")

        # Layer 1: role model identifier
        overrides["model"] = self.model_name
        logger.debug(f"Layer 1 (model):\n{mask_sensitive(overrides) = }")

        # Layer 2: context overrides (only if modifiable).
        # Skip any keys the node has frozen in model_defaults.
        if self.modifiable and context_overrides:
            for k, v in context_overrides.items():
                if node_defaults and k in node_defaults:
                    logger.debug(
                        f"Skipping context override '{k}' (frozen by node defaults)"
                    )
                    continue
                overrides[k] = v
            logger.debug(f"Layer 2 (context):\n{mask_sensitive(overrides) = }")

        # Layer 3: node defaults  ---  always win
        if node_defaults:
            overrides.update(node_defaults)
            logger.debug(f"Layer 3 (node defaults):\n{mask_sensitive(overrides) = }")

        # Parse the final model string into LangChain-compatible components.
        # Klea stores full provider-prefixed model strings internally (e.g.
        # "custom:gpt-4o:https://endpoint/v1"), but the _ConfigurableModel
        # expects the bare model name plus separate model_provider and base_url.
        # parse_model_name is defined in this module  ---  no lazy import needed.
        parsed = parse_model_name(overrides["model"])
        overrides["model"] = parsed.model_name
        if parsed.provider and parsed.provider == "custom":
            # The suffix may be a bare base URL (default: OpenAI Chat
            # Completions) or a full endpoint URL whose surface we detect
            # (chat completions / responses / messages).  See
            # resolve_custom_endpoint.
            if parsed.suffix:
                custom_endpoint: CustomEndpoint = resolve_custom_endpoint(parsed.suffix)
                overrides["model_provider"] = custom_endpoint.model_provider
                if custom_endpoint.model_provider == "anthropic":
                    # Anthropic's LangChain field is anthropic_api_url, and its
                    # SDK re-appends /v1/messages to the stripped base URL.
                    overrides["anthropic_api_url"] = custom_endpoint.base_url
                else:
                    overrides["base_url"] = custom_endpoint.base_url
                if custom_endpoint.use_responses_api is not None:
                    overrides["use_responses_api"] = custom_endpoint.use_responses_api
            else:
                overrides["model_provider"] = "openai"
        # for certain providers, we do not want to use the models dev
        # catalogue, since langchain handles them natively
        elif parsed.provider:
            overrides["model_provider"] = parsed.provider
            # explicit end point url
            if parsed.suffix:
                overrides["base_url"] = parsed.suffix
            else:
                if parsed.provider not in MODELS_DEV_PROVIDERS_IGNORED:
                    # No explicit endpoint (``provider:model``): resolve it from
                    # the models.dev catalog when the provider is an OpenAI- or
                    # Anthropic-compatible endpoint, so users need not write a
                    # ``custom:`` URL.  Native SDK providers (no catalog ``api``)
                    # are left to LangChain, which knows their default endpoint.
                    endpoint = resolve_catalog_provider_endpoint(
                        parsed.provider, parsed.model_name
                    )
                    if endpoint is not None:
                        overrides["model_provider"] = endpoint.model_provider
                        if endpoint.model_provider == "anthropic":
                            overrides["anthropic_api_url"] = endpoint.base_url
                        else:
                            overrides["base_url"] = endpoint.base_url
                        if endpoint.use_responses_api is not None:
                            overrides["use_responses_api"] = endpoint.use_responses_api
        logger.debug(f"After model string parse:\n{mask_sensitive(overrides) = }")

        # A custom or catalog-resolved Anthropic endpoint still authenticates
        # with the generic key users set as OPENAI_API_KEY (ChatAnthropic
        # itself reads ANTHROPIC_API_KEY).  Copy it across so one key works
        # for every non-native custom surface; an explicit api_key override
        # wins.  The native ``anthropic:`` provider keeps ANTHROPIC_API_KEY.
        if (
            parsed.provider != "anthropic"
            and overrides.get("model_provider") == "anthropic"
        ):
            require_extra("langchain_anthropic", "anthropic")
            custom_key = overrides.get("api_key") or os.environ.get("OPENAI_API_KEY")
            if custom_key:
                overrides.setdefault("anthropic_api_key", custom_key)
            logger.debug(
                f"After anthropic key mapping:\n{mask_sensitive(overrides) = }"
            )

        # Inject HuggingFace from_model_id kwargs derived from the model
        # string suffix.  These are not fields on ChatHuggingFace itself
        # (they flow through to HuggingFaceEndpoint) so they'd be filtered
        # out later  ---  we set them here so they survive provider filtering.
        if overrides.get("model_provider") == "huggingface" and parsed.suffix:
            if parsed.suffix == "local":
                overrides.setdefault("backend", "pipeline")
            else:
                overrides.setdefault("backend", "endpoint")
                overrides.setdefault("provider", parsed.suffix)
            logger.debug(
                f"HuggingFace kwargs injected:\n{mask_sensitive(overrides) = }"
            )

        # Layer 4: per-provider defaults from graph config.  Only fills in
        # keys not already set by role/context/node layers (setdefault), so
        # explicit values always win.  The provider is resolved by now.
        provider = overrides.get("model_provider") or "openai"
        provider_defaults = self.provider_defaults.get(provider)
        if provider_defaults:
            for k, v in provider_defaults.items():
                overrides.setdefault(k, v)
            logger.debug(
                f"Layer 4 (provider defaults):\n{mask_sensitive(overrides) = }"
            )

        # Map generic "api_key" to provider-specific token field names so a
        # single user-facing field works across all providers.
        if "api_key" in overrides:
            overrides.setdefault("huggingfacehub_api_token", overrides["api_key"])
            logger.debug(f"After api_key mapping:\n{mask_sensitive(overrides) = }")

        # Provider-specific request headers (Klea User-Agent; opencode session
        # header on opencode-hosted endpoints).
        apply_provider_overrides(
            overrides, session_id=session_id, user_agent=self.user_agent
        )

        # Wrap in the "configurable" key expected by _ConfigurableModel.
        return cast(RunnableConfig, {"configurable": overrides})


def get_last_n_conversations(
    all_messages, start: int = 0, stop: int | None = None
) -> tuple[str, list[BaseMessage]]:
    """Get recent conversations between start and stop indices.

    Returns the conversation as a single text block (used as prompt/summary
    input) along with the ordered ``BaseMessage`` objects, preserving the
    interleaved user/assistant order of the original history.

    :param all_messages: all the messages
    :param start: start index
    :param stop: stop index
    :returns: (conversation, ordered list of human/ai messages)

    """
    logger.debug(f"{start = }; {stop = }")
    conv_messages: list[BaseMessage] = [
        msg
        for msg in all_messages[start:stop]
        if isinstance(msg, (HumanMessage, AIMessage))
    ]
    conversation = ""
    for msg in conv_messages:
        if isinstance(msg, HumanMessage):
            conversation += f"{msg.pretty_repr()}"
        else:
            conversation += f": {msg.pretty_repr()}"

    logger.debug(f"{conversation = }")

    return (conversation.replace("{", "{{").replace("}", "}}"), conv_messages)


def get_recent_messages(
    messages, max_chars: int, keep_at_least: int = 1
) -> list[BaseMessage]:
    """Return the most recent human/ai messages bounded by *max_chars*.

    Walks backwards through *messages* (in their original interleaved order)
    accumulating ``pretty_repr()`` length until *max_chars* would be
    exceeded.  The first *keep_at_least* messages are always included, so
    the latest exchange is never dropped even when it alone exceeds the
    budget.

    :param messages: All conversation messages.
    :param max_chars: Maximum total characters of the returned window.
    :param keep_at_least: Minimum number of messages to always include.
    :returns: Ordered list of recent human/ai messages.
    """
    recent: list[BaseMessage] = []
    total = 0
    for msg in reversed(messages):
        if not isinstance(msg, (HumanMessage, AIMessage)):
            continue
        length = len(msg.pretty_repr())
        if len(recent) >= keep_at_least and total + length > max_chars:
            break
        recent.append(msg)
        total += length
    recent.reverse()
    logger.debug(f"{len(recent) = } recent messages, {total} chars")
    return recent


def add_memory_to_prompt(context_summary: str) -> str:
    """Add the context summary to the system prompt.

    Returns a text block framing the previous-context summary.  Recent
    conversation messages are no longer flattened into this block: they are
    injected as real message objects by the node's prompt assembly (see
    ``klea_utils.nodes.base``).

    :param context_summary: Summary of the past conversation.
    :returns: Prompt text block, or ``""`` when there is no summary.
    """
    ret_string = ""

    directive = dedent("""

        ## Previous context

        IMPORTANT:

        - Consider both the latest user message AND the conversation history.

    """)

    if len(context_summary):
        ret_string += dedent(f"""

        ### Context summary

        Here is a concise summary of the past conversation to maintain continuity:

        {context_summary}

        """)

    if len(ret_string):
        ret_string += directive

    return ret_string


@lru_cache(maxsize=10000)
def load_prompt(prompt_name: str, prompt_registry_location: str):
    """Load a prompt from file called prompt_name.md

    :param str: prompt file name
    :param prompt_registry_location: location of prompts folder/registry
    :returns: loaded prompt text

    """
    base = Path(prompt_registry_location).resolve()
    prompt_path = (base / f"{prompt_name}.md").resolve()
    # Prevent path traversal (e.g. prompt_name="../../etc/passwd")
    try:
        prompt_path.relative_to(base)
    except ValueError:
        raise FileNotFoundError(
            f"Invalid prompt name {prompt_name!r}: outside registry"
        ) from None
    if not prompt_path.is_file():
        raise FileNotFoundError(f"{prompt_path} was not found")

    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()
