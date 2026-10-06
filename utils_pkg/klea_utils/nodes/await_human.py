#!/usr/bin/env python3
"""
Generic human-in-the-loop await node

File: klea_utils/nodes/await_human.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging
from typing import Any, Literal, final

from langgraph.types import interrupt
from pydantic import BaseModel, Field

from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)
from klea_utils.nodes.context import NodeContext


class _QuestionsResponse(BaseModel):
    """Default resume schema for a ``questions`` payload (free-text answers).

    ``answers`` is positional, aligned with the flattened question list; the
    reserved ``action`` cancels.
    """

    action: Literal["answer", "cancel"] = "answer"
    answers: list[str] = Field(default_factory=list)


class AwaitHumanNode[TState: BaseModel](
    AbstractLangGraphNode[TState, dict[str, Any], NodeContext]
):
    """Generic human-in-the-loop pause/resume node (ADR-0046).

    The node calls LangGraph ``interrupt()`` with an app-authored payload and
    resumes the **same** run when the client sends ``Command(resume=...)``.
    It owns the mechanism -- the interrupt, the resume parsing, the optional
    ``hitl_response_schema``, and the stream events -- while the app supplies
    the policy through four hooks:

    * :meth:`_ask` -- the JSON-serialisable payload shown to the user (it
      should carry a ``kind`` and the question or questions);
    * :meth:`_hitl_response_schema` -- an optional Pydantic model describing
      the expected resume payload, so clients can render a typed form (when
      not overridden and the payload carries ``questions``, a default
      free-text ``answers`` list schema is used);
    * :meth:`_on_answer` -- state updates for an answer;
    * :meth:`_on_cancel` -- state updates for a cancellation.

    A resume value is a mapping: ``action`` selects answer (default) or
    ``cancel``, and every other key is an answer.  Routing after an answer or
    a cancel is the app's job: this node only produces the state update.

    Reference: https://docs.langchain.com/oss/python/langgraph/interrupts
    """

    def __init__(self, logger: logging.Logger, label: str, *, kind: str = ""):
        """Initialise with a logger.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param kind: Short identifier for this ask (logged; also useful for
            the app's payload/hooks)
        """
        super().__init__(logger, label)
        self.kind = kind

    # --- App policy hooks ---------------------------------------------
    def _ask(self, state: TState) -> dict[str, Any]:
        """Return the interrupt payload shown to the user.  Override."""
        raise NotImplementedError

    def _hitl_response_schema(
        self, state: TState, payload: dict[str, Any]
    ) -> type[BaseModel] | None:
        """Return an optional Pydantic model for the resume payload."""
        return None

    @staticmethod
    def _questions_schema(
        questions: list[dict[str, Any]],
    ) -> type[BaseModel] | None:
        """Return the default free-text schema for a ``questions`` payload.

        The client answers the questions positionally in ``answers`` (or
        cancels); :meth:`_parse_response` and the app map them back.
        """
        return _QuestionsResponse if questions else None

    def _on_answer(self, state: TState, answers: dict[str, Any]) -> dict[str, Any]:
        """Return state updates for an answer."""
        return {}

    def _on_cancel(self, state: TState, payload: dict[str, Any]) -> dict[str, Any]:
        """Return state updates for a cancellation."""
        return {}

    # --- Mechanism ----------------------------------------------------
    @staticmethod
    def _parse_response(response: Any) -> tuple[str, dict[str, Any]]:
        """Return ``(action, answers)`` from an interrupt resume value.

        Accepts the validated Pydantic model (when a
        ``hitl_response_schema`` is set), a plain mapping, or a bare string
        (surfaced under ``answer``).
        ``action`` selects answer (default) or cancel; the remaining keys are
        the answers.

        :param response: The value passed to ``Command(resume=...)``
        :returns: ``(action, answers)``
        """
        if isinstance(response, BaseModel):
            data: dict[str, Any] = response.model_dump()
        elif isinstance(response, dict):
            data = dict(response)
        elif isinstance(response, str):
            data = {"answer": response}
        else:
            data = {}
        action = str(data.pop("action", "answer"))
        return action, data

    def _inspect(self, summary: str, details: dict[str, Any]) -> None:
        """Emit an ``inspect`` event summarising the resolved ask.

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

        The template method: subclasses override the four policy hooks, not
        this method.

        :param state: Current graph state
        :returns: State update produced by the answer/cancel hook
        """
        self._emit_progress()
        payload = self._ask(state)
        self.logger.debug(f"{self.kind = }\n{payload = }")

        schema = self._hitl_response_schema(state, payload)
        if schema is None:
            # No app schema: a ``questions`` payload gets a free-text form so
            # clients can render one field per question.
            questions = payload.get("questions")
            if isinstance(questions, list):
                schema = self._questions_schema(questions)
        # ``response_schema`` is LangGraph's interrupt kwarg; the app-facing
        # hook is ``_hitl_response_schema`` (see the class docstring).
        response = (
            interrupt(payload, response_schema=schema)
            if schema is not None
            else interrupt(payload)
        )
        action, answers = self._parse_response(response)
        self.logger.info("AwaitHumanNode[%s] resumed: action=%s", self.kind, action)

        if action == "cancel":
            update = dict(self._on_cancel(state, payload))
            self._inspect("Cancelled by user", {"payload": payload, "action": "cancel"})
            return update

        update = dict(self._on_answer(state, answers))
        self._inspect(
            "Human input received",
            {"payload": payload, "action": "answer", "answers": answers},
        )
        return update
