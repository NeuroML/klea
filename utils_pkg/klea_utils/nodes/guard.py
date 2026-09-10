#!/usr/bin/env python3
"""
Guard node for safety checking

File: klea_utils/nodes/guard.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from langchain_core.messages import AIMessage
from pydantic import BaseModel

from ..llm import content_to_str
from .base import BaseLLMNode


class GuardNode(BaseLLMNode[BaseModel, BaseModel]):
    model_type = "guard"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
        "max_output_tokens": 2048,
    }
    """Safety guard node that checks if user queries are safe to process.

    Evaluates whether a query contains potentially harmful content
    and returns a routing decision ("safe" or "unsafe").

    Note: to be used with llama-guard, which always returns safe/unsafe.

    To skip, do not set a model.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
    ):
        """Initialise the guard node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include conversation history in the prompt
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=None,
            memory=memory,
        )

    @override
    def _pre_exec(self, state: BaseModel) -> bool:
        """Skip execution if no guard model is configured."""
        return bool(self._llm_entry.model_name)

    @override
    def _get_prompt_variables(self, state: BaseModel) -> dict:
        """Format prompt with the user's query."""
        return {"query": state.query}  # type: ignore

    @override
    def _get_system_prompt(self, state: BaseModel) -> str:
        return ""

    @override
    def _update_state(self, result: AIMessage, state: BaseModel) -> dict[str, Any]:
        """Check result for safety and return routing decision."""
        self.logger.debug(f"{result = }")

        content = content_to_str(result.content)
        if "unsafe" in content:
            return {"guard_decision": "unsafe"}

        return {"guard_decision": "safe"}

    @override
    def _get_default_error_result(self) -> str:
        """Unused: no schema in this node."""
        return "safe"
