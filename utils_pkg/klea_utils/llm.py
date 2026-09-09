#!/usr/bin/env python3
"""
LLM related utils

File: klea_rag/llm.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
import math
import re
import sys
import time
from functools import lru_cache
from pathlib import Path
from textwrap import dedent
from typing import Any, NamedTuple, cast

from json_repair import repair_json
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.prompt_values import PromptValue
from langgraph.types import RunnableConfig
from pydantic import BaseModel

from .errors import LLMInvocationErrorCategory
from .models_catalog import get_catalog_model_limits, probe_endpoint_model_limits
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
            cleaned = repair_json(answer)
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
            cleaned = repair_json(answer)
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

    def build_config(
        self,
        context_overrides: dict[str, Any] | None = None,
        node_defaults: dict[str, Any] | None = None,
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
        if parsed.provider == "custom":
            overrides["model_provider"] = "openai"
        elif parsed.provider:
            overrides["model_provider"] = parsed.provider
        if parsed.suffix:
            overrides["base_url"] = parsed.suffix
        logger.debug(f"After model string parse:\n{mask_sensitive(overrides) = }")

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
