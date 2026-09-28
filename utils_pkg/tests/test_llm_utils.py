#!/usr/bin/env python3
"""
Test llm utils

File:

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import unittest

import pytest
from klea_utils.llm import (
    LLMModel,
    add_memory_to_prompt,
    estimate_input_tokens,
    format_alert,
    get_last_n_conversations,
    get_recent_messages,
    missing_required_roles,
    parse_model_name,
    resolve_langchain_endpoint,
    split_output_by_section,
)
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage


def test_format_alert_wraps_in_warning_blockquote():
    """format_alert emits a GitHub-style warning alert blockquote."""
    result = format_alert("something to verify")
    assert result == "> [!WARNING]\n> something to verify"


def test_format_alert_prefixes_multiline_body():
    """Multi-line alert bodies keep every line inside the blockquote."""
    result = format_alert("line one\nline two")
    assert result == "> [!WARNING]\n> line one\n> line two"


def test_format_alert_supports_level():
    """The alert level can be customised (e.g. note)."""
    result = format_alert("heads up", level="note")
    assert result == "> [!NOTE]\n> heads up"


@pytest.mark.parametrize(
    argnames=[
        "test_id",
        "text",
        "start_mark",
        "end_mark",
        "expected_delim",
        "expected_other",
    ],
    argvalues=[
        (
            "1",
            "some chat <thinking>some thought </thinking>",
            "<thinking>",
            "</thinking>",
            "some thought",
            "some chat",
        ),
        (
            "2",
            "<thinking>some thought </thinking>",
            "<thinking>",
            "</thinking>",
            "some thought",
            "",
        ),
        (
            "3",
            "some chat <thinking>some thought </thinking> some more chat",
            "<thinking>",
            "</thinking>",
            "some thought",
            "some chat  some more chat",
        ),
        (
            "4",
            "all thought no chat</thinking>",
            "<thinking>",
            "</thinking>",
            "all thought no chat",
            "\nNOTE: NO START MARKER FOUND",
        ),
        (
            "5",
            "<thinking>all thought no chat",
            "<thinking>",
            "</thinking>",
            "all thought no chat",
            "\nNOTE: NO END MARKER FOUND",
        ),
        (
            "6",
            "<nothinking>all chat",
            "<thinking>",
            "</thinking>",
            "",
            "<nothinking>all chat",
        ),
        (
            "7",
            "<thinking>some thought</thinking>",
            "<thinking>",
            "",
            "some thought</thinking>",
            "",
        ),
        (
            "8",
            "<thinking>some thought</thinking>some chat<thinking>more thought</thinking>",
            "<thinking>",
            "</thinking>",
            "some thoughtmore thought",
            "some chat",
        ),
        (
            "9",
            "<thinking>some thought</thinking>some chat<thinking>more thought</thinking> more chat",
            "<thinking>",
            "</thinking>",
            "some thoughtmore thought",
            "some chat more chat",
        ),
        (
            "10",
            "start chat <thinking>some thought</thinking>some chat<thinking>more thought</thinking>",
            "<thinking>",
            "</thinking>",
            "some thoughtmore thought",
            "start chat some chat",
        ),
        (
            "11",
            "start chat <thinking>some thought</thinking>some chat<thinking>more thought</thinking> end chat",
            "<thinking>",
            "</thinking>",
            "some thoughtmore thought",
            "start chat some chat end chat",
        ),
    ],
)
def test_split_output_by_section(
    test_id, text, start_mark, end_mark, expected_delim, expected_other
):
    delim, other = split_output_by_section(text, start_mark, end_mark)
    assert delim == expected_delim
    assert other == expected_other


@pytest.mark.parametrize(
    argnames=[
        "raw",
        "expected_provider",
        "expected_model",
        "expected_suffix",
    ],
    argvalues=[
        # provider:model:tag -> provider + model:tag
        ("ollama:bge-m3:latest", "ollama", "bge-m3:latest", None),
        ("ollama:qwen3:0.6b", "ollama", "qwen3:0.6b", None),
        # huggingface:org/model:suffix -> provider, model, suffix
        (
            "huggingface:intfloat/multilingual-e5-large:auto",
            "huggingface",
            "intfloat/multilingual-e5-large",
            "auto",
        ),
        # huggingface:org/model -> provider, model, no suffix
        ("huggingface:org/model", "huggingface", "org/model", None),
        # provider:model (2 parts) for any provider
        ("openai:gpt-4o", "openai", "gpt-4o", None),
        ("anthropic:claude-sonnet-4-5", "anthropic", "claude-sonnet-4-5", None),
        ("deepseek:deepseek-chat", "deepseek", "deepseek-chat", None),
        ("google_genai:gemini-2.0-flash", "google_genai", "gemini-2.0-flash", None),
        # bare model name, no provider
        ("bge-m3", None, "bge-m3", None),
    ],
)
def test_parse_model_name(raw, expected_provider, expected_model, expected_suffix):
    parsed = parse_model_name(raw)
    assert parsed.provider == expected_provider
    assert parsed.model_name == expected_model
    assert parsed.suffix == expected_suffix


def _history_messages():
    return [
        HumanMessage(content="q1"),
        AIMessage(content="a1"),
        HumanMessage(content="q2"),
        AIMessage(content="a2"),
    ]


def test_get_last_n_conversations_ordered_interleaved():
    """Returns the conversation text plus ordered message objects."""
    conversation, ordered = get_last_n_conversations(_history_messages(), 0, None)
    assert [m.content for m in ordered] == ["q1", "a1", "q2", "a2"]
    assert all(isinstance(m, (HumanMessage, AIMessage)) for m in ordered)
    assert "q1" in conversation and "a2" in conversation
    assert conversation.index("q1") < conversation.index("a2")


def test_get_last_n_conversations_filters_non_conversation():
    """System messages are not part of the conversation window."""
    messages = [SystemMessage(content="sys"), *_history_messages()]
    _, ordered = get_last_n_conversations(messages, 0, None)
    assert [m.content for m in ordered] == ["q1", "a1", "q2", "a2"]


def test_get_last_n_conversations_slices():
    """start/stop slice the history like a normal list."""
    _, ordered = get_last_n_conversations(_history_messages(), 1, 3)
    assert [m.content for m in ordered] == ["a1", "q2"]


def test_get_recent_messages_bounded_by_chars():
    """Only messages that fit the char budget are returned."""
    messages = _history_messages()
    recent = get_recent_messages(messages, max_chars=10_000)
    assert [m.content for m in recent] == ["q1", "a1", "q2", "a2"]

    tiny = get_recent_messages(messages, max_chars=5)
    assert [m.content for m in tiny] == ["a2"]


def test_get_recent_messages_keep_at_least():
    """keep_at_least overrides the budget for the newest messages."""
    messages = _history_messages()
    recent = get_recent_messages(messages, max_chars=5, keep_at_least=2)
    assert [m.content for m in recent] == ["q2", "a2"]


def test_get_recent_messages_skips_non_conversation():
    """System messages are ignored while walking the tail."""
    messages = [SystemMessage(content="sys"), *_history_messages()]
    recent = get_recent_messages(messages, max_chars=10_000)
    assert [m.content for m in recent] == ["q1", "a1", "q2", "a2"]


def test_add_memory_to_prompt_is_summary_only():
    """The recent-messages block is gone; only the summary is returned."""
    block = add_memory_to_prompt("remember-this")
    assert "remember-this" in block
    assert "Recent messages" not in block
    assert "## Previous context" in block
    assert "conversation history" in block


def test_add_memory_to_prompt_empty_summary():
    """No summary produces an empty block."""
    assert add_memory_to_prompt("") == ""


def test_estimate_input_tokens_is_conservative():
    """The estimate is inflated by the safety factor and rounded up."""
    # 9736 chars -> 2434 at 4 chars/token; with the 5% factor it must be
    # strictly higher than the raw estimate (real tokenizers can count one
    # more token, which previously pushed input+output over the context).
    assert estimate_input_tokens(9736) == 2556
    assert estimate_input_tokens(9736) > 9736 // 4


def test_estimate_input_tokens_rounds_up():
    """Rounding goes up so the margin is never undercut."""
    # ceil(1 * 1.05) = 2, not 1.
    assert estimate_input_tokens(4) == 2
    assert estimate_input_tokens(0) == 0


class _FakeConfigurableModel:
    """Fake ``_ConfigurableModel`` returning a fixed concrete instance."""

    def __init__(self, concrete):
        self._concrete = concrete

    def _model(self, config):
        return self._concrete


def _concrete(**attrs):
    """Build a concrete model instance exposing the given attributes."""
    return type("ConcreteModel", (), attrs)()


def test_resolve_langchain_endpoint_openai_api_base():
    """ChatOpenAI-style custom endpoints resolve via openai_api_base."""
    inst = _FakeConfigurableModel(_concrete(openai_api_base="https://custom/v1"))
    assert resolve_langchain_endpoint(inst, {}) == "https://custom/v1"


def test_resolve_langchain_endpoint_mistral_endpoint():
    """ChatMistralAI-style endpoints resolve via endpoint."""
    inst = _FakeConfigurableModel(_concrete(endpoint="https://api.mistral.ai/v1"))
    assert resolve_langchain_endpoint(inst, {}) == "https://api.mistral.ai/v1"


def test_resolve_langchain_endpoint_anthropic_api_url():
    """ChatAnthropic-style endpoints resolve via anthropic_api_url."""
    inst = _FakeConfigurableModel(
        _concrete(anthropic_api_url="https://api.anthropic.com")
    )
    assert resolve_langchain_endpoint(inst, {}) == "https://api.anthropic.com"


def test_resolve_langchain_endpoint_skips_none_then_finds_api_base():
    """The cycle skips None attrs; ChatDeepSeek picks up api_base."""
    inst = _FakeConfigurableModel(
        _concrete(openai_api_base=None, api_base="https://api.deepseek.com")
    )
    assert resolve_langchain_endpoint(inst, {}) == "https://api.deepseek.com"


def test_resolve_langchain_endpoint_ignores_non_string():
    """Non-string attrs (e.g. mocks) are skipped, not treated as an endpoint."""
    inst = _FakeConfigurableModel(_concrete(openai_api_base=object()))
    assert resolve_langchain_endpoint(inst, {}) is None


def test_resolve_langchain_endpoint_none_without_known_attr():
    """A concrete model exposing none of the endpoint attrs returns None."""
    inst = _FakeConfigurableModel(_concrete())
    assert resolve_langchain_endpoint(inst, {}) is None


def test_resolve_langchain_endpoint_model_error_returns_none():
    """A failing _model() materialisation returns None, not an exception."""

    class _Boom:
        def _model(self, config):
            raise RuntimeError("bad params")

    assert resolve_langchain_endpoint(_Boom(), {}) is None


def test_missing_required_roles_lists_unset_required():
    """Required roles with an empty model are reported, in order."""
    info = {
        "chat": {"model": "ollama:qwen3", "required": True},
        "plan": {"model": "", "required": True},
        "guard": {"model": "", "required": False},
    }
    assert missing_required_roles(info) == ["plan"]


def test_missing_required_roles_defaults_required_true():
    """A missing ``required`` flag is treated as required (LLMModel default)."""
    info = {"chat": {"model": ""}}
    assert missing_required_roles(info) == ["chat"]


def test_missing_required_roles_empty_when_all_set():
    """A fully configured set reports no missing roles."""
    info = {
        "chat": {"model": "ollama:qwen3", "required": True},
        "plan": {"model": "ollama:qwen3:0.6b", "required": True},
    }
    assert missing_required_roles(info) == []


def test_missing_required_roles_empty_info():
    """An empty model-info mapping reports no missing roles."""
    assert missing_required_roles({}) == []


def test_missing_required_roles_accepts_llm_model_objects():
    """The predicate also works on LLMModel objects (graph startup path)."""
    models = {
        "chat": LLMModel(instance=None, model_name="ollama:qwen3"),
        "plan": LLMModel(instance=None, model_name="", required=True),
        "guard": LLMModel(instance=None, model_name="", required=False),
    }
    assert missing_required_roles(models) == ["plan"]


if __name__ == "__main__":
    unittest.main()
