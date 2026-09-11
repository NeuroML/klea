#!/usr/bin/env python3
"""
Tests for provider request headers (Klea User-Agent, opencode session).

File: tests/test_llm_provider_headers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from importlib import metadata

import httpx
import pytest
from klea_utils.llm import (
    LLMModel,
    apply_provider_overrides,
    resolve_user_agent,
)
from klea_utils.nodes.base import _current_session_id


class TestResolveUserAgent:
    def test_known_prefix_gets_version(self):
        expected = f"klea-agent/{metadata.version('klea_agent')}"
        assert resolve_user_agent("klea-agent") == expected
        assert (
            resolve_user_agent("klea-rag") == f"klea-rag/{metadata.version('klea_rag')}"
        )

    def test_unknown_prefix_falls_back_to_dev(self):
        assert resolve_user_agent("no-such-dist-xyz") == "no-such-dist-xyz/dev"


class TestApplyProviderOverrides:
    def test_openai_user_agent(self):
        overrides = {"model_provider": "openai"}
        apply_provider_overrides(overrides, user_agent="klea-agent/0.0.1")
        assert overrides["default_headers"] == {"User-Agent": "klea-agent/0.0.1"}

    def test_opencode_gets_session_header(self):
        overrides = {
            "model_provider": "openai",
            "base_url": "https://opencode.ai/zen/go/v1/",
        }
        apply_provider_overrides(
            overrides, session_id="sess-1", user_agent="klea-agent/0.0.1"
        )
        assert overrides["default_headers"] == {
            "User-Agent": "klea-agent/0.0.1",
            "x-opencode-session": "sess-1",
        }

    def test_anthropic_opencode_gets_session_header(self):
        """The anthropic endpoint lives under anthropic_api_url."""
        overrides = {
            "model_provider": "anthropic",
            "anthropic_api_url": "https://opencode.ai/zen/go",
        }
        apply_provider_overrides(
            overrides, session_id="sess-1", user_agent="klea-agent/0.0.1"
        )
        assert overrides["default_headers"] == {
            "User-Agent": "klea-agent/0.0.1",
            "x-opencode-session": "sess-1",
        }

    def test_non_opencode_gets_only_user_agent(self):
        overrides = {
            "model_provider": "openai",
            "base_url": "https://api.example.com/v1",
        }
        apply_provider_overrides(
            overrides, session_id="sess-1", user_agent="klea-agent/0.0.1"
        )
        assert overrides["default_headers"] == {"User-Agent": "klea-agent/0.0.1"}

    def test_other_provider_untouched(self):
        overrides = {"model_provider": "mistral"}
        apply_provider_overrides(
            overrides, session_id="sess-1", user_agent="klea-agent/0.0.1"
        )
        assert "default_headers" not in overrides

    def test_existing_headers_win(self):
        overrides = {
            "model_provider": "openai",
            "default_headers": {"User-Agent": "custom/1.0", "X-Custom": "1"},
        }
        apply_provider_overrides(overrides, user_agent="klea-agent/0.0.1")
        assert overrides["default_headers"] == {
            "User-Agent": "custom/1.0",
            "X-Custom": "1",
        }


class TestBuildConfigHeaders:
    def _model(self, name: str) -> LLMModel:
        return LLMModel(
            instance=None,
            model_name=name,
            user_agent="klea-agent/0.0.1",
        )

    def test_custom_opencode_endpoint(self):
        model = self._model("custom:deepseek-v4-flash:https://opencode.ai/zen/go/v1/")
        config = model.build_config(session_id="sess-1")
        cfg = config["configurable"]
        assert cfg["model_provider"] == "openai"
        assert cfg["base_url"] == "https://opencode.ai/zen/go/v1/"
        assert cfg["default_headers"] == {
            "User-Agent": "klea-agent/0.0.1",
            "x-opencode-session": "sess-1",
        }

    def test_custom_other_endpoint(self):
        model = self._model("custom:gpt-4o:https://api.example.com/v1")
        config = model.build_config(session_id="sess-1")
        cfg = config["configurable"]
        assert cfg["default_headers"] == {"User-Agent": "klea-agent/0.0.1"}


class TestCurrentSessionId:
    def test_none_outside_run(self):
        assert _current_session_id() is None

    def test_reads_thread_id(self, monkeypatch):
        monkeypatch.setattr(
            "langgraph.config.get_config",
            lambda: {"configurable": {"thread_id": "sess-1"}},
        )
        assert _current_session_id() == "sess-1"

    def test_missing_thread_id(self, monkeypatch):
        monkeypatch.setattr("langgraph.config.get_config", dict)
        assert _current_session_id() is None


@pytest.mark.asyncio
async def test_default_headers_reach_the_wire():
    """``default_headers`` override the OpenAI SDK User-Agent on the wire."""
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        return httpx.Response(
            200,
            json={
                "id": "1",
                "object": "chat.completion",
                "created": 0,
                "model": "x",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "hi"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    from langchain_openai import ChatOpenAI

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    model = ChatOpenAI(
        model="x",
        base_url="https://example.com/v1",
        api_key="k",
        default_headers={
            "User-Agent": "klea-agent/0.0.1",
            "x-opencode-session": "sess-1",
        },
        http_async_client=client,
    )
    await model.ainvoke("hi")

    assert seen["user-agent"] == "klea-agent/0.0.1"
    assert seen["x-opencode-session"] == "sess-1"
    await client.aclose()


def _chat_completion_response() -> dict:
    return {
        "id": "1",
        "object": "chat.completion",
        "created": 0,
        "model": "m",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": "hi"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }


def _responses_response() -> dict:
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 0,
        "model": "m",
        "status": "completed",
        "output": [
            {
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "hi", "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
    }


@pytest.mark.asyncio
async def test_custom_chat_completions_hits_chat_endpoint(monkeypatch):
    """A custom ``/chat/completions`` URL resolves to that exact endpoint."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from langchain.chat_models import init_chat_model

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        seen["__url__"] = str(request.url)
        return httpx.Response(200, json=_chat_completion_response())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = LLMModel(
        instance=init_chat_model(model=None, configurable_fields="any"),
        model_name=(
            "custom:deepseek-v4.1-flash:https://opencode.ai/zen/go/v1/chat/completions"
        ),
        user_agent="klea-agent/0.0.1",
    )
    config = entry.build_config(
        session_id="sess-1", context_overrides={"http_async_client": client}
    )
    await entry.instance.ainvoke("hi", config=config)
    await client.aclose()

    assert seen["__url__"] == "https://opencode.ai/zen/go/v1/chat/completions"
    assert seen["user-agent"] == "klea-agent/0.0.1"
    assert seen["x-opencode-session"] == "sess-1"


