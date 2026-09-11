#!/usr/bin/env python3
"""
Tests for custom endpoint URL surface detection.

File: tests/test_llm_custom_endpoint.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from klea_utils.llm import CustomEndpoint, resolve_custom_endpoint


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
