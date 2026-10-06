#!/usr/bin/env python3
"""
Generic human-in-the-loop await node

File: klea_utils/nodes/await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, final

from langgraph.types import interrupt
from pydantic import BaseModel

from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)
from klea_utils.nodes.context import NodeContext


class AwaitHumanNode[TState: BaseModel](
    AbstractLangGraphNode[TState, dict[str, Any], NodeContext]
):
    """Generic human-in-the-loop pause/resume node (ADR-0046).

    The node calls LangGraph ``interrupt()`` with ``{"kind", "question"}``.
    The app resumes the **same** run with
    ``Command(resume={"action": "answer", "text": ...})`` or
    ``Command(resume={"action": "cancel"})``.  This class owns the mechanism
    -- the interrupt, the resume-sentinel parsing, the stream events, and the
    answer-field envelope -- while the app supplies the policy through three
    hooks:

    * :meth:`_question` -- the ask presented to the user;
    * :meth:`_on_answer` -- extra state updates for an answer (typically
      conversation history);
    * :meth:`_on_cancel` -- state updates marking a cancellation.

    The template writes :attr:`answer_field` (the answer text, or ``""`` on a
    cancel), so a subclass never sets it directly.  Routing after a cancel is
    the app's job: this node only produces the state update.

    Reference: https://docs.langchain.com/oss/python/langgraph/interrupts
    """

    def __init__(
        self, logger: logging.Logger, label: str, *, kind: str, answer_field: str
    ):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param kind: Which ask this instance presents (carried in the payload)
        :param answer_field: State field the answer is written to
        """
        super().__init__(logger, label)
        self.kind = kind
        self.answer_field = answer_field

    # --- App policy hooks ---------------------------------------------
    def _question(self, state: TState) -> str:
        """Return the ask to present to the user.  Override in the app."""
        raise NotImplementedError

    def _on_answer(self, state: TState, text: str) -> dict[str, Any]:
        """Return extra state updates for an answer (no ``answer_field``)."""
        return {}

    def _on_cancel(self, state: TState, question: str) -> dict[str, Any]:
        """Return state updates marking a cancellation (no ``answer_field``)."""
        return {}

    # --- Mechanism ----------------------------------------------------
    @staticmethod
    def _parse_response(response: Any) -> tuple[str, str]:
        """Return ``(action, text)`` from an interrupt resume value.

        The API sends ``{"action": "answer"|"cancel", "text": ...}``; a bare
        string is treated as an answer so a simple client/test can resume
        without the sentinel envelope.

        :param response: The value passed to ``Command(resume=...)``
        :returns: ``(action, text)`` where action is ``answer`` or ``cancel``
        """
        if isinstance(response, dict):
            action = str(response.get("action", "answer"))
            return action, str(response.get("text", ""))
        if response is None:
            return "answer", ""
        return "answer", str(response)

    def _inspect(self, summary: str, details: dict[str, Any]) -> None:
        """Emit an ``inspect`` event summarising the resolved question.

        :param summary: One-line summary for the inspector
        :param details: Structured detail payload
        """
        info = NodeStreamData(heading=self.label, summary=summary, details=details)
        self.write_custom_stream(
            NodeStreamEvent(type="inspect", node=self.label, data=info).model_dump()
        )

    @final
    async def execute(self, state: TState) -> dict[str, Any]:
        """Pause for user input; resume with the answer or a cancel.

        The template method: subclasses override the three policy hooks, not
        this method.

        :param state: Current graph state
        :returns: State update carrying the answer, or the cancellation
        """
        self._emit_progress()
        question = self._question(state)
        self.logger.debug(f"{self.kind = }\n{question = }")

        response = interrupt({"kind": self.kind, "question": question})
        action, text = self._parse_response(response)
        self.logger.info("AwaitHumanNode[%s] resumed: action=%s", self.kind, action)

        if action == "cancel":
            update = dict(self._on_cancel(state, question))
            update[self.answer_field] = ""
            self._inspect(
                "Cancelled by user", {"question": question, "action": "cancel"}
            )
            return update

        update = dict(self._on_answer(state, text))
        update[self.answer_field] = text
        self._inspect(
            "Human input received",
            {"question": question, "action": "answer", self.answer_field: text},
        )
        return update
