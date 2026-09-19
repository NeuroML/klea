#!/usr/bin/env python3
"""
Summarise conversation history node

File: klea_utils/nodes/summarise_memory.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ..llm import (
    content_to_str,
    get_last_n_conversations,
    get_recent_messages,
    split_output_by_section,
)
from .base import BaseLLMNode

#: Default char budget for the recent verbatim window that is kept out of
#: the summary.  Must match the base node's ``num_history_chars`` default so
#: the verbatim window in prompts and the window excluded here stay aligned.
_DEFAULT_NUM_HISTORY_CHARS = 10_000


class SummariseMemoryNode(BaseLLMNode[BaseModel, BaseModel]):
    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
        "max_output_tokens": 4096,
    }
    """Node that summarises conversation history into a context summary.

    Uses _pre_exec() to skip execution if there isn't enough *old*
    conversation to summarise.  The most recent messages (within
    ``num_history_chars``) form the verbatim window that the prompt
    assembly injects as real messages, so this node only summarises history
    up to that window -- the summary and the verbatim window never overlap.
    Does NOT append the summary to messages -- it's metadata, not a turn.

    Expects state to have the following fields:

    - messages: list of messages
    - summarised_till: index of messages that have been summarised already
    - context_summary: previous memory/context summary

    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        summarisation_threshold_chars: int = 10_000,
        num_history_chars: int = _DEFAULT_NUM_HISTORY_CHARS,
        memory: bool = False,
    ):
        """Initialise the summarisation node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param summarisation_threshold_chars: Minimum characters of old
            conversation (before the recent verbatim window) before
            summarising.  ``0`` summarises as soon as there is any old
            history.
        :param num_history_chars: Character budget for the recent verbatim
            window kept out of the summary.
        :param memory: Whether to include conversation history in the prompt
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=None,
            memory=memory,
        )

        self.summarisation_threshold_chars = summarisation_threshold_chars
        self.num_history_chars = num_history_chars
        self.conversation = ""
        self._window_start = 0

    @override
    def _pre_exec(self, state: BaseModel) -> bool:
        """Skip if not enough old conversation to summarise."""
        recent = get_recent_messages(
            state.messages,  # type: ignore
            self.num_history_chars,
        )
        self._window_start = len(state.messages) - len(recent)  # type: ignore
        self.conversation, _ = get_last_n_conversations(
            state.messages,  # type: ignore
            state.summarised_till,  # type: ignore
            self._window_start,
        )

        if self._window_start <= state.summarised_till:  # type: ignore
            self.logger.debug("No new history to summarise yet")
            return False

        if len(self.conversation) < self.summarisation_threshold_chars:
            self.logger.debug(
                f"Not enough conversation to summarise yet: "
                f"{len(self.conversation)}/{self.summarisation_threshold_chars} chars"
            )
            return False
        return True

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with conversation data.

        ``context_summary`` is empty on the first summarisation, so the
        previous-summary section is composed as an optional block and omitted
        entirely when there is none (prompt conventions).
        """
        return {
            "old_summary_block": self._optional_section(
                "Current summary",
                state.context_summary,  # type: ignore
            ),
            "conversation": self.conversation,
        }

    @override
    def _update_state(self, result: Any, state: BaseModel) -> dict[str, Any]:
        """Extract summary from raw AIMessage output.

        A blank response is not a usable summary.  Advancing
        ``summarised_till`` on it would replace the existing
        ``context_summary`` with an empty string and drop the old
        conversation from the memory window, so preserve the existing state
        instead (return ``{}``): the next invocation retries the same window.
        """
        self.logger.debug(f"Current history summary is:\n{result.content}")
        content = content_to_str(result.content)
        _, answer = split_output_by_section(content, "<think>", "</think>")
        if not answer.strip():
            self.logger.warning(
                "Summarisation produced no usable content; preserving the "
                "existing summary and leaving the window unsummarised"
            )
            return {}
        return {
            "context_summary": answer,
            "summarised_till": self._window_start,
        }

    # TODO: may need updating
    @override
    def _get_default_error_result(self) -> AIMessage:
        """Return default result when processing fails."""
        return AIMessage(content="")
