#!/usr/bin/env python3
"""
Tests for bounded max-output token resolution (resolve_output_token_limit).

File: tests/test_output_token_limit.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import unittest
from unittest import mock

from klea_utils.llm import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    resolve_output_token_limit,
)
from klea_utils.models_catalog import ModelLimits


def _no_catalog(limits=None):
    """Patch get_catalog_model_limits to return a fixed value (default: no info)."""
    return mock.patch("klea_utils.llm.get_catalog_model_limits", return_value=limits)


class TestResolveOutputTokenLimit(unittest.TestCase):
    """Tests for resolve_output_token_limit."""

    def _resolve(self, overrides, provider, **kwargs):
        with _no_catalog():
            resolve_output_token_limit(overrides, provider, **kwargs)
        return overrides

    # --- role fallback defaults ---

    def test_chat_role_default(self):
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai"}, "openai", role="chat"
        )
        self.assertEqual(ov["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    def test_guard_role_default(self):
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai"}, "openai", role="guard"
        )
        self.assertEqual(ov["max_tokens"], 2048)

    def test_unknown_role_default(self):
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai"},
            "openai",
            role="mystery",
        )
        self.assertEqual(ov["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    def test_no_role_default(self):
        ov = self._resolve({"model": "gpt-4o", "model_provider": "openai"}, "openai")
        self.assertEqual(ov["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    # --- explicit provider param wins ---

    def test_explicit_param_wins(self):
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai", "max_tokens": 8192},
            "openai",
            role="chat",
        )
        self.assertEqual(ov["max_tokens"], 8192)

    # --- generic key translation ---

    def test_generic_key_translated_to_openai(self):
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai", "max_output_tokens": 2048},
            "openai",
            role="chat",
        )
        self.assertEqual(ov["max_tokens"], 2048)
        self.assertNotIn("max_output_tokens", ov)

    def test_generic_key_translated_to_ollama(self):
        ov = self._resolve(
            {
                "model": "qwen3:0.6b",
                "model_provider": "ollama",
                "max_output_tokens": 2048,
            },
            "ollama",
            role="chat",
        )
        self.assertEqual(ov["num_predict"], 2048)
        self.assertNotIn("max_output_tokens", ov)

    def test_stale_param_replaced(self):
        """A param for the wrong provider is replaced by the correct one."""
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai", "max_new_tokens": 512},
            "openai",
            role="chat",
        )
        self.assertEqual(ov["max_tokens"], 512)
        self.assertNotIn("max_new_tokens", ov)

    # --- catalog clamps ---

    def test_output_limit_clamp(self):
        with _no_catalog(ModelLimits(context=128000, output=16384)):
            ov = {"model": "gpt-4o", "model_provider": "openai", "max_tokens": 30000}
            resolve_output_token_limit(ov, "openai", role="chat")
        self.assertEqual(ov["max_tokens"], 16384)

    def test_output_limit_clamp_not_applied_when_under(self):
        with _no_catalog(ModelLimits(context=128000, output=16384)):
            ov = {"model": "gpt-4o", "model_provider": "openai", "max_tokens": 2048}
            resolve_output_token_limit(ov, "openai", role="chat")
        self.assertEqual(ov["max_tokens"], 2048)

    def test_total_budget_clamp(self):
        """Output is bounded by context - estimated input tokens."""
        # 40_000 chars ~= 10_000 tokens; with the 5% safety factor the
        # estimate is 10_500; context 32k -> headroom 22_268.
        # An explicit 30k budget is reduced to the available headroom.
        with _no_catalog(ModelLimits(context=32768, output=65536)):
            ov = {
                "model": "org/model",
                "model_provider": "huggingface",
                "max_output_tokens": 30000,
            }
            resolve_output_token_limit(
                ov, "huggingface", role="chat", input_chars=40000
            )
        self.assertEqual(ov["max_tokens"], 22268)

    def test_total_budget_clamp_below_role_default(self):
        """A tight context + large input shrinks even the role default."""
        with _no_catalog(ModelLimits(context=8192, output=65536)):
            ov = {"model": "org/model", "model_provider": "huggingface"}
            resolve_output_token_limit(
                ov, "huggingface", role="chat", input_chars=30000
            )
        # 30k chars ~= 7500 tokens; with the 5% safety factor that is 7875;
        # headroom = 8192 - 7875 = 317.
        self.assertEqual(ov["max_tokens"], 317)

    def test_no_total_budget_clamp_without_input_chars(self):
        """Without input_chars only the output-limit clamp applies."""
        with _no_catalog(ModelLimits(context=8192, output=65536)):
            ov = {"model": "org/model", "model_provider": "huggingface"}
            resolve_output_token_limit(ov, "huggingface", role="chat")
        self.assertEqual(ov["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    def test_no_clamp_without_catalog(self):
        """No catalog info means only the role default applies."""
        ov = self._resolve(
            {"model": "gpt-4o", "model_provider": "openai"}, "openai", role="chat"
        )
        self.assertEqual(ov["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    # --- live-endpoint limits (custom OpenAI-compatible endpoints) ---

    def _resolve_with_endpoint(self, overrides, provider, endpoint_limits, **kwargs):
        """Resolve with models.dev returning None and a mocked endpoint lookup.

        Uses ``use_endpoint=True`` so the endpoint context is consulted
        (the retry-path behaviour).
        """
        kwargs.setdefault("use_endpoint", True)
        with (
            _no_catalog(),
            mock.patch(
                "klea_utils.llm.probe_endpoint_model_limits",
                return_value=endpoint_limits,
            ),
        ):
            resolve_output_token_limit(overrides, provider, **kwargs)
        return overrides

    def test_custom_endpoint_total_budget_clamp(self):
        """A custom endpoint's max_model_len drives the total-budget clamp."""
        ov = self._resolve_with_endpoint(
            {
                "model": "Qwen",
                "model_provider": "openai",
                "base_url": "https://inf01.arc-llm.condenser.arc.ucl.ac.uk/v1/",
                "max_output_tokens": 30000,
            },
            "openai",
            ModelLimits(context=262144, output=None, input=None),
            role="chat",
            input_chars=100000,  # ~26250 tokens with 5% factor -> headroom ~236k
        )
        # 30000 < 236k headroom, so it stays at the configured value.
        self.assertEqual(ov["max_tokens"], 30000)

    def test_custom_endpoint_headroom_shrinks_large_budget(self):
        """A huge budget is clamped by the endpoint's remaining context."""
        ov = self._resolve_with_endpoint(
            {
                "model": "Qwen",
                "model_provider": "openai",
                "base_url": "https://example.com/v1/",
                "max_output_tokens": 200000,
            },
            "openai",
            ModelLimits(context=262144, output=None, input=None),
            role="chat",
            input_chars=40000,  # ~10500 tokens with 5% factor -> headroom ~251k
        )
        self.assertEqual(ov["max_tokens"], 200000)

    def test_custom_endpoint_small_context_clamps(self):
        """A tight endpoint context shrinks even a modest budget."""
        ov = self._resolve_with_endpoint(
            {
                "model": "Qwen",
                "model_provider": "openai",
                "base_url": "https://example.com/v1/",
                "max_output_tokens": 4096,
            },
            "openai",
            ModelLimits(context=8192, output=None, input=None),
            role="chat",
            input_chars=30000,  # ~7875 tokens with 5% factor -> headroom ~317
        )
        self.assertEqual(ov["max_tokens"], 317)

    def test_native_provider_with_resolved_endpoint_is_probed(self):
        """A native provider's resolved base_url drives the retry-path clamp."""
        ov = self._resolve_with_endpoint(
            {
                "model": "mistral-small-latest",
                "model_provider": "mistralai",
                "base_url": "https://api.mistral.ai/v1",
                "max_output_tokens": 4096,
            },
            "mistralai",
            ModelLimits(context=8192, output=None, input=None),
            role="chat",
            input_chars=30000,  # ~7875 tokens with 5% factor -> headroom ~317
        )
        self.assertEqual(ov["max_tokens"], 317)

    def test_normal_path_does_not_query_endpoint(self):
        """use_endpoint=False (normal path) never calls the endpoint lookup."""
        with (
            _no_catalog(),
            mock.patch("klea_utils.llm.probe_endpoint_model_limits") as endpoint_lookup,
        ):
            ov = {
                "model": "Qwen",
                "model_provider": "openai",
                "base_url": "https://example.com/v1/",
            }
            resolve_output_token_limit(ov, "openai", role="chat", input_chars=30000)
        endpoint_lookup.assert_not_called()
        # No models.dev entry -> falls back to the chat role default.
        self.assertEqual(ov["max_tokens"], DEFAULT_MAX_OUTPUT_TOKENS)

    def test_use_endpoint_falls_back_to_models_dev_when_none(self):
        """Endpoint returning None falls back to models.dev for context."""
        with (
            _no_catalog(ModelLimits(context=128000, output=16384)),
            mock.patch("klea_utils.llm.probe_endpoint_model_limits", return_value=None),
        ):
            ov = {
                "model": "gpt-4o",
                "model_provider": "openai",
                "base_url": "https://example.com/v1/",
                "max_output_tokens": 30000,
            }
            resolve_output_token_limit(
                ov, "openai", role="chat", input_chars=10000, use_endpoint=True
            )
        # Clamped to models.dev output cap.
        self.assertEqual(ov["max_tokens"], 16384)


if __name__ == "__main__":
    unittest.main()
