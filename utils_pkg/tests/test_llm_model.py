"""Tests for LLMModel.build_config() — the four-layer config merge.

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

import pytest
from klea_utils.llm import LLMModel

logging.basicConfig(level=logging.DEBUG)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def build(
    role_defaults=None,
    model_name="ollama:qwen3:0.6b",
    modifiable=True,
    context_overrides=None,
    node_defaults=None,
    provider_defaults=None,
):
    """Convenience wrapper: create an LLMModel and call build_config()."""
    model = LLMModel(
        model_name=model_name,
        instance=None,
        role_defaults=role_defaults or {},
        modifiable=modifiable,
        provider_defaults=provider_defaults or {},
    )
    return model.build_config(
        context_overrides=context_overrides,
        node_defaults=node_defaults,
    )


def configurable(config):
    """Extract the ``configurable`` dict from the returned RunnableConfig."""
    return config["configurable"]


# ---------------------------------------------------------------------------
# Layer 0: role_defaults
# ---------------------------------------------------------------------------


class TestRoleDefaults:
    """Layer 0 — role-wide defaults from graph config."""

    def test_empty(self):
        c = configurable(build())
        assert c["model"] == "qwen3:0.6b"
        assert c["model_provider"] == "ollama"

    def test_with_role_defaults(self):
        c = configurable(build(role_defaults={"max_tokens": 4096}))
        assert c["max_tokens"] == 4096
        assert c["model"] == "qwen3:0.6b"

    def test_role_defaults_do_not_override_model(self):
        c = configurable(build(role_defaults={"model": "should-be-ignored"}))
        # model comes from Layer 1 (model_name), not Layer 0
        assert c["model"] == "qwen3:0.6b"


# ---------------------------------------------------------------------------
# Layer 2: context_overrides + modifiable
# ---------------------------------------------------------------------------


class TestContextOverrides:
    """Layer 2 — per-request user overrides."""

    def test_overrides_role_defaults_when_modifiable(self):
        c = configurable(
            build(
                role_defaults={"temperature": 0.3},
                context_overrides={"temperature": 0.7},
            )
        )
        assert c["temperature"] == 0.7

    def test_ignored_when_not_modifiable(self):
        c = configurable(
            build(
                role_defaults={"temperature": 0.3},
                modifiable=False,
                context_overrides={"temperature": 0.7},
            )
        )
        # temperature stays with role_defaults because modifiable is False
        assert c["temperature"] == 0.3

    def test_model_provider_from_override(self):
        c = configurable(
            build(
                model_name="ollama:qwen3:0.6b",
                context_overrides={"model": "openai:gpt-4o"},
            )
        )
        assert c["model"] == "gpt-4o"
        assert c["model_provider"] == "openai"

    def test_api_key_from_override(self):
        c = configurable(
            build(
                context_overrides={"api_key": "sk-test"},
            )
        )
        assert c["api_key"] == "sk-test"
        # Should also map to huggingfacehub_api_token
        assert c.get("huggingfacehub_api_token") == "sk-test"


# ---------------------------------------------------------------------------
# Layer 3: node_defaults (frozen)
# ---------------------------------------------------------------------------


class TestNodeDefaults:
    """Layer 3 — frozen per-node defaults (always win)."""

    def test_node_defaults_applied(self):
        c = configurable(build(node_defaults={"temperature": 0.0}))
        assert c["temperature"] == 0.0

    def test_node_defaults_beat_role_defaults(self):
        c = configurable(
            build(
                role_defaults={"temperature": 0.3},
                node_defaults={"temperature": 0.0},
            )
        )
        assert c["temperature"] == 0.0

    def test_node_defaults_freeze_against_context(self):
        """Context override for a field in node_defaults must be skipped."""
        c = configurable(
            build(
                modifiable=True,
                context_overrides={
                    "temperature": 0.9,
                    "model": "openai:gpt-4o",
                },
                node_defaults={"temperature": 0.0},
            )
        )
        # temperature is frozen by node_defaults
        assert c["temperature"] == 0.0
        # model is not in node_defaults, so context wins
        assert c["model"] == "gpt-4o"

    def test_node_defaults_freeze_model(self):
        """Node pins a specific model; context override should be skipped."""
        c = configurable(
            build(
                model_name="ollama:qwen3:0.6b",
                context_overrides={"model": "openai:gpt-4o"},
                node_defaults={"model": "ollama:qwen3:0.6b"},
            )
        )
        assert c["model"] == "qwen3:0.6b"
        assert c["model_provider"] == "ollama"


# ---------------------------------------------------------------------------
# Model string parsing
# ---------------------------------------------------------------------------


class TestModelParsing:
    """Final model string is parsed into LangChain-compatible components."""

    def test_ollama_model_string(self):
        c = configurable(build(model_name="ollama:qwen3:0.6b"))
        assert c["model"] == "qwen3:0.6b"
        assert c["model_provider"] == "ollama"

    def test_custom_model_string(self):
        """custom: prefix should map to model_provider=openai + base_url."""
        c = configurable(build(model_name="custom:gpt-4o:https://my-endpoint/v1"))
        assert c["model"] == "gpt-4o"
        assert c["model_provider"] == "openai"
        assert c["base_url"] == "https://my-endpoint/v1"

    def test_openai_model_string(self):
        c = configurable(build(model_name="openai:gpt-4o"))
        assert c["model"] == "gpt-4o"
        assert c["model_provider"] == "openai"

    def test_no_provider_model_string(self):
        """Bare model name with no prefix — provider is None, left unset."""
        c = configurable(build(model_name="gpt-4o"))
        assert c["model"] == "gpt-4o"
        assert c.get("model_provider") is None or c["model_provider"] == ""


# ---------------------------------------------------------------------------
# api_key -> huggingfacehub_api_token mapping
# ---------------------------------------------------------------------------


class TestApiKeyMapping:
    """api_key should be mirrored to huggingfacehub_api_token."""

    def test_api_key_mapped_when_set(self):
        c = configurable(build(context_overrides={"api_key": "sk-abc123"}))
        assert c["api_key"] == "sk-abc123"
        assert c.get("huggingfacehub_api_token") == "sk-abc123"

    def test_huggingfacehub_api_token_not_set_without_api_key(self):
        c = configurable(build())
        assert "huggingfacehub_api_token" not in c

    def test_huggingfacehub_api_token_not_overwritten(self):
        """If both api_key and huggingfacehub_api_token are provided,
        setdefault should not overwrite the existing token."""
        c = configurable(
            build(
                context_overrides={
                    "api_key": "sk-abc",
                    "huggingfacehub_api_token": "hf-xyz",
                }
            )
        )
        assert c["huggingfacehub_api_token"] == "hf-xyz"


# ---------------------------------------------------------------------------
# Layer 4: provider_defaults (post-parse, setdefault)
# ---------------------------------------------------------------------------


class TestProviderDefaults:
    """Layer 4 - per-provider defaults from graph config."""

    def test_applied_for_matching_provider(self):
        c = configurable(
            build(
                model_name="huggingface:org/model:auto",
                provider_defaults={
                    "huggingface": {"max_output_tokens": 2048, "temperature": 0.1}
                },
            )
        )
        assert c["max_output_tokens"] == 2048
        assert c["temperature"] == 0.1

    def test_ignored_for_other_provider(self):
        c = configurable(
            build(
                model_name="openai:gpt-4o",
                provider_defaults={"huggingface": {"max_output_tokens": 2048}},
            )
        )
        assert "max_output_tokens" not in c

    def test_role_defaults_win_over_provider_defaults(self):
        c = configurable(
            build(
                model_name="huggingface:org/model:auto",
                role_defaults={"max_output_tokens": 4096, "temperature": 0.5},
                provider_defaults={
                    "huggingface": {"max_output_tokens": 2048, "temperature": 0.1}
                },
            )
        )
        assert c["max_output_tokens"] == 4096
        assert c["temperature"] == 0.5


# ---------------------------------------------------------------------------
# Integration: all layers together
# ---------------------------------------------------------------------------


class TestFullMerge:
    """All four layers combined — verify precedence."""

    def test_full_precedence(self):
        c = configurable(
            build(
                role_defaults={"max_tokens": 2048, "temperature": 0.3},
                model_name="custom:my-model:https://example.com/v1",
                modifiable=True,
                context_overrides={
                    "model": "ollama:llama3:latest",
                    "temperature": 0.7,
                },
                node_defaults={"temperature": 0.0},
            )
        )
        # Layer 3 wins for temperature
        assert c["temperature"] == 0.0
        # Layer 2 wins for model (not frozen)
        assert c["model"] == "llama3:latest"
        assert c["model_provider"] == "ollama"
        # Layer 0 still present (not overridden by anything)
        assert c["max_tokens"] == 2048


# ---------------------------------------------------------------------------
# custom: endpoint surface detection
# ---------------------------------------------------------------------------


class TestCustomEndpointSurface:
    """``custom:`` model strings map their endpoint suffix to a surface."""

    def _config(self, model_name, **kwargs):
        model = LLMModel(
            model_name=model_name,
            instance=None,
            user_agent="klea-agent/0.0.1",
        )
        return configurable(model.build_config(**kwargs))

    def test_base_url_defaults_to_openai(self):
        c = self._config("custom:some-model:https://opencode.ai/zen/go/v1/")
        assert c["model_provider"] == "openai"
        # Bare base URL is preserved verbatim for backward compatibility.
        assert c["base_url"] == "https://opencode.ai/zen/go/v1/"
        assert "use_responses_api" not in c

    def test_chat_completions_endpoint(self):
        c = self._config(
            "custom:deepseek-v4.1-flash:https://opencode.ai/zen/go/v1/chat/completions"
        )
        assert c["model_provider"] == "openai"
        assert c["base_url"] == "https://opencode.ai/zen/go/v1"
        assert c["use_responses_api"] is False

    def test_responses_endpoint(self):
        c = self._config(
            "custom:muse-spark-1.3-contributor:https://opencode.ai/zen/go/v1/responses"
        )
        assert c["model_provider"] == "openai"
        assert c["base_url"] == "https://opencode.ai/zen/go/v1"
        assert c["use_responses_api"] is True

    def test_messages_endpoint(self):
        pytest.importorskip("langchain_anthropic")
        c = self._config("custom:minimax-m3:https://opencode.ai/zen/go/v1/messages")
        assert c["model_provider"] == "anthropic"
        # The Anthropic SDK re-appends /v1/messages, so /v1 is stripped too.
        assert c["anthropic_api_url"] == "https://opencode.ai/zen/go"
        assert "base_url" not in c

    def test_messages_copies_openai_api_key_from_env(self, monkeypatch):
        pytest.importorskip("langchain_anthropic")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        c = self._config("custom:minimax-m3:https://opencode.ai/zen/go/v1/messages")
        assert c["anthropic_api_key"] == "env-key"

    def test_messages_prefers_explicit_api_key(self, monkeypatch):
        pytest.importorskip("langchain_anthropic")
        monkeypatch.setenv("OPENAI_API_KEY", "env-key")
        c = self._config(
            "custom:minimax-m3:https://opencode.ai/zen/go/v1/messages",
            context_overrides={"api_key": "override-key"},
        )
        assert c["anthropic_api_key"] == "override-key"

    def test_messages_without_any_key(self, monkeypatch):
        pytest.importorskip("langchain_anthropic")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        c = self._config("custom:minimax-m3:https://opencode.ai/zen/go/v1/messages")
        assert "anthropic_api_key" not in c
