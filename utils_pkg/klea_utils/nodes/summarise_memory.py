#!/usr/bin/env python3
"""
Summarise conversation history node

See ``devdocs/adr/0018-message-memory.md``.

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
from .abstract import NodeStreamData, NodeStreamEvent
from .base import BaseLLMNode
from .context import NodeContext

#: Default char budget for the recent verbatim window that is kept out of
#: the summary.  Must match the base node's ``num_history_chars`` default so
#: the verbatim window in prompts and the window excluded here stay aligned.
_DEFAULT_NUM_HISTORY_CHARS = 10_000


class SummariseMemoryNode(BaseLLMNode[BaseModel, BaseModel]):
    model_role = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
    }
    """Node that summarises conversation history into a context summary.

    Uses _pre_exec() to skip execution if there isn't enough *old*
    conversation to summarise.  The most recent messages (within
    ``num_history_chars``) form the verbatim window that the prompt
    assembly injects as real messages, so this node summarises user-visible
    history up to that window.  Internal run history (``messages_internal``)
    is not injected verbatim into prompts; it is summarised from its own
    cursor to the end.  Does NOT append the summary to messages -- it is
    metadata, not a turn.

    Expects state to have the following fields:

    - messages: user-visible conversation messages
    - messages_internal: internal plan/evaluation/reasoning run history
    - summarised_till / summarised_internal_till: per-stream cursors
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
        announce: str = "",
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
        :param announce: A chat message to set as ``message_for_user`` when the
            node actually produces a summary (e.g. a manual ``/compact``
            confirmation).  Empty by default, so the automatic node never
            touches ``message_for_user`` (it runs after the answer and would
            clobber it); the outcome is always reported via ``_get_inspect``.
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
        self.announce = announce

    def _summarisation_window(self, state: BaseModel) -> tuple[str, int]:
        """Return ``(conversation, window_start)`` for this run.

        Derived from *state* on each call rather than stored on the shared
        node instance, so concurrent runs cannot clobber each other's
        summarisation window (see ADR-0045).
        """
        recent = get_recent_messages(
            state.messages,  # type: ignore
            self.num_history_chars,
        )
        window_start = len(state.messages) - len(recent)  # type: ignore
        conversation, _ = get_last_n_conversations(
            state.messages,  # type: ignore
            state.summarised_till,  # type: ignore
            window_start,
        )
        return conversation, window_start

    def _internal_window(self, state: BaseModel) -> tuple[str, int]:
        """Return ``(conversation, window_end)`` for internal run history.

        Internal messages (plan versions, evaluation verdicts, reasoning
        conclusions) are not injected verbatim into node prompts, so there is
        no recent-tail window to preserve: everything from the internal cursor
        to the end is summarised.

        :param state: Current graph state.
        :returns: The internal conversation text and the new internal cursor.
        """
        messages = list(getattr(state, "messages_internal", None) or [])
        cursor = int(getattr(state, "summarised_internal_till", 0) or 0)
        cursor = max(0, min(cursor, len(messages)))
        conversation, _ = get_last_n_conversations(messages, start=cursor)
        return conversation, len(messages)

    @override
    def _pre_exec(self, state: BaseModel, ctx: NodeContext) -> bool:
        """Skip if not enough old conversation to summarise.

        A skip is reported to the inspection pane (so the user can see the node
        ran and why nothing happened); the run continues to END either way.
        """
        conversation, window_start = self._summarisation_window(state)
        internal, internal_end = self._internal_window(state)
        internal_cursor = int(getattr(state, "summarised_internal_till", 0) or 0)
        no_user_history = window_start <= state.summarised_till  # type: ignore
        no_internal_history = internal_end <= internal_cursor

        if no_user_history and no_internal_history:
            self.logger.debug("No new history to summarise yet")
            self._emit_memory_inspect(
                "Nothing new to summarise",
                {
                    "summarised_till": state.summarised_till,  # type: ignore
                    "window_start": window_start,
                    "summarised_internal_till": internal_cursor,
                    "internal_end": internal_end,
                },
            )
            return False

        combined_chars = len(conversation) + len(internal)
        if combined_chars < self.summarisation_threshold_chars:
            self.logger.debug(
                f"Not enough conversation to summarise yet: "
                f"{combined_chars}/{self.summarisation_threshold_chars} chars"
            )
            self._emit_memory_inspect(
                "Not enough old history to summarise yet",
                {
                    "conversation_chars": len(conversation),
                    "internal_chars": len(internal),
                    "threshold_chars": self.summarisation_threshold_chars,
                    "summarised_till": state.summarised_till,  # type: ignore
                    "window_start": window_start,
                    "summarised_internal_till": internal_cursor,
                    "internal_end": internal_end,
                },
            )
            return False
        return True

    def _emit_memory_inspect(self, summary: str, details: dict[str, Any]) -> None:
        """Emit an ``inspect`` entry describing a summarisation decision.

        Used for the skip paths (``_get_inspect`` covers the run path).  Guarded
        because ``_pre_exec`` is called directly by unit tests outside a graph
        run, where no stream writer exists.
        """
        event = NodeStreamEvent(
            type="inspect",
            node=self.label,
            data=NodeStreamData(heading="Memory", summary=summary, details=details),
        )
        try:
            self.write_custom_stream(event.model_dump())
        except RuntimeError:
            pass

    @override
    def _get_prompt_variables(self, state: BaseModel, ctx: Any) -> dict:
        """Format prompt with conversation data.

        ``context_summary`` is empty on the first summarisation, so the
        previous-summary section is composed as an optional block and omitted
        entirely when there is none (prompt conventions).
        """
        conversation, _ = self._summarisation_window(state)
        internal, _ = self._internal_window(state)
        return {
            "old_summary_block": self._optional_section(
                "Current summary",
                state.context_summary,  # type: ignore
            ),
            "conversation": conversation,
            "internal_history": self._optional_section(
                "Internal run history (decisions and progress)", internal
            ),
        }

    @override
    def _update_state(self, result: Any, state: BaseModel, ctx: Any) -> dict[str, Any]:
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
        _conversation, window_start = self._summarisation_window(state)
        _internal, internal_end = self._internal_window(state)
        updates: dict[str, Any] = {
            "context_summary": answer,
            "summarised_till": window_start,
        }
        if hasattr(state, "summarised_internal_till"):
            updates["summarised_internal_till"] = internal_end
        if self.announce:
            # A manual compaction confirmation (the automatic node sets no
            # ``announce``, so it never clobbers the run's answer).
            updates["message_for_user"] = self.announce
        return updates

    @override
    def _get_inspect(self, state: BaseModel, ctx: Any) -> NodeStreamData:
        """Report the summarisation to the inspection pane.

        The summary is internal memory metadata, not a chat turn, so it is
        surfaced as an ``inspect`` entry rather than written to
        ``message_for_user`` (which, as the last node, would clobber the run's
        answer).  ``_pre_exec`` covers the skip paths; this covers a run that
        produced a summary (or a blank one).
        """
        updates = getattr(ctx, "state_updates", None) or {}
        summary = updates.get("context_summary", "")
        if not summary:
            return NodeStreamData(
                heading="Memory",
                summary="Summarisation produced no usable summary",
                details={
                    "summarised_till": state.summarised_till,  # type: ignore
                    "summarised_internal_till": getattr(
                        state, "summarised_internal_till", 0
                    ),
                    "note": "the model returned blank output; state preserved",
                },
            )
        internal_cursor = updates.get("summarised_internal_till")
        progress = f"user index {updates.get('summarised_till')}"
        if internal_cursor is not None:
            progress += f" and internal index {internal_cursor}"
        return NodeStreamData(
            heading="Memory",
            summary=f"Summarised memory up to {progress} ({len(summary)} chars)",
            details={
                "summary": summary,
                "summarised_till": updates.get("summarised_till"),
                "summarised_internal_till": internal_cursor,
                "summary_chars": len(summary),
            },
        )

    # TODO: may need updating
    @override
    def _get_default_error_result(self, ctx: Any) -> AIMessage:
        """Return default result when processing fails."""
        return AIMessage(content="")
