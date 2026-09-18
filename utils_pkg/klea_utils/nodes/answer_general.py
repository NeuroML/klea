#!/usr/bin/env python3
"""
Answer general question node

File: klea_utils/nodes/answer_general.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ..llm import (
    content_to_str,
    extract_llm_output_content,
    format_alert,
    prompt_value_to_messages,
    split_output_by_section,
)
from ..nodes.abstract import NodeStreamData
from .base import EMPTY_RESULT_FALLBACK, BaseLLMNode


class FallbackConfig(BaseModel):
    enabled: bool = False
    warning: str = ""


class AnswerGeneral(BaseLLMNode[BaseModel, BaseModel]):
    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
        "max_output_tokens": 2048,
    }
    """Answer general (non-domain) questions using the LLM's training data.

    Provides a conversational, user-friendly response. Optionally appends
    conversation history for context and a fallback warning when configured.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
        num_history_chars: int = 10_000,
        fallback_config: FallbackConfig | None = None,
    ):
        """Initialise the general answer node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include conversation history in the prompt
        :param num_history_chars: Character budget for the recent verbatim
            history messages injected between the system and human prompts.
        :param fallback_config: Optional config for fallback warning text
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=None,
            memory=memory,
        )

        self.num_history_chars = num_history_chars
        self.fallback_config = fallback_config

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with the user's query."""
        return {"query": state.query}  # type: ignore

    @override
    def _update_state(self, result: Any, state: BaseModel) -> dict[str, Any]:
        """Extract answer, append fallback warning if configured, update messages."""
        answer = ""

        # Add fallback warning if configured and query was domain-related.
        # The RAG stores classified domains in ``query_domains`` (a list);
        # a genuinely non-domain query is classified as ``["undefined"]``.
        # Warn only when a real domain matched, i.e. the query fell back to
        # training data after failed retrieval, not for plain general chat.
        # Default to ``["undefined"]`` so states without the attribute
        # (e.g. non-RAG graphs) never show the warning.
        fallback = self.fallback_config
        if fallback and fallback.enabled and fallback.warning:
            query_domains = getattr(state, "query_domains", ["undefined"])
            if "undefined" not in query_domains:
                answer += f"\n\n{format_alert(fallback.warning)}\n\n"

        content = content_to_str(result.content)
        _thought, answer_text = split_output_by_section(content, "<think>", "</think>")
        # An empty generation (the base retry budget was exhausted) must not
        # surface as a blank reply to the user; fall back to a clear message.
        if not answer_text.strip():
            answer_text = content_to_str(self._get_default_error_result().content)
        answer += answer_text

        messages = list(state.messages)  # type: ignore
        result.content = answer
        messages.append(result)

        return {"messages": messages, "message_for_user": answer}

    @override
    def _get_default_error_result(self) -> AIMessage:
        """Return the fallback message when the model produced no answer."""
        return AIMessage(content=EMPTY_RESULT_FALLBACK)

    @override
    def _get_inspect(self) -> NodeStreamData | None:
        """Return the answer summary plus input/output prompt data."""
        assert self._last_state_updates is not None
        assert self._last_prompt is not None
        assert self._last_output is not None
        result = content_to_str(self._last_state_updates.get("message_for_user", ""))
        char_count = len(result)
        return NodeStreamData(
            heading="General Answer",
            summary=f"Generated answer ({char_count} characters)"
            if char_count
            else "No answer generated",
            details={
                "character_count": char_count,
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
            },
        )
