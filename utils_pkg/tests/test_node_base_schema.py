#!/usr/bin/env python3
"""
Tests for the structured-output schema prompt block.

File: tests/test_node_base_schema.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import os
from collections.abc import Mapping
from typing import Any, Literal, cast

import pytest
from klea_utils.graph.schemas import TokenUsage
from klea_utils.llm import LLMModel, create_configurable_model, is_output_empty
from klea_utils.nodes.base import BaseLLMNode, _is_empty_result, _schema_to_example
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langchain_core.prompt_values import ChatPromptValue
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field


class AnswerSchema(BaseModel):
    """A minimal structured-output schema for testing."""

    answer: str = ""
    references: list[str] = Field(default_factory=list)


class EvalSchema(BaseModel):
    """Schema exercising enum and numeric types."""

    confidence: float = 0.0
    next_step: Literal["continue", "rewrite_answer"] = "continue"
    summary: str = ""


class NestedSchema(BaseModel):
    """Schema exercising nested object/array types."""

    tool_calls: list[dict] = Field(default_factory=list)


class MemoryState(BaseModel):
    """Minimal state exposing the fields the memory hook reads."""

    messages: list = Field(default_factory=list)
    context_summary: str = ""


class DummyNode(BaseLLMNode[MemoryState, BaseModel]):
    """Concrete BaseLLMNode for testing the prompt block."""

    model_type = "chat"

    def _get_prompt_variables(self, state: BaseModel) -> dict:
        return {}

    def _update_state(self, result, state: BaseModel) -> dict:
        return {}

    def _get_default_error_result(self):
        return AnswerSchema(answer="fallback")


def _node(schema) -> DummyNode:
    return DummyNode(
        logger=logging.getLogger("test_node_base_schema"),
        label="Dummy",
        llm_models={"chat": None},
        output_schema=schema,
    )


def _render(block: str) -> str:
    """Simulate ChatPromptTemplate un-escaping the braces in the block."""
    tpl = ChatPromptTemplate([("system", block), ("human", "{query}")])
    return tpl.invoke({"query": "q"}).to_string()


def test_schema_to_example_string():
    assert _schema_to_example({"type": "string"}) == "text"


def test_schema_to_example_number_and_boolean():
    assert _schema_to_example({"type": "integer"}) == 0
    assert _schema_to_example({"type": "number"}) == 0
    assert _schema_to_example({"type": "boolean"}) is True


def test_schema_to_example_enum_uses_first_value():
    schema = {"type": "string", "enum": ["continue", "rewrite_answer"]}
    assert _schema_to_example(schema) == "continue"


def test_schema_to_example_array_and_object():
    assert _schema_to_example({"type": "array", "items": {"type": "string"}}) == [
        "text"
    ]
    nested = {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "count": {"type": "integer"},
        },
    }
    assert _schema_to_example(nested) == {"name": "text", "count": 0}


def test_schema_to_example_unknown_type_is_none():
    assert _schema_to_example({"type": "unknown"}) is None


def test_prompt_block_strips_title_and_description():
    """The prompt block drops top-level title/description metadata."""
    rendered = _render(_node(AnswerSchema)._format_output_schema_prompt())
    assert '"title"' not in rendered
    assert '"description"' not in rendered


def test_prompt_block_contains_directive_and_example():
    """The prompt block tells the model not to echo the schema and shows an example."""
    rendered = _render(_node(AnswerSchema)._format_output_schema_prompt())
    assert "Do not output the schema definition itself" in rendered
    assert '"answer": "text"' in rendered
    assert '"references": ["text"]' in rendered


def test_prompt_block_example_matches_schema():
    """The rendered example parses as JSON and uses the schema's keys."""
    rendered = _render(_node(NestedSchema)._format_output_schema_prompt())
    example_line = next(
        line.strip()
        for line in rendered.splitlines()
        if line.strip().startswith("{") and "[{}]" in line
    )
    example = json.loads(example_line)
    assert example == {"tool_calls": [{}]}


