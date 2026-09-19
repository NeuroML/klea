#!/usr/bin/env python3
"""
Tests for the models.dev catalog client.

File: tests/test_models_catalog.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from klea_utils import models_catalog

_SAMPLE_CATALOG = {
    "openai": {
        "name": "OpenAI",
        "models": {
            "gpt-4o": {
                "id": "gpt-4o",
                "limit": {"context": 128000, "output": 16384},
            },
            "no-output-limit": {
                "id": "no-output-limit",
                "limit": {"context": 1000},
            },
        },
    },
    "huggingface": {
        "name": "Hugging Face",
        "models": {
            "Qwen/Qwen3-Coder-30B-A3B-Instruct": {
                "id": "Qwen/Qwen3-Coder-30B-A3B-Instruct",
                "limit": {"context": 262144, "input": 200000, "output": 65536},
            },
        },
    },
    "openrouter": {
        "name": "OpenRouter",
        "api": "https://openrouter.ai/api/v1",
        "npm": "@openrouter/ai-sdk-provider",
        "env": ["OPENROUTER_API_KEY"],
        "models": {},
    },
    "minimax": {
        "name": "MiniMax",
        "api": "https://api.minimax.io/anthropic/v1",
        "npm": "@ai-sdk/anthropic",
        "models": {},
    },
    "groq": {
        "name": "Groq",
        "npm": "@ai-sdk/groq",
        "models": {},
    },
    "gateway": {
        "name": "Mixed-surface gateway",
        "api": "https://gateway.example.com/v1",
        "npm": "@ai-sdk/openai-compatible",
        "models": {
            "plain-model": {"id": "plain-model"},
            "responses-model": {
                "id": "responses-model",
                "provider": {"npm": "@ai-sdk/openai"},
            },
            "claude-model": {
                "id": "claude-model",
                "provider": {"npm": "@ai-sdk/anthropic"},
            },
            "gemini-model": {
                "id": "gemini-model",
                "provider": {"npm": "@ai-sdk/google"},
            },
        },
    },
}


class TestModelsCatalog(unittest.TestCase):
    """Tests for the models.dev catalog cache and lookups."""

    def setUp(self):
        # Route the on-disk cache to a temp dir and reset the in-memory
        # lru_cache so each test starts from a known state.
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_path = Path(self._tmpdir.name) / "models-dev.json"
        self._path_patcher = mock.patch.object(
            models_catalog, "_disk_cache_path", return_value=self._cache_path
        )
        self._path_patcher.start()
        self.addCleanup(self._path_patcher.stop)
        self.addCleanup(models_catalog._catalog.cache_clear)
        models_catalog._catalog.cache_clear()

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_disk_cache(self, catalog=None, age_seconds=0):
        """Write a catalog to the on-disk cache, optionally aging it."""
        catalog = catalog if catalog is not None else _SAMPLE_CATALOG
        self._cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cache_path, "w") as f:
            json.dump(catalog, f)
        if age_seconds:
            old = time.time() - age_seconds
            os.utime(self._cache_path, (old, old))

    def test_first_fetch_writes_disk_cache(self):
        """A missing cache triggers a fetch, which is mirrored to disk."""
        with mock.patch.object(
            models_catalog, "_fetch_catalog", return_value=_SAMPLE_CATALOG
        ) as fetch:
            limits = models_catalog.get_catalog_model_limits("openai", "gpt-4o")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.output, 16384)
        fetch.assert_called_once()
        self.assertTrue(self._cache_path.exists())

    def test_fresh_disk_cache_no_network(self):
        """A fresh on-disk cache is used without a network fetch."""
        self._write_disk_cache()
        with mock.patch.object(models_catalog, "_fetch_catalog") as fetch:
            limits = models_catalog.get_catalog_model_limits("openai", "gpt-4o")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.output, 16384)
        fetch.assert_not_called()

    def test_expired_disk_cache_refetches(self):
        """An expired on-disk cache is replaced by a fresh fetch."""
        self._write_disk_cache(age_seconds=models_catalog.DISK_CACHE_TTL_SECONDS + 10)
        with mock.patch.object(
            models_catalog, "_fetch_catalog", return_value=_SAMPLE_CATALOG
        ) as fetch:
            limits = models_catalog.get_catalog_model_limits("openai", "gpt-4o")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.output, 16384)
        fetch.assert_called_once()

    def test_offline_no_cache_returns_none(self):
        """A fetch failure with no disk copy resolves to None, not an error."""
        with mock.patch.object(
            models_catalog, "_fetch_catalog", side_effect=RuntimeError("offline")
        ):
            self.assertIsNone(
                models_catalog.get_catalog_model_limits("openai", "gpt-4o")
            )

    def test_offline_falls_back_to_stale_disk(self):
        """A fetch failure falls back to a stale on-disk copy."""
        self._write_disk_cache(age_seconds=models_catalog.DISK_CACHE_TTL_SECONDS + 10)
        with mock.patch.object(
            models_catalog, "_fetch_catalog", side_effect=RuntimeError("offline")
        ):
            limits = models_catalog.get_catalog_model_limits("openai", "gpt-4o")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.output, 16384)

    def test_missing_model_returns_none(self):
        """A model absent from the catalog resolves to None."""
        self._write_disk_cache()
        with mock.patch.object(models_catalog, "_fetch_catalog") as fetch:
            self.assertIsNone(
                models_catalog.get_catalog_model_limits("openai", "no-such-model")
            )
        fetch.assert_not_called()

    def test_provider_without_catalog_entry_returns_none(self):
        """Local providers (ollama) have no catalog entry -> None."""
        self._write_disk_cache()
        with mock.patch.object(models_catalog, "_fetch_catalog") as fetch:
            self.assertIsNone(
                models_catalog.get_catalog_model_limits("ollama", "qwen3:0.6b")
            )
        fetch.assert_not_called()

    def test_unknown_provider_returns_none(self):
        """An unknown provider is passed through and simply misses."""
        self._write_disk_cache()
        self.assertIsNone(
            models_catalog.get_catalog_model_limits("not-a-provider", "gpt-4o")
        )

    def test_custom_provider_maps_to_openai(self):
        """custom: maps to the openai catalog entry, mirroring build_config."""
        self._write_disk_cache()
        limits = models_catalog.get_catalog_model_limits("custom", "gpt-4o")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.output, 16384)

    def test_context_input_output_limits(self):
        """All defined limit fields are returned in ModelLimits."""
        self._write_disk_cache()
        limits = models_catalog.get_catalog_model_limits(
            "huggingface", "Qwen/Qwen3-Coder-30B-A3B-Instruct"
        )
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.context, 262144)
        self.assertEqual(limits.input, 200000)
        self.assertEqual(limits.output, 65536)

    def test_missing_output_field(self):
        """Models without an output limit expose it as None."""
        self._write_disk_cache()
        limits = models_catalog.get_catalog_model_limits("openai", "no-output-limit")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.context, 1000)
        self.assertIsNone(limits.input)
        self.assertIsNone(limits.output)

    def test_fetch_uses_configured_url(self):
        """The catalog URL honours the KLEA_MODELS_DEV_URL override."""
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = _SAMPLE_CATALOG
        with (
            mock.patch("httpx.get", return_value=mock_resp) as get,
            mock.patch.dict(
                "os.environ",
                {"KLEA_MODELS_DEV_URL": "https://mirror.example/api.json"},
            ),
        ):
            models_catalog._catalog.cache_clear()
            limits = models_catalog.get_catalog_model_limits("openai", "gpt-4o")
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.output, 16384)
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://mirror.example/api.json")
        self.assertEqual(kwargs["timeout"], models_catalog.FETCH_TIMEOUT_SECONDS)


class TestEndpointModelLimits(unittest.TestCase):
    """Tests for probe_endpoint_model_limits (live OpenAI-compatible probe)."""

    def setUp(self):
        self.addCleanup(models_catalog._ENDPOINT_LIMITS_CACHE.clear)
        models_catalog._ENDPOINT_LIMITS_CACHE.clear()

    def _resp(self, payload):
        mock_resp = mock.Mock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status.return_value = None
        mock_resp.json.return_value = payload
        return mock_resp

    def test_returns_context_from_max_model_len(self):
        payload = {"data": [{"id": "Qwen", "max_model_len": 262144}]}
        with mock.patch("httpx.get", return_value=self._resp(payload)) as get:
            limits = models_catalog.probe_endpoint_model_limits(
                "openai", "Qwen", "https://example.com/v1", "secret"
            )
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.context, 262144)
        self.assertIsNone(limits.output)
        self.assertIsNone(limits.input)
        get.assert_called_once()
        args, kwargs = get.call_args
        self.assertEqual(args[0], "https://example.com/v1/models")
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer secret")

    def test_caches_after_first_fetch(self):
        payload = {"data": [{"id": "Qwen", "max_model_len": 262144}]}
        with mock.patch("httpx.get", return_value=self._resp(payload)) as get:
            models_catalog.probe_endpoint_model_limits(
                "openai", "Qwen", "https://example.com/v1"
            )
            models_catalog.probe_endpoint_model_limits(
                "openai", "Qwen", "https://example.com/v1"
            )
        get.assert_called_once()

    def test_native_provider_with_base_url_is_probed(self):
        """A non-openai provider with a base_url is probed (relaxed gate)."""
        payload = {"data": [{"id": "Qwen", "max_model_len": 262144}]}
        with mock.patch("httpx.get", return_value=self._resp(payload)) as get:
            limits = models_catalog.probe_endpoint_model_limits(
                "mistral", "Qwen", "https://api.mistral.ai/v1"
            )
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.context, 262144)
        get.assert_called_once()
        args, _kwargs = get.call_args
        self.assertEqual(args[0], "https://api.mistral.ai/v1/models")

    def test_no_base_url_returns_none(self):
        with mock.patch("httpx.get") as get:
            self.assertIsNone(
                models_catalog.probe_endpoint_model_limits("openai", "Qwen", None)
            )
        get.assert_not_called()

    def test_missing_model_returns_none(self):
        payload = {"data": [{"id": "Other", "max_model_len": 1000}]}
        with mock.patch("httpx.get", return_value=self._resp(payload)):
            self.assertIsNone(
                models_catalog.probe_endpoint_model_limits(
                    "openai", "Qwen", "https://example.com/v1"
                )
            )

    def test_http_error_returns_none(self):
        mock_resp = mock.Mock()
        mock_resp.raise_for_status.side_effect = RuntimeError("boom")
        with mock.patch("httpx.get", return_value=mock_resp):
            self.assertIsNone(
                models_catalog.probe_endpoint_model_limits(
                    "openai", "Qwen", "https://example.com/v1"
                )
            )

    def test_network_error_returns_none(self):
        with mock.patch("httpx.get", side_effect=RuntimeError("offline")):
            self.assertIsNone(
                models_catalog.probe_endpoint_model_limits(
                    "openai", "Qwen", "https://example.com/v1"
                )
            )

    def test_non_int_max_model_len_returns_none(self):
        payload = {"data": [{"id": "Qwen", "max_model_len": "large"}]}
        with mock.patch("httpx.get", return_value=self._resp(payload)):
            self.assertIsNone(
                models_catalog.probe_endpoint_model_limits(
                    "openai", "Qwen", "https://example.com/v1"
                )
            )

    def test_env_api_key_fallback_when_no_explicit_key(self):
        """A missing explicit key falls back to the {PROVIDER}_API_KEY env var."""
        payload = {"data": [{"id": "Qwen", "max_model_len": 262144}]}
        with (
            mock.patch("httpx.get", return_value=self._resp(payload)) as get,
            mock.patch.dict(
                "os.environ", {"OPENAI_API_KEY": "env-secret"}, clear=False
            ),
        ):
            limits = models_catalog.probe_endpoint_model_limits(
                "openai", "Qwen", "https://example.com/v1"
            )
        self.assertIsNotNone(limits)
        assert limits is not None
        self.assertEqual(limits.context, 262144)
        _args, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer env-secret")

    def test_explicit_key_wins_over_env(self):
        """An explicit api_key overrides the env var."""
        payload = {"data": [{"id": "Qwen", "max_model_len": 262144}]}
        with (
            mock.patch("httpx.get", return_value=self._resp(payload)) as get,
            mock.patch.dict(
                "os.environ", {"OPENAI_API_KEY": "env-secret"}, clear=False
            ),
        ):
            models_catalog.probe_endpoint_model_limits(
                "openai", "Qwen", "https://example.com/v1", "explicit-secret"
            )
        _args, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer explicit-secret")

    def test_env_key_derived_from_provider(self):
        """The env var name is derived from the provider (e.g. MISTRAL_API_KEY)."""
        payload = {"data": [{"id": "Qwen", "max_model_len": 262144}]}
        with (
            mock.patch("httpx.get", return_value=self._resp(payload)) as get,
            mock.patch.dict(
                "os.environ", {"MISTRAL_API_KEY": "mistral-secret"}, clear=False
            ),
        ):
            models_catalog.probe_endpoint_model_limits(
                "mistralai", "Qwen", "https://api.mistral.ai/v1"
            )
        _args, kwargs = get.call_args
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer mistral-secret")


class TestProviderEndpoint(unittest.TestCase):
    """Tests for get_provider_endpoint (provider -> api + npm)."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._cache_path = Path(self._tmpdir.name) / "models-dev.json"
        self._path_patcher = mock.patch.object(
            models_catalog, "_disk_cache_path", return_value=self._cache_path
        )
        self._path_patcher.start()
        self.addCleanup(self._path_patcher.stop)
        self.addCleanup(models_catalog._catalog.cache_clear)
        models_catalog._catalog.cache_clear()
        with open(self._cache_path, "w") as f:
            json.dump(_SAMPLE_CATALOG, f)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_openai_compatible_provider(self):
        entry = models_catalog.get_provider_endpoint("openrouter")
        assert entry is not None
        self.assertEqual(entry.api, "https://openrouter.ai/api/v1")
        self.assertEqual(entry.npm, "@openrouter/ai-sdk-provider")

    def test_anthropic_provider(self):
        entry = models_catalog.get_provider_endpoint("minimax")
        assert entry is not None
        self.assertEqual(entry.npm, "@ai-sdk/anthropic")

    def test_native_provider_without_api(self):
        entry = models_catalog.get_provider_endpoint("groq")
        assert entry is not None
        self.assertIsNone(entry.api)
        self.assertEqual(entry.npm, "@ai-sdk/groq")

    def test_unknown_provider_returns_none(self):
        self.assertIsNone(models_catalog.get_provider_endpoint("nope-xyz"))

    def test_per_model_npm_override(self):
        """A model's provider.npm override wins over the provider default."""
        entry = models_catalog.get_provider_endpoint("gateway", "responses-model")
        assert entry is not None
        self.assertEqual(entry.npm, "@ai-sdk/openai")
        self.assertEqual(entry.api, "https://gateway.example.com/v1")

    def test_model_without_override_uses_provider_npm(self):
        entry = models_catalog.get_provider_endpoint("gateway", "plain-model")
        assert entry is not None
        self.assertEqual(entry.npm, "@ai-sdk/openai-compatible")

    def test_unknown_model_uses_provider_npm(self):
        entry = models_catalog.get_provider_endpoint("gateway", "not-a-model")
        assert entry is not None
        self.assertEqual(entry.npm, "@ai-sdk/openai-compatible")

    def test_offline_returns_none(self):
        models_catalog._catalog.cache_clear()
        self._cache_path.unlink()
        with mock.patch.object(
            models_catalog, "_fetch_catalog", side_effect=RuntimeError("no net")
        ):
            self.assertIsNone(models_catalog.get_provider_endpoint("openrouter"))


if __name__ == "__main__":
    unittest.main()
