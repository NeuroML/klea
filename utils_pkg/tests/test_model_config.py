#!/usr/bin/env python3
"""
Tests for the shared (framework-agnostic) model-configuration helpers.

File: tests/test_model_config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from typing import Any

import pytest
from klea_utils.ui import model_config
from klea_utils.ui.model_config import (
    TONE_ERROR,
    TONE_MUTED,
    CatalogueCache,
    build_model_string,
    changed_roles,
    clear_role,
    credential_display,
    live_credential,
    live_info,
    parse_model_origin,
    required_credential_scopes,
    role_label,
    save_roles,
    use_for_all_targets,
)


def test_role_label_formats_underscored_keys():
    """Underscored role keys render as readable labels."""
    assert role_label("tool_picker") == "Tool picker"
    assert role_label("chat") == "Chat"
    assert role_label("embedding") == "Embedding"


def test_parse_and_build_round_trip():
    """A saved model string round-trips through the fields."""
    for model in (
        "openai:gpt-4o",
        "ollama:bge-m3:latest",
        "custom:my-model:https://host/v1",
        "huggingface:org/model:novita",
        "huggingface:org/model:local",
    ):
        origin = parse_model_origin(model)
        rebuilt = build_model_string(
            origin.provider,
            origin.model,
            url=origin.url,
            backend=origin.backend,
            inference_provider=origin.inference_provider,
            original=origin,
        )
        assert rebuilt == model


def test_parse_half_typed_keeps_whole_string():
    """A half-typed model string is kept whole in the model field."""
    origin = parse_model_origin("openai:")
    assert origin.provider == ""
    assert origin.model == "openai:"
    assert build_model_string("", "openai:", original=origin) == "openai:"


def test_build_huggingface_variants():
    """HuggingFace joins from its backend and inference-provider fields."""
    origin = parse_model_origin("huggingface:org/model:auto")
    assert (
        build_model_string(
            "huggingface",
            "org/model",
            backend="local",
            original=origin,
        )
        == "huggingface:org/model:local"
    )
    assert (
        build_model_string(
            "huggingface",
            "org/model",
            backend="endpoint",
            inference_provider="",
            original=origin,
        )
        == "huggingface:org/model:auto"
    )
    assert (
        build_model_string(
            "huggingface",
            "org/model",
            backend="endpoint",
            inference_provider="novita",
            original=origin,
        )
        == "huggingface:org/model:novita"
    )


def test_build_custom_url_and_unchanged_suffix():
    """A custom URL wins; an unchanged non-URL suffix is restored."""
    origin = parse_model_origin("custom:m:https://host/v1")
    assert (
        build_model_string("custom", "m", url="https://host/v1", original=origin)
        == "custom:m:https://host/v1"
    )
    suffixed = parse_model_origin("foo:bar:baz")
    assert build_model_string("foo", "bar", original=suffixed) == "foo:bar:baz"
    # A changed model drops the stale suffix.
    assert build_model_string("foo", "qux", original=suffixed) == "foo:qux"


def test_required_credential_scopes_dedupes_key_providers():
    """Only key-requiring roles contribute a scope, deduped by provider."""
    info = {
        "chat": {"credential": {"provider": "openai", "requires_key": True}},
        "plan": {"credential": {"provider": "openai", "requires_key": True}},
        "embedding": {"credential": {"provider": "ollama", "requires_key": False}},
    }
    scopes = required_credential_scopes(info)
    assert list(scopes) == [("openai", "")]


def test_live_credential_and_display_tones():
    """Credential status is derived from the stored scope and tones."""
    stored = {("openai", ""): {"masked": "sk-***"}}
    assert live_credential("openai:gpt-4o", None, {})["source"] == "none"
    cred = live_credential("openai:gpt-4o", None, stored)
    assert cred["source"] == "user"
    assert cred["masked"] == "sk-***"
    assert live_credential("ollama:qwen3", None, {})["requires_key"] is False

    assert credential_display("", None, {}) == ("No model selected", TONE_MUTED)
    assert credential_display("bge-m3", None, {}) == (
        "No API key configured",
        TONE_ERROR,
    )
    assert credential_display("ollama:qwen3", None, {}) == (
        "No API key required",
        TONE_MUTED,
    )
    label, tone = credential_display("openai:gpt-4o", None, stored)
    assert tone == TONE_MUTED
    assert "sk-***" in label
    assert credential_display("openai:gpt-4o", None, {}) == (
        "No API key configured for openai",
        TONE_ERROR,
    )


def test_live_info_replaces_model_and_credential():
    """Live info reflects unsaved role values."""
    info = {"chat": {"model": "openai:gpt-4o", "credential": {"provider": "openai"}}}
    live = live_info(info, {"chat": "anthropic:claude"}, {})
    assert live["chat"]["model"] == "anthropic:claude"
    assert live["chat"]["credential"]["provider"] == "anthropic"


def test_changed_roles_skips_locked_and_unchanged():
    """Changed roles exclude locked roles and unchanged values."""
    info = {
        "chat": {"model": "a", "modifiable": True},
        "guard": {"model": "b", "modifiable": False},
    }
    assert changed_roles(info, {"chat": "c", "guard": "d"}) == [("chat", "c")]
    assert changed_roles(info, {"chat": "a"}) == []


def test_use_for_all_targets_scoped_by_model_type():
    """Only modifiable same-kind roles are targeted."""
    info = {
        "chat": {"model_type": "text_generation", "modifiable": True},
        "plan": {"model_type": "text_generation", "modifiable": True},
        "guard": {"model_type": "text_generation", "modifiable": False},
        "embedding": {"model_type": "embedding", "modifiable": True},
    }
    assert use_for_all_targets(info, "chat", "x") == {"plan": "x"}


async def test_catalogue_cache_fetches_once(monkeypatch):
    """Providers and each provider's models are fetched once and cached."""
    calls: dict[str, Any] = {"providers": 0, "models": []}

    async def _providers(server_url, user_id):
        calls["providers"] += 1
        return ["openai", "ollama"]

    async def _models(server_url, user_id, provider):
        calls["models"].append(provider)
        return [f"{provider}-model"]

    monkeypatch.setattr(model_config, "fetch_catalogue_providers", _providers)
    monkeypatch.setattr(model_config, "fetch_catalogue_models", _models)

    cache = CatalogueCache("http://server", "u")
    assert await cache.providers() == ["openai", "ollama"]
    assert await cache.providers() == ["openai", "ollama"]
    assert calls["providers"] == 1
    assert await cache.load_models("openai") == ["openai-model"]
    assert cache.models_for("openai") == ["openai-model"]
    assert await cache.load_models("openai") == ["openai-model"]
    assert calls["models"] == ["openai"]