def test_system_prompt_puts_schema_before_memory(tmp_path):
    """The output-schema block is prepended before memory so the stable
    prefix (``load_prompt`` + schema) stays cacheable."""
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "DummyNode_system.md").write_text("Base system prompt.")
    node = _node(AnswerSchema)
    node.prompt_registry_location = prompts
    node.memory = True
    state = MemoryState(context_summary="remember-the-context")

    system = node._get_system_prompt(state)

    # With memory enabled the system prompt is a list of ``("system", text)``
    # plus any recent history messages; the text lives in the first element.
    assert isinstance(system, list)
    system_text = system[0][1]
    assert "remember-the-context" in system_text
    assert system_text.index("## Output schema (strict)") < system_text.index(
        "remember-the-context"
    )


def test_is_empty_result_structured_default():
    """An all-default structured instance is flagged as empty."""
    assert _is_empty_result(AnswerSchema(), AnswerSchema) is True


def test_is_empty_result_structured_populated():
    """A populated structured instance is not empty."""
    assert _is_empty_result(AnswerSchema(answer="some answer"), AnswerSchema) is False


def test_is_empty_result_non_structured_blank():
    """A blank AIMessage (no output schema) is flagged as empty."""
    assert _is_empty_result(AIMessage(content="")) is True
    assert _is_empty_result(AIMessage(content="   ")) is True


def test_is_empty_result_non_structured_populated():
    """A non-blank AIMessage is not empty."""
    assert _is_empty_result(AIMessage(content="some text")) is False


def test_is_output_empty_plain_message():
    """Blank/whitespace AIMessages are empty; populated ones are not."""
    assert is_output_empty(AIMessage(content="")) is True
    assert is_output_empty(AIMessage(content="   \n")) is True
    assert is_output_empty(AIMessage(content="text")) is False


def test_is_output_empty_structured_dict_inspects_raw():
    """Structured output emptiness is judged from the raw message."""
    blank = {"raw": AIMessage(content=""), "parsed": AnswerSchema()}
    populated = {
        "raw": AIMessage(content='{"answer": "a"}'),
        "parsed": AnswerSchema(answer="a"),
    }
    assert is_output_empty(blank) is True
    assert is_output_empty(populated) is False


def test_is_output_empty_structured_dict_without_raw():
    """With no raw message, a parsed result counts as non-empty."""
    assert is_output_empty({"parsed": AnswerSchema(answer="a")}) is False
    assert is_output_empty({"parsed": None}) is True


def test_process_output_warns_on_empty_result(caplog):
    """An all-default structured parse warns and uses the node fallback."""
    node = _node(AnswerSchema)
    output = {
        "parsed": AnswerSchema(),
        "parsing_error": None,
        "raw": AIMessage(content="{}"),
    }
    with caplog.at_level(logging.WARNING):
        result = node._process_output(output)
    assert result == AnswerSchema(answer="fallback")
    assert "Empty LLM output from Dummy" in caplog.text


def test_process_output_no_warning_on_populated_result(caplog):
    """A populated structured parse does not warn."""
    node = _node(AnswerSchema)
    output = {
        "parsed": AnswerSchema(answer="a real answer"),
        "parsing_error": None,
        "raw": AIMessage(content='{"answer": "a real answer"}'),
    }
    with caplog.at_level(logging.WARNING):
        result = node._process_output(output)
    assert result == AnswerSchema(answer="a real answer")
    assert "Empty LLM output" not in caplog.text


def test_process_output_blank_message_uses_default(caplog):
    """A blank structured message degrades to the typed default, no raise."""
    node = _node(AnswerSchema)
    with caplog.at_level(logging.WARNING):
        result = node._process_output(AIMessage(content=""))
    assert result == AnswerSchema(answer="fallback")
    assert "could not be parsed" in caplog.text


