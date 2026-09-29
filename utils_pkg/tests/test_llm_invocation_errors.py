#!/usr/bin/env python3
"""
Tests for LLM invocation error classification and token-param mapping.

File: tests/test_llm_invocation_errors.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import unittest

from klea_utils.errors import LLMInvocationErrorCategory
from klea_utils.llm import classify_llm_invocation_error, get_token_limit_param


class TestTokenLimitParam(unittest.TestCase):
    """Tests for get_token_limit_param provider mapping."""

    def test_huggingface(self):
        # ChatHuggingFace exposes max_tokens (mapped to max_new_tokens on
        # the underlying HF endpoint).
        self.assertEqual(get_token_limit_param("huggingface"), "max_tokens")

    def test_ollama(self):
        self.assertEqual(get_token_limit_param("ollama"), "num_predict")

    def test_openai(self):
        self.assertEqual(get_token_limit_param("openai"), "max_tokens")

    def test_anthropic(self):
        self.assertEqual(get_token_limit_param("anthropic"), "max_tokens")

    def test_custom(self):
        self.assertEqual(get_token_limit_param("custom"), "max_tokens")

    def test_empty_provider(self):
        self.assertEqual(get_token_limit_param(""), "max_tokens")


class TestClassifyInvocationError(unittest.TestCase):
    """Tests for classify_llm_invocation_error."""

    def _assert_category(self, category, message):
        exc = RuntimeError(message)
        self.assertEqual(
            classify_llm_invocation_error(exc),
            category,
            f"message={message!r}",
        )

    # --- context overflow ---

    def test_context_overflow_messages(self):
        messages = [
            (
                "This model's maximum context length is 8192 tokens. "
                "However, you requested 9000 tokens."
            ),
            "Error: context_length_exceeded: input tokens 5000 > max 4096",
            "prompt is too long",
            "Your input is too long. The maximum length is 4096 tokens.",
            "Too many tokens. The current input length is 9000 tokens.",
            "Token limit exceeded: please reduce the size of the request",
            "The model has generated 9000 tokens and the maximum length is 8192",
            (
                "Input validation error: `inputs` tokens + `max_new_tokens` "
                "tokens must be <= 32768 tokens."
            ),
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.CONTEXT_OVERFLOW, msg)

    def test_context_overflow_chained_exception(self):
        """The cause chain is unwrapped when LangChain wraps the error."""
        inner = RuntimeError("Error code: 400 - context_length_exceeded")
        wrapped = ValueError("Failed to invoke chat model")
        wrapped.__cause__ = inner
        self.assertEqual(
            classify_llm_invocation_error(wrapped),
            LLMInvocationErrorCategory.CONTEXT_OVERFLOW,
        )

    # --- length truncation (raised, e.g. OpenAI streaming path) ---

    def test_length_truncation_messages(self):
        messages = [
            "Could not parse response content as the length limit was reached",
            (
                "Could not parse response content as the length limit was "
                "reached - 4096 tokens used"
            ),
            "LengthFinishReasonError: finish_reason is 'length'",
            "APIError: response was truncated at finish_reason: length",
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.LENGTH_TRUNCATION, msg)

    def test_context_overflow_still_outranks_truncation(self):
        """Context-overflow messages must not be stolen by the truncation heuristics."""
        msg = (
            "This model's maximum context length is 8192 tokens. "
            "However, you requested 9000 tokens."
        )
        self._assert_category(LLMInvocationErrorCategory.CONTEXT_OVERFLOW, msg)

    # --- rate limited ---

    def test_rate_limited_messages(self):
        messages = [
            "Error code: 429 - {'error': {'message': 'Rate limit reached', ...}}",
            "RateLimitError: You have exceeded your rate limit",
            "Too many requests, please try again later",
            (
                "You exceeded your current quota. Please check your plan and "
                "billing details."
            ),
            "rate_limit_exceeded for model gpt-4o",
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.RATE_LIMITED, msg)

    # --- auth ---

    def test_auth_messages(self):
        messages = [
            "401 Unauthorized: Incorrect API key provided",
            "Error: 403 Forbidden: you do not have access",
            "AuthenticationError: The api_key client option must be set",
            "Invalid API key: sk-1234",
            "Unauthorized: missing or invalid authentication credentials",
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.AUTH_FAILED, msg)

    # --- model not found ---

    def test_model_not_found_messages(self):
        messages = [
            "404: The model 'gpt-5' does not exist",
            "ModelNotFoundError: model not found: meta-llama/Llama-3-8B",
            "The model does not exist or you do not have access",
            "A valid model was not found",
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.MODEL_NOT_FOUND, msg)

    # --- timeout ---

    def test_timeout_messages(self):
        messages = [
            "httpx.ReadTimeout: timed out",
            "Request timed out after 30 seconds",
            "Connection timed out",
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.TIMEOUT, msg)

    def test_timeout_by_exception_type_without_token(self):
        """A timeout class whose message has no "timeout" token is caught."""
        exc = TimeoutError("")
        self.assertEqual(
            classify_llm_invocation_error(exc), LLMInvocationErrorCategory.TIMEOUT
        )

        class StreamChunkTimeoutError(Exception):
            """Mirrors langchain-openai's class (message has no token)."""

        exc2 = StreamChunkTimeoutError("No streaming chunk received for 120.0s")
        self.assertEqual(
            classify_llm_invocation_error(exc2), LLMInvocationErrorCategory.TIMEOUT
        )

    def test_timeout_in_cause_chain_by_type(self):
        """A typed timeout nested under a wrapper is still classified."""
        StreamChunkTimeoutError = type("StreamChunkTimeoutError", (Exception,), {})
        wrapped = RuntimeError("invoke failed")
        wrapped.__cause__ = StreamChunkTimeoutError("no chunk")
        self.assertEqual(
            classify_llm_invocation_error(wrapped),
            LLMInvocationErrorCategory.TIMEOUT,
        )

    # --- structured output rejected ---

    def test_structured_output_rejected_messages(self):
        messages = [
            (
                "Error code: 400 - BadRequestError: this model does not "
                "support response_format"
            ),
            "BadRequestError: json_schema is not supported by this endpoint",
            "Structured output failed: model does not support structured output",
            (
                "Error code: 400 - {'error': {'message': 'invalid_request_error: "
                "response_format is not supported'}}"
            ),
        ]
        for msg in messages:
            self._assert_category(
                LLMInvocationErrorCategory.STRUCTURED_OUTPUT_REJECTED, msg
            )

    def test_context_overflow_with_invalid_request_error(self):
        """Context overflow outranks the structured-output heuristic.

        OpenAI reports context-length errors with ``invalid_request_error``
        too; those must stay CONTEXT_OVERFLOW, not be misclassified as a
        structured-output rejection.
        """
        msg = (
            "Error code: 400 - {'error': {'message': 'This model's maximum "
            "context length is 8192 tokens. However, you requested 9000 "
            "tokens.', 'type': 'invalid_request_error'}}"
        )
        self._assert_category(LLMInvocationErrorCategory.CONTEXT_OVERFLOW, msg)

    # --- unknown ---

    def test_unknown_messages(self):
        messages = [
            "Some completely unrelated error",
            "502 Bad Gateway",
            "ValueError: invalid literal for int() with base 10",
        ]
        for msg in messages:
            self._assert_category(LLMInvocationErrorCategory.UNKNOWN, msg)


if __name__ == "__main__":
    unittest.main()