@pytest.mark.asyncio
async def test_custom_responses_hits_responses_endpoint(monkeypatch):
    """A custom ``/responses`` URL resolves to the Responses API endpoint."""
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    from langchain.chat_models import init_chat_model

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update({k.lower(): v for k, v in request.headers.items()})
        seen["__url__"] = str(request.url)
        return httpx.Response(200, json=_responses_response())

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    entry = LLMModel(
        instance=init_chat_model(model=None, configurable_fields="any"),
        model_name=(
            "custom:muse-spark-1.3-contributor:https://opencode.ai/zen/go/v1/responses"
        ),
        user_agent="klea-agent/0.0.1",
    )
    config = entry.build_config(
        session_id="sess-1", context_overrides={"http_async_client": client}
    )
    await entry.instance.ainvoke("hi", config=config)
    await client.aclose()

    assert seen["__url__"] == "https://opencode.ai/zen/go/v1/responses"
    assert seen["user-agent"] == "klea-agent/0.0.1"
    assert seen["x-opencode-session"] == "sess-1"


def test_anthropic_sdk_posts_to_messages_with_headers():
    """The stripped anthropic base + default headers yield /v1/messages."""
    anthropic = pytest.importorskip("anthropic")
    from anthropic._models import FinalRequestOptions

    client = anthropic.Anthropic(
        api_key="test-key",
        base_url="https://opencode.ai/zen/go",
        default_headers={
            "User-Agent": "klea-agent/0.0.1",
            "x-opencode-session": "sess-1",
        },
    )
    options = FinalRequestOptions(
        method="post",
        url="/v1/messages",
        json_data={"model": "m", "max_tokens": 1, "messages": []},
    )
    request = client._build_request(options)
    client.close()

    assert str(request.url) == "https://opencode.ai/zen/go/v1/messages"
    headers = {k.lower(): v for k, v in request.headers.items()}
    assert headers["user-agent"] == "klea-agent/0.0.1"
    assert headers["x-opencode-session"] == "sess-1"