def test_process_output_parsing_error_blank_raw_uses_default(caplog):
    """A parsing_error with a blank raw message degrades to the default."""
    node = _node(AnswerSchema)
    output = {
        "parsed": None,
        "parsing_error": "Invalid json output",
        "raw": AIMessage(content=""),
    }
    with caplog.at_level(logging.WARNING):
        result = node._process_output(output)
    assert result == AnswerSchema(answer="fallback")
    assert "using fallback" in caplog.text
    assert "could not be parsed" in caplog.text


def test_process_output_parsing_error_malformed_raw_uses_default(caplog):
    """Unrecoverable non-JSON raw output also degrades to the default."""
    node = _node(AnswerSchema)
    output = {
        "parsed": None,
        "parsing_error": "Invalid json output",
        "raw": AIMessage(content="not json at all"),
    }
    with caplog.at_level(logging.WARNING):
        result = node._process_output(output)
    assert result == AnswerSchema(answer="fallback")


def test_llm_post_exec_stream_emits_usage_event():
    """AbstractLLMNode._post_exec_stream adds the LLM-specific usage event."""
    node = _node(AnswerSchema)
    node._token_usage = TokenUsage(input_tokens=10, output_tokens=5, total_tokens=15)
    events: list[dict] = []
    cast(Any, node).write_custom_stream = events.append

    node._post_exec_stream()

    event_types = [e["type"] for e in events]
    assert event_types == ["usage"]
    assert events[0]["data"]["details"]["input_tokens"] == 10


def _system_prompt_value():
    """Build a ChatPromptValue with a plain-string system message."""
    tpl = ChatPromptTemplate([("system", "Base system prompt."), ("human", "{query}")])
    return tpl.invoke({"query": "q"})


