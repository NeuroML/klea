#!/usr/bin/env python3
"""
Custom errors.

File: utils_pkg/klea_utils/errors.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from enum import Enum


class LLMInitializationError(Exception):
    pass


class PromptTemplateError(Exception):
    pass


class ClientContextError(Exception):
    """A client-supplied runtime context is untrusted or malformed.

    Raised at the API boundary and mapped to a 4xx response; see
    :mod:`klea_utils.client_context`.

    :param message: Human-readable reason.
    :param status_code: HTTP status the API layer should use (``400`` for a
        malformed context, ``403`` for a non-loopback peer).
    """

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class LLMInvocationErrorCategory(str, Enum):
    """Classification of LLM invocation failures.

    Providers report errors inconsistently, so the classification is a
    best-effort heuristic driven by tolerant regex matching (see
    ``klea_utils.llm.classify_llm_invocation_error``).  The category
    drives retry behaviour in the LLM nodes: some categories are retried
    with adjusted parameters, others surface immediately.
    """

    CONTEXT_OVERFLOW = "context_overflow"
    LENGTH_TRUNCATION = "length_truncation"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    MODEL_NOT_FOUND = "model_not_found"
    TIMEOUT = "timeout"
    STRUCTURED_OUTPUT_REJECTED = "structured_output_rejected"
    UNKNOWN = "unknown"
