#!/usr/bin/env python3
"""
Tests for adaptive LLM invoke retries (BaseLLMNode._invoke_with_retries).

File: tests/test_llm_invoke_retries.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from unittest import mock

import pytest
from klea_utils.graph.context import KleaRunContext
from klea_utils.llm import LLMModel
from klea_utils.models_catalog import ModelLimits
from klea_utils.nodes.base import (
    MAX_CONTEXT_OVERFLOW_RETRIES,
    MAX_EMPTY_OUTPUT_RETRIES,
    MAX_OUTPUT_TOKENS_CEILING,
    MAX_TRUNCATION_RETRIES,
    TRUNCATION_LINEAR_STEP,
    BaseLLMNode,
)
from langchain_core.exceptions import OutputParserException
from langchain_core.messages import AIMessage
from langchain_core.prompt_values import StringPromptValue
from langgraph.runtime import Runtime
from pydantic import BaseModel

logger = logging.getLogger("test")


def _runtime_context(model_overrides: dict | None = None):
    """Harness: run a block inside a LangGraph Runtime context (ADR-0033).

    ``_build_invoke_config`` reads ``get_runtime().context`` ambiently; a
    direct call outside a graph run would raise, so unit tests stub the
    ambient accessor with a ``Runtime`` carrying the given overrides.
    """
    runtime = Runtime(context=KleaRunContext(model_overrides=model_overrides or {}))
    return mock.patch("klea_utils.nodes.base.get_runtime", return_value=runtime)


class _OutputSchema(BaseModel):
    answer: str


class _MinimalLLMNode(BaseLLMNode[BaseModel, BaseModel]):
    """Concrete LLM node implementing the remaining abstract methods."""

    model_type = "chat"

    def _get_prompt_variables(self, state):
        return {}

    def _update_state(self, result, state):
        return {}

    def _get_default_error_result(self):
        return ""


def make_node(inst, output_schema=None):
    """Build a minimal node backed by a mocked model instance."""
    return _MinimalLLMNode(
        logger=logger,
        label="test",
        llm_models={"chat": LLMModel(instance=inst, model_name="openai:gpt-4o")},
        output_schema=output_schema,
    )


def make_config(max_tokens=4096, model="gpt-4o", provider="openai"):
    """Build a per-invoke config in the shape _build_invoke_config returns."""
    return {
        "configurable": {
            "model": model,
            "model_provider": provider,
            "max_tokens": max_tokens,
        }
    }


class TestBuildInvokeConfigNoModel:
    """Tests for the role-aware 'no model configured' guard."""

    def test_empty_model_raises_clear_error(self):
        """An empty resolved model raises an actionable error."""
        node = _MinimalLLMNode(
            logger=logger,
            label="test",
            llm_models={
                "chat": LLMModel(instance=mock.Mock(), model_name="", required=True)
            },
            output_schema=None,
        )

        with (
            _runtime_context(),
            pytest.raises(RuntimeError, match="No model configured for role 'chat'"),
        ):
            node._build_invoke_config()

    def test_set_model_builds_config(self):
        """A resolved model proceeds past the guard."""
        node = _MinimalLLMNode(
            logger=logger,
            label="test",
            llm_models={
                "chat": LLMModel(
                    instance=mock.Mock(), model_name="openai:gpt-4o", required=True
                )
            },
            output_schema=None,
        )
        node._last_prompt = StringPromptValue(text="hi")

        with _runtime_context():
            config = node._build_invoke_config()
        assert config["configurable"]["model"] == "gpt-4o"

    def test_context_overrides_reach_merge(self):
        """Per-run overrides (ADR-0033 Runtime context) win in the merge."""
        node = _MinimalLLMNode(
            logger=logger,
            label="test",
            llm_models={
                "chat": LLMModel(
                    instance=mock.Mock(), model_name="openai:gpt-4o", required=True
                )
            },
            output_schema=None,
        )
        node._last_prompt = StringPromptValue(text="hi")

        with _runtime_context({"chat": {"model": "ollama:qwen3", "temperature": 0.5}}):
            config = node._build_invoke_config()
        assert config["configurable"]["model"] == "qwen3"
        assert config["configurable"]["temperature"] == 0.5


class TestInvokeWithRetries:
    """Tests for _invoke_with_retries via the plain (non-structured) path."""

    def setup_method(self):
        # No catalog data by default so tests are hermetic and offline.
        self._catalog_patcher = mock.patch(
            "klea_utils.llm.get_catalog_model_limits", return_value=None
        )
        self._catalog_patcher.start()

    def teardown_method(self):
        self._catalog_patcher.stop()

    async def _invoke(self, inst, config=None):
        node = make_node(inst)
        return await node._invoke_llm(
            inst, StringPromptValue(text="hi"), config or make_config()
        )

    async def test_context_overflow_retry_shrinks_window(self):
        """An overflow error retries once with a halved output window."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=[
                RuntimeError("Error code: 400 - context_length_exceeded"),
                AIMessage(content="ok", response_metadata={"finish_reason": "stop"}),
            ]
        )
        config = make_config(max_tokens=4096)
        out = await self._invoke(inst, config)

        assert inst.ainvoke.await_count == 2
        assert out.content == "ok"
        assert config["configurable"]["max_tokens"] == 2048

    async def test_context_overflow_exhausts_retries(self):
        """Persistent overflow raises after all shrink retries are used."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=RuntimeError("Error code: 400 - context_length_exceeded")
        )
        try:
            await self._invoke(inst)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")

        assert inst.ainvoke.await_count == 1 + MAX_CONTEXT_OVERFLOW_RETRIES

    async def test_rate_limited_no_retry(self):
        """Rate-limit errors surface immediately, as designed."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=RuntimeError("Error code: 429 - Rate limit reached")
        )
        try:
            await self._invoke(inst)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")

        assert inst.ainvoke.await_count == 1

    async def test_empty_output_retries_then_succeeds(self):
        """A blank response is re-invoked until the model returns content."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=[
                AIMessage(content="", response_metadata={"finish_reason": "stop"}),
                AIMessage(content="ok", response_metadata={"finish_reason": "stop"}),
            ]
        )
        out = await self._invoke(inst)

        assert inst.ainvoke.await_count == 2
        assert out.content == "ok"

    async def test_empty_output_exhausts_retries(self):
        """Persistent blank responses stop retrying and return as-is."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            return_value=AIMessage(
                content="  ", response_metadata={"finish_reason": "stop"}
            )
        )
        out = await self._invoke(inst)

        assert inst.ainvoke.await_count == 1 + MAX_EMPTY_OUTPUT_RETRIES
        assert out.content == "  "

    async def test_truncation_retry_grows_window(self):
        """A truncated output retries once with a linearly grown output window."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=[
                AIMessage(
                    content="partial", response_metadata={"finish_reason": "length"}
                ),
                AIMessage(content="full", response_metadata={"finish_reason": "stop"}),
            ]
        )
        config = make_config(max_tokens=4096)
        out = await self._invoke(inst, config)

        assert inst.ainvoke.await_count == 2
        assert out.content == "full"
        assert config["configurable"]["max_tokens"] == 4096 + TRUNCATION_LINEAR_STEP

    async def test_truncation_exception_retry_grows_window(self):
        """A raised truncation error (OpenAI streaming path) retries with a grown window."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=[
                RuntimeError(
                    "Could not parse response content as the length limit was reached"
                ),
                AIMessage(content="full", response_metadata={"finish_reason": "stop"}),
            ]
        )
        config = make_config(max_tokens=4096)
        out = await self._invoke(inst, config)

        assert inst.ainvoke.await_count == 2
        assert out.content == "full"
        assert config["configurable"]["max_tokens"] == 4096 + TRUNCATION_LINEAR_STEP

    async def test_truncation_exception_exhausts_retries(self):
        """Persistent raised truncation climbs to the ceiling, then re-raises.

        From 1024 the ladder steps +2048 through the linear phase up to
        16384, then +32768 to the fixed 32768 ceiling (9 rungs); at the
        ceiling the window cannot grow further, so the error surfaces.
        """
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=RuntimeError(
                "Could not parse response content as the length limit was reached"
            )
        )
        config = make_config(max_tokens=1024)
        try:
            await self._invoke(inst, config)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")

        # 1 original + 9 grow rungs (3072..32768); the ceiling cannot grow further.
        assert inst.ainvoke.await_count == 10
        assert config["configurable"]["max_tokens"] == MAX_OUTPUT_TOKENS_CEILING

    async def test_truncation_exception_linear_rungs_before_success(self):
        """Linear grow rungs, not a jump, precede a successful retry."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            side_effect=[
                RuntimeError(
                    "Could not parse response content as the length limit was reached"
                ),
                RuntimeError(
                    "Could not parse response content as the length limit was reached"
                ),
                RuntimeError(
                    "Could not parse response content as the length limit was reached"
                ),
                AIMessage(content="full", response_metadata={"finish_reason": "stop"}),
            ]
        )
        config = make_config(max_tokens=1024)
        out = await self._invoke(inst, config)

        # 1024 -> 3072 -> 5120 -> 7168 (linear) -> success.
        assert inst.ainvoke.await_count == 4
        assert out.content == "full"
        assert config["configurable"]["max_tokens"] == 1024 + 3 * TRUNCATION_LINEAR_STEP

    async def test_truncation_at_ceiling_no_more_retries(self):
        """Once the ceiling is reached, further truncation stops retrying."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            return_value=AIMessage(
                content="cut", response_metadata={"finish_reason": "length"}
            )
        )
        config = make_config(max_tokens=1024)
        out = await self._invoke(inst, config)

        # 1 original + 9 grow rungs to the 32768 ceiling; then no progress.
        assert inst.ainvoke.await_count == 10
        assert out.content == "cut"
        assert config["configurable"]["max_tokens"] == MAX_OUTPUT_TOKENS_CEILING

    async def test_truncation_grows_to_endpoint_ceiling(self):
        """The ladder climbs to the endpoint headroom, not the fixed ceiling."""
        with (
            mock.patch(
                "klea_utils.llm.probe_endpoint_model_limits",
                return_value=ModelLimits(context=262144, output=None, input=None),
            ),
            mock.patch(
                "klea_utils.nodes.base.probe_endpoint_model_limits",
                return_value=ModelLimits(context=262144, output=None, input=None),
            ),
        ):
            inst = mock.Mock()
            inst.ainvoke = mock.AsyncMock(
                side_effect=RuntimeError(
                    "Could not parse response content as the length limit was reached"
                )
            )
            # base_url present so the endpoint lookup applies; small prompt so
            # the endpoint headroom (~262k) is far above the 32768 fallback.
            config = make_config(max_tokens=2048)
            config["configurable"]["base_url"] = (
                "https://inf01.arc-llm.condenser.arc.ucl.ac.uk/v1/"
            )
            config["configurable"]["model_provider"] = "openai"
            node = make_node(inst)
            node._last_prompt = StringPromptValue(text="hi")
            try:
                await node._invoke_llm(inst, StringPromptValue(text="hi"), config)
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError")

        # Linear rungs to 16384, then +32768 rungs to the endpoint headroom
        # (262144 - 1 input token = 262143) at the 15th rung.
        assert inst.ainvoke.await_count == 1 + MAX_TRUNCATION_RETRIES
        assert config["configurable"]["max_tokens"] == 262143

    async def test_truncation_growth_not_capped_by_models_dev_output(self):
        """The endpoint context, not a lower models.dev output cap, bounds growth.

        The retry path uses the live endpoint for the context cap, so a
        models.dev output cap below the endpoint context must not under-cut
        the grow ladder.
        """
        with (
            mock.patch(
                "klea_utils.llm.probe_endpoint_model_limits",
                return_value=ModelLimits(context=262144, output=None, input=None),
            ),
            mock.patch(
                "klea_utils.nodes.base.probe_endpoint_model_limits",
                return_value=ModelLimits(context=262144, output=None, input=None),
            ),
            mock.patch(
                "klea_utils.llm.get_catalog_model_limits",
                return_value=ModelLimits(context=128000, output=4096),
            ),
        ):
            inst = mock.Mock()
            inst.ainvoke = mock.AsyncMock(
                side_effect=RuntimeError(
                    "Could not parse response content as the length limit was reached"
                )
            )
            config = make_config(max_tokens=2048)
            config["configurable"]["base_url"] = "https://example.com/v1/"
            config["configurable"]["model_provider"] = "openai"
            node = make_node(inst)
            node._last_prompt = StringPromptValue(text="hi")
            try:
                await node._invoke_llm(inst, StringPromptValue(text="hi"), config)
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError")

        # Grows well past the models.dev output cap of 4096, up to the
        # endpoint headroom.
        assert inst.ainvoke.await_count == 1 + MAX_TRUNCATION_RETRIES
        assert config["configurable"]["max_tokens"] == 262143
        assert config["configurable"]["max_tokens"] > MAX_OUTPUT_TOKENS_CEILING

    async def test_truncation_exception_at_catalog_cap_no_retry(self):
        """Raised truncation at the catalog output cap surfaces immediately."""
        with mock.patch(
            "klea_utils.llm.get_catalog_model_limits",
            return_value=ModelLimits(context=128000, output=4096),
        ):
            inst = mock.Mock()
            inst.ainvoke = mock.AsyncMock(
                side_effect=RuntimeError(
                    "Could not parse response content as the length limit was reached"
                )
            )
            config = make_config(max_tokens=4096)
            try:
                await self._invoke(inst, config)
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError")

        assert inst.ainvoke.await_count == 1
        assert config["configurable"]["max_tokens"] == 4096

    async def test_truncation_at_catalog_cap_no_retry(self):
        """Truncation at the catalog output cap does not waste a retry."""
        with mock.patch(
            "klea_utils.llm.get_catalog_model_limits",
            return_value=ModelLimits(context=128000, output=4096),
        ):
            inst = mock.Mock()
            inst.ainvoke = mock.AsyncMock(
                return_value=AIMessage(
                    content="cut", response_metadata={"finish_reason": "length"}
                )
            )
            config = make_config(max_tokens=4096)
            out = await self._invoke(inst, config)

        assert inst.ainvoke.await_count == 1
        assert out.content == "cut"
        assert config["configurable"]["max_tokens"] == 4096

    async def test_truncation_resolves_native_provider_endpoint(self):
        """A native provider without base_url resolves its endpoint for the probe."""
        captured = {}

        def fake_endpoint_lookup(provider, model, base_url, api_key=None):
            captured["provider"] = provider
            captured["base_url"] = base_url
            return ModelLimits(context=262144, output=None, input=None)

        with (
            mock.patch(
                "klea_utils.llm.probe_endpoint_model_limits",
                side_effect=fake_endpoint_lookup,
            ),
            mock.patch(
                "klea_utils.nodes.base.probe_endpoint_model_limits",
                side_effect=fake_endpoint_lookup,
            ),
        ):
            inst = mock.Mock()
            inst.ainvoke = mock.AsyncMock(
                side_effect=[
                    RuntimeError(
                        "Could not parse response content as the length limit was reached"
                    ),
                    AIMessage(
                        content="full", response_metadata={"finish_reason": "stop"}
                    ),
                ]
            )
            # instance._model(config) -> concrete Mistral model exposing .endpoint.
            inst._model = mock.Mock(
                return_value=mock.Mock(endpoint="https://api.mistral.ai/v1")
            )

            config = make_config(
                max_tokens=1024, model="mistral-small-latest", provider="mistralai"
            )
            node = make_node(inst)
            node._last_prompt = StringPromptValue(text="hi")
            out = await node._invoke_llm(inst, StringPromptValue(text="hi"), config)

        assert out.content == "full"
        assert config["configurable"]["base_url"] == "https://api.mistral.ai/v1"
        assert captured["base_url"] == "https://api.mistral.ai/v1"
        assert captured["provider"] == "mistralai"

    async def test_truncation_growth_uses_resolved_native_endpoint(self):
        """The ladder climbs to a native provider's resolved endpoint context."""
        captured = {}

        def fake_endpoint_lookup(provider, model, base_url, api_key=None):
            captured["provider"] = provider
            captured["base_url"] = base_url
            return ModelLimits(context=262144, output=None, input=None)

        with (
            mock.patch(
                "klea_utils.llm.probe_endpoint_model_limits",
                side_effect=fake_endpoint_lookup,
            ),
            mock.patch(
                "klea_utils.nodes.base.probe_endpoint_model_limits",
                side_effect=fake_endpoint_lookup,
            ),
        ):
            inst = mock.Mock()
            inst.ainvoke = mock.AsyncMock(
                side_effect=RuntimeError(
                    "Could not parse response content as the length limit was reached"
                )
            )
            inst._model = mock.Mock(
                return_value=mock.Mock(endpoint="https://api.mistral.ai/v1")
            )

            config = make_config(
                max_tokens=2048, model="mistral-small-latest", provider="mistralai"
            )
            node = make_node(inst)
            node._last_prompt = StringPromptValue(text="hi")
            try:
                await node._invoke_llm(inst, StringPromptValue(text="hi"), config)
            except RuntimeError:
                pass
            else:
                raise AssertionError("expected RuntimeError")

        assert captured["base_url"] == "https://api.mistral.ai/v1"
        assert captured["provider"] == "mistralai"
        assert config["configurable"]["max_tokens"] == 262143

    async def test_truncation_list_finish_reason(self):
        """List-form finish_reason (some providers) is handled."""
        inst = mock.Mock()
        inst.ainvoke = mock.AsyncMock(
            return_value=AIMessage(
                content="full",
                response_metadata={"finish_reason": ["stop"]},
            )
        )
        out = await self._invoke(inst)
        assert inst.ainvoke.await_count == 1
        assert out.content == "full"

    async def test_structured_output_fallback_on_rejection(self):
        """Structured-output rejection falls back to a plain invoke."""
        inst = mock.Mock()
        wrapped = mock.Mock()
        inst.with_structured_output.return_value = wrapped
        wrapped.ainvoke = mock.AsyncMock(
            side_effect=RuntimeError("response_format not supported")
        )
        inst.ainvoke = mock.AsyncMock(
            return_value=AIMessage(
                content="ok", response_metadata={"finish_reason": "stop"}
            )
        )

        node = make_node(inst, output_schema=_OutputSchema)
        config = make_config()
        out = await node._invoke_llm(inst, StringPromptValue(text="hi"), config)

        assert wrapped.ainvoke.await_count == 1
        assert inst.ainvoke.await_count == 1
        assert out.content == "ok"

    async def test_structured_output_non_rejection_propagates(self):
        """A non-rejection structured-path error is re-raised."""
        inst = mock.Mock()
        wrapped = mock.Mock()
        inst.with_structured_output.return_value = wrapped
        wrapped.ainvoke = mock.AsyncMock(
            side_effect=RuntimeError("Error code: 429 - rate limit")
        )
        inst.ainvoke = mock.AsyncMock(return_value=AIMessage(content="ok"))

        node = make_node(inst, output_schema=_OutputSchema)
        try:
            await node._invoke_llm(inst, StringPromptValue(text="hi"), make_config())
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected RuntimeError")

        assert wrapped.ainvoke.await_count == 1
        assert inst.ainvoke.await_count == 0

    async def test_empty_structured_output_retries_then_succeeds(self):
        """A blank structured response is re-invoked instead of failing.

        On the structured path the parser raises for a blank response rather
        than returning it, so the empty-retry budget must be applied on the
        exception path (HuggingFace intermittently blanks under rate limiting).
        """
        inst = mock.Mock()
        wrapped = mock.Mock()
        inst.with_structured_output.return_value = wrapped
        wrapped.ainvoke = mock.AsyncMock(
            side_effect=[
                OutputParserException(
                    "Invalid json output: \nFor troubleshooting, visit: "
                    "https://docs.langchain.com/oss/python/langchain/errors/"
                    "OUTPUT_PARSING_FAILURE "
                ),
                AIMessage(content='{"answer": "ok"}'),
            ]
        )

        node = make_node(inst, output_schema=_OutputSchema)
        out = await node._invoke_llm(inst, StringPromptValue(text="hi"), make_config())

        assert wrapped.ainvoke.await_count == 2
        assert out.content == '{"answer": "ok"}'

    async def test_empty_structured_output_exhausts_retries(self):
        """Persistent blank structured responses retry, then re-raise."""
        inst = mock.Mock()
        wrapped = mock.Mock()
        inst.with_structured_output.return_value = wrapped
        wrapped.ainvoke = mock.AsyncMock(
            side_effect=OutputParserException("Invalid json output: \n")
        )

        node = make_node(inst, output_schema=_OutputSchema)
        try:
            await node._invoke_llm(inst, StringPromptValue(text="hi"), make_config())
        except OutputParserException:
            pass
        else:
            raise AssertionError("expected OutputParserException")

        assert wrapped.ainvoke.await_count == 1 + MAX_EMPTY_OUTPUT_RETRIES

    async def test_nonempty_invalid_json_not_retried_as_empty(self):
        """A non-empty invalid payload is a schema mismatch, not an empty retry."""
        inst = mock.Mock()
        wrapped = mock.Mock()
        inst.with_structured_output.return_value = wrapped
        wrapped.ainvoke = mock.AsyncMock(
            side_effect=OutputParserException("Invalid json output: {not json}")
        )

        node = make_node(inst, output_schema=_OutputSchema)
        try:
            await node._invoke_llm(inst, StringPromptValue(text="hi"), make_config())
        except OutputParserException:
            pass
        else:
            raise AssertionError("expected OutputParserException")

        assert wrapped.ainvoke.await_count == 1