def test_add_cache_control_anthropic_tags_system_content_block():
    """cache_control lands inside the SystemMessage content block.

    langchain-anthropic serialises a plain-string SystemMessage as the bare
    ``system`` field and drops ``additional_kwargs``, so the breakpoint must
    be embedded in a structured text block wrapping the string.
    """
    node = _node(None)
    prompt = _system_prompt_value()

    result = node._add_cache_control(
        prompt, {"configurable": {"model_provider": "anthropic"}}
    )

    messages = result.to_messages()
    assert messages[0].type == "system"
    assert messages[0].content == [
        {
            "type": "text",
            "text": "Base system prompt.",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert "cache_control" not in messages[0].additional_kwargs


def test_add_cache_control_ignored_for_non_anthropic():
    """Non-Anthropic providers leave the string system message untouched."""
    node = _node(None)
    prompt = _system_prompt_value()

    result = node._add_cache_control(
        prompt, {"configurable": {"model_provider": "openai"}}
    )

    assert result is prompt
    messages = prompt.to_messages()
    assert messages[0].content == "Base system prompt."


def _anthropic_cache_details(
    meta: Mapping[str, Any] | None,
) -> dict[str, int]:
    """Extract Anthropic cache fields from a message's ``usage_metadata``.

    langchain-anthropic nests the raw Anthropic usage fields under
    ``input_token_details`` (``cache_read`` / ``cache_creation`` /
    ``ephemeral_5m_input_tokens``) and reports the pre-cache ``input_tokens``
    at the top level, so both must be read together.  When an
    ``ephemeral_5m_input_tokens`` count is present, langchain-anthropic zeroes
    the generic ``cache_creation`` to avoid double counting, so the ephemeral
    field is the source of truth for cache creation.
    """
    details: dict[str, Any] = {}
    if meta:
        details.update(meta.get("input_token_details") or {})
        details["input_tokens"] = meta.get("input_tokens", 0)
    # Normalise cache creation: prefer the specific ephemeral count.
    details["cache_creation"] = details.get(
        "ephemeral_5m_input_tokens", details.get("cache_creation", 0)
    )
    return cast(dict[str, int], details)  # type: ignore[misc]


@pytest.mark.localonly
async def test_anthropic_cache_control_real_call():
    """Real Anthropic call caches a >4096-token system prefix (skipped w/o key).

    Requires ``ANTHROPIC_API_KEY`` in the environment.  Makes a node invoke
    (exercising ``_add_cache_control``) with a long system prompt, calls it
    twice, and asserts the second call reads the cache created by the first
    via the Anthropic response usage fields.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY not set; cannot make a real Anthropic call")

    # A configurable model that materialises the Anthropic client per invoke.
    # The provider/model/max_tokens are all supplied in the per-invoke config;
    # ChatAnthropic picks up ANTHROPIC_API_KEY from the environment.
    inst = create_configurable_model(logging.getLogger("test_anthropic_cache"))

    node = DummyNode(
        logger=logging.getLogger("test_anthropic_cache"),
        label="anthropic-cache",
        llm_models={
            "chat": LLMModel(
                instance=inst, model_name="anthropic:claude-haiku-4-5-20251001"
            )
        },
        output_schema=None,
    )

    # System prefix must clear Claude Haiku 4.5's 4096-token cache minimum
    # for a breakpoint to actually create/read a cache.  Repeated plain ASCII
    # tokenises at roughly 3-4 chars/token, so pad well beyond 4096 to leave
    # unambiguous margin before spending API quota.
    block = (
        "The NeuroML community maintains a rich set of tools for describing and "
        "simulating neuronal models, with a focus on standardisation and "
        "interoperability across simulator backends. "
    )
    repetitions = 4096 * 8 // max(len(block), 1) + 1
    system_text = block * repetitions
    # Under a pessimistic ~4 chars/token this is still far above the 4096
    # minimum; the char count (not the lossy token estimate) is the guarantee.
    assert len(system_text) > 4096 * 4

    config = cast(
        Any,
        {
            "configurable": {
                "model": "claude-haiku-4-5-20251001",
                "model_provider": "anthropic",
                "max_tokens": 256,
            }
        },
    )
    prompt = ChatPromptValue(
        messages=[
            SystemMessage(content=system_text),
            HumanMessage(content="Repeat the first sentence."),
        ]
    )

    first = cast(AIMessage, await node._invoke_llm(inst, prompt, config))
    second = cast(AIMessage, await node._invoke_llm(inst, prompt, config))

    first_details = _anthropic_cache_details(first.usage_metadata)
    second_details = _anthropic_cache_details(second.usage_metadata)

    node.logger.info(
        "First call: %s\nSecond call: %s",
        first_details,
        second_details,
    )

    first_cache = first_details.get("cache_creation", 0) or first_details.get(
        "cache_read", 0
    )
    assert first_cache > 0, (
        "First call should create or read a cache entry for the system "
        f"prefix; usage={first_details}"
    )
    assert second_details.get("cache_read", 0) > 0, (
        f"Second call should read the cached prefix; usage={second_details}"
    )


def test_extract_usage_from_usage_metadata():
    """LangChain-normalised ``usage_metadata`` is read when present."""
    node = _node(AnswerSchema)
    usage = node._extract_usage(
        AIMessage(
            content="ok",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
            },
        )
    )
    assert isinstance(usage, TokenUsage)
    assert usage.input_tokens == 100
    assert usage.output_tokens == 20
    assert usage.total_tokens == 120


def test_extract_usage_falls_back_to_response_metadata():
    """Gateways that leave ``usage_metadata`` empty are still measured."""
    node = _node(AnswerSchema)
    usage = node._extract_usage(
        AIMessage(
            content="ok",
            response_metadata={
                "token_usage": {
                    "prompt_tokens": 3407,
                    "completion_tokens": 505,
                    "total_tokens": 3912,
                    "completion_tokens_details": {"reasoning_tokens": 441},
                }
            },
        )
    )
    assert isinstance(usage, TokenUsage)
    assert usage.input_tokens == 3407
    assert usage.output_tokens == 505
    assert usage.total_tokens == 3912


def test_extract_usage_none_without_metadata():
    """No usage in either shape yields ``None``."""
    node = _node(AnswerSchema)
    assert node._extract_usage(AIMessage(content="ok")) is None
