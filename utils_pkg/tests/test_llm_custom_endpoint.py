#!/usr/bin/env python3
"""
Tests for custom endpoint URL surface detection.

File: tests/test_llm_custom_endpoint.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from unittest import mock

import pytest
from klea_utils.llm import (
    CustomEndpoint,
    resolve_catalog_provider_endpoint,
    resolve_custom_endpoint,
)
from klea_utils.models_catalog import ProviderEndpoint


class TestResolveCustomEndpoint:
    def test_chat_completions(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/chat/completions"
        ) == CustomEndpoint("https://opencode.ai/zen/go/v1", "openai", False)

    def test_chat_completions_trailing_slash(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/chat/completions/"
        ) == CustomEndpoint("https://opencode.ai/zen/go/v1", "openai", False)

    def test_responses(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/responses"
        ) == CustomEndpoint("https://opencode.ai/zen/go/v1", "openai", True)

    def test_responses_trailing_slash(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/responses/"
        ) == CustomEndpoint("https://opencode.ai/zen/go/v1", "openai", True)

    def test_messages(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/messages"
        ) == CustomEndpoint("https://opencode.ai/zen/go", "anthropic", None)

    def test_messages_trailing_slash(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/messages/"
        ) == CustomEndpoint("https://opencode.ai/zen/go", "anthropic", None)

    def test_base_url_falls_back_to_openai(self):
        assert resolve_custom_endpoint(
            "https://opencode.ai/zen/go/v1/"
        ) == CustomEndpoint("https://opencode.ai/zen/go/v1/", "openai", None)

    def test_base_url_without_trailing_slash(self):
        assert resolve_custom_endpoint("https://api.example.com/v1") == CustomEndpoint(
            "https://api.example.com/v1", "openai", None
        )

    def test_empty_url_raises(self):
        with pytest.raises(ValueError):
            resolve_custom_endpoint("")

    def test_endpoint_without_base_path_raises(self):
        with pytest.raises(ValueError):
            resolve_custom_endpoint("/chat/completions")


class TestResolveCatalogProviderEndpoint:
    """Catalog providers resolve to a wire surface via api + npm."""

    def _resolve(self, entry):
        with mock.patch("klea_utils.llm.get_provider_endpoint", return_value=entry):
            return resolve_catalog_provider_endpoint("some-provider")

    def test_openai_compatible(self):
        out = self._resolve(
            ProviderEndpoint(api="https://openrouter.ai/api/v1", npm="@openrouter/x")
        )
        assert out == CustomEndpoint("https://openrouter.ai/api/v1", "openai", None)

    def test_vendor_openai_npm_defaults_to_chat_completions(self):
        """A non-``openai-compatible`` vendor package is still OpenAI wire."""
        out = self._resolve(
            ProviderEndpoint(
                api="https://gateway.example.com/v1",
                npm="@vendor/ai-sdk-provider",
            )
        )
        assert out == CustomEndpoint("https://gateway.example.com/v1", "openai", None)

    def test_openai_npm_selects_responses(self):
        out = self._resolve(
            ProviderEndpoint(api="https://opencode.ai/zen/go/v1", npm="@ai-sdk/openai")
        )
        assert out == CustomEndpoint("https://opencode.ai/zen/go/v1", "openai", True)

    def test_google_npm_returns_none(self):
        out = self._resolve(
            ProviderEndpoint(api="https://opencode.ai/zen/v1", npm="@ai-sdk/google")
        )
        assert out is None

    def test_per_model_npm_override(self):
        """The model name is passed through for per-model surface resolution."""
        with mock.patch(
            "klea_utils.llm.get_provider_endpoint",
            return_value=ProviderEndpoint(
                api="https://opencode.ai/zen/go/v1", npm="@ai-sdk/openai"
            ),
        ) as lookup:
            out = resolve_catalog_provider_endpoint("opencode-go", "muse-spark")
        lookup.assert_called_once_with("opencode-go", "muse-spark")
        assert out == CustomEndpoint("https://opencode.ai/zen/go/v1", "openai", True)

    def test_anthropic_strips_trailing_v1(self):
        out = self._resolve(
            ProviderEndpoint(
                api="https://api.minimax.io/anthropic/v1", npm="@ai-sdk/anthropic"
            )
        )
        assert out == CustomEndpoint(
            "https://api.minimax.io/anthropic", "anthropic", None
        )

    def test_anthropic_full_path_stripped(self):
        out = self._resolve(
            ProviderEndpoint(
                api="https://api.example.com/anthropic/v1/messages",
                npm="@ai-sdk/anthropic",
            )
        )
        assert out == CustomEndpoint(
            "https://api.example.com/anthropic", "anthropic", None
        )

    def test_no_api_returns_none(self):
        assert self._resolve(ProviderEndpoint(api=None, npm="@ai-sdk/groq")) is None

    def test_missing_entry_returns_none(self):
        assert self._resolve(None) is None