class _ValidatingNode(_MinimalLLMNode):
    """Node that rejects the first result then accepts (validation-retry test)."""

    model_type = "chat"
    max_validation_retries = 2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.calls = 0
        self.feedback_seen = []

    def _get_prompt_variables(self, state):
        self.feedback_seen.append(self._validation_feedback)
        return {"x": "y"}

    def _get_system_prompt(self, state):
        return "system"

    def _get_human_prompt(self, state):
        return "human"

    def _pre_exec_stream(self):
        pass

    def _post_exec_stream(self):
        pass

    async def _invoke_llm(self, llm, prompt, config):
        self.calls += 1
        return AIMessage(content="raw")

    def _process_output(self, output):
        return self.calls

    def _validate_result(self, result, state):
        # Reject the first result, accept the second.
        return "bad plan" if result == 1 else None

    def _update_state(self, result, state):
        return {"accepted": result}

    def _get_default_error_result(self):
        return 0


class TestValidationRetryLoop:
    """execute() re-invokes on a validation failure with feedback."""

    def setup_method(self):
        self._catalog_patcher = mock.patch(
            "klea_utils.llm.get_catalog_model_limits", return_value=None
        )
        self._catalog_patcher.start()

    def teardown_method(self):
        self._catalog_patcher.stop()

    async def test_retries_until_valid(self):
        node = _ValidatingNode(
            logger=logger,
            label="test",
            llm_models={
                "chat": LLMModel(instance=mock.Mock(), model_name="openai:gpt-4o")
            },
            output_schema=None,
        )
        with (
            _runtime_context(),
            mock.patch("klea_utils.nodes.base._current_session_id", return_value=None),
            mock.patch.object(
                node, "_configure_llm", return_value=(mock.Mock(), make_config())
            ),
        ):
            updates = await node.execute(_OutputSchema(answer="x"))

        assert node.calls == 2
        assert updates["accepted"] == 2
        # The retry saw the validation error as feedback.
        assert "bad plan" in node.feedback_seen

    async def test_gives_up_after_max_retries(self):
        class _AlwaysInvalid(_ValidatingNode):
            def _validate_result(self, result, state):
                return "still bad"

        node = _AlwaysInvalid(
            logger=logger,
            label="test",
            llm_models={
                "chat": LLMModel(instance=mock.Mock(), model_name="openai:gpt-4o")
            },
            output_schema=None,
        )
        with (
            _runtime_context(),
            mock.patch("klea_utils.nodes.base._current_session_id", return_value=None),
            mock.patch.object(
                node, "_configure_llm", return_value=(mock.Mock(), make_config())
            ),
        ):
            updates = await node.execute(_OutputSchema(answer="x"))

        # 1 original + max_validation_retries re-invokes.
        assert node.calls == 1 + _AlwaysInvalid.max_validation_retries
        # The final (still invalid) result is accepted fail-closed.
        assert updates["accepted"] == node.calls