async def test_save_roles_session_scope(monkeypatch):
    """Session-scope saves call the session override endpoint."""
    calls = []

    async def _set_session(server_url, user_id, role, payload):
        calls.append((role, payload))
        return True

    monkeypatch.setattr(model_config, "set_session_model_override", _set_session)

    info = {"chat": {"model": "a", "modifiable": True}}
    outcome = await save_roles(
        "http://server",
        "u",
        "",
        session_scope=True,
        info=info,
        role_values={"chat": "b"},
    )
    assert outcome.ok
    assert outcome.saved == [("chat", "b")]
    assert calls == [("chat", {"model": "b"})]


async def test_save_roles_chat_scope_promotes_and_stops_on_failure(monkeypatch):
    """Chat-scope saves promote the default and stop at the first failure."""
    calls = []

    async def _set_chat(server_url, user_id, chat_id, role, payload):
        calls.append((chat_id, role, payload))
        return role != "guard"

    monkeypatch.setattr(model_config, "set_model_override", _set_chat)

    info = {
        "chat": {"model": "a", "modifiable": True},
        "guard": {"model": "g", "modifiable": True},
    }
    outcome = await save_roles(
        "http://server",
        "u",
        "c1",
        session_scope=False,
        info=info,
        role_values={"chat": "b", "guard": "h"},
    )
    assert not outcome.ok
    assert outcome.saved == [("chat", "b")]
    assert outcome.failed_role == "guard"
    assert calls == [
        ("c1", "chat", {"model": "b", "promote_default": True}),
        ("c1", "guard", {"model": "h", "promote_default": True}),
    ]


async def test_clear_role_selects_scope(monkeypatch):
    """Clear picks the session or chat endpoint by scope."""
    calls = []

    async def _clear_session(server_url, user_id, role):
        calls.append(("session", role))
        return True

    async def _clear_chat(server_url, user_id, chat_id, role):
        calls.append(("chat", role))
        return True

    monkeypatch.setattr(model_config, "clear_session_model_override", _clear_session)
    monkeypatch.setattr(model_config, "clear_model_override", _clear_chat)

    assert await clear_role("http://server", "u", "", session_scope=True, role="chat")
    assert await clear_role(
        "http://server", "u", "c1", session_scope=False, role="chat"
    )
    assert calls == [("session", "chat"), ("chat", "chat")]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
