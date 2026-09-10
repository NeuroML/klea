#!/usr/bin/env python3
"""
Classify question domain node

File: rag_pkg/klea_rag/nodes/classify_question.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import (
    extract_llm_output_content,
    prompt_value_to_messages,
)
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel

from klea_rag.schemas import RAGState


# Type is calculated at runtime in orchestrator
class ClassifyQuestion[TSchema: BaseModel](BaseLLMNode[RAGState, TSchema]):
    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
        "max_output_tokens": 2048,
    }
    """Classify a user query into domain categories.

    Uses an LLM to determine which domains the query belongs to, based on
    configured domain metadata. Appends conversation history to the system
    prompt when memory is enabled.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        domains: dict[str, str],
        output_schema: type[TSchema] | None,
        memory: bool = False,
        pre_prompt: str = "",
    ):
        """Initialise the classifier node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param domains: Domain name to description mapping
        :param output_schema: Pydantic schema for classification output
        :param memory: Whether to include conversation history in the prompt
        :param pre_prompt: Optional pre-prompt text for domain classification
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=output_schema,
            memory=memory,
        )
        self.domains = domains
        self.pre_prompt = pre_prompt

    def _build_domain_str(self) -> str:
        """Build the domain classification string from domain metadata."""
        domain_str = self.pre_prompt
        domain_str += "\n\n## Domains\n\n"

        for d, desc in self.domains.items():
            if not desc or len(desc) == 0:
                desc = f"If the question is about {d}"

            domain_str += f"\n### {d}\n{desc}"

        domain_str += (
            "\n### undefined\nUse 'undefined' only if no other domain applies.\n\n"
        )
        return domain_str

    @override
    def _get_system_prompt(self, state: RAGState) -> str | list[Any]:
        """Load base prompt, append domains, then rules, then optional memory."""
        system_prompt = self._load_prompt_file(f"{self.prompt_prefix}_system")

        # additional logic
        system_prompt += f"\n\n## Domains\n{self._build_domain_str()}\n\n"

        if self.memory:
            memory_addition = self._get_memory_addition(state)
            system_prompt += memory_addition

        # Schema goes last so it is the instruction closest to the human
        # query (recency), maximizing adherence to the JSON format.
        if self.output_schema:
            system_prompt += self._format_output_schema_prompt()

        if self.memory:
            # Mirror the base node: with memory enabled the system prompt is
            # a list of ("system", text) plus recent history messages.
            return [("system", system_prompt), *self._get_recent_memory_messages(state)]

        self.logger.debug(f"{system_prompt =}")
        return system_prompt

    @override
    def _get_prompt_variables(self, state: RAGState) -> dict:
        """Format prompt with the user's query."""
        return {"query": state.query}

    @override
    def _update_state(self, result: Any, state: RAGState) -> dict[str, Any]:
        """Extract classification result, append query to messages."""
        messages = list(state.messages)
        messages.append(HumanMessage(content=state.query))

        domains = result.query_domains

        # limit domains to valid ones
        valid_domains = []
        for d in domains:
            if d in self.domains:
                valid_domains.append(d)

        # if no valid domains, default to "undefined"
        if len(valid_domains) == 0:
            valid_domains.append("undefined")

        # if there are multiple domains, but "undefined" is also included,
        # remove it: we assume that the other domains are valid domains
        if len(valid_domains) > 1 and "undefined" in valid_domains:
            valid_domains.remove("undefined")

        return {
            "query_domains": valid_domains,
            "messages": messages,
        }

    @override
    def _get_info(self) -> NodeStreamData:
        """Return classification summary and details."""
        assert self._last_state_updates is not None
        classified = self._last_state_updates.get("query_domains", [])
        available = list(self.domains.keys())
        return NodeStreamData(
            heading="Question Classification",
            summary=f"Classified into: {', '.join(classified)} (from {len(available)} available domains)",
            details={
                "classified_domains": classified,
                "available_domains": available,
            },
        )

    @override
    def _get_debug(self) -> NodeStreamData:
        """Return info + input prompt, raw output, and processed output."""
        assert self._last_state is not None
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        info = self._get_info()
        details = info.details.copy()
        details.update(
            {
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            }
        )
        return NodeStreamData(
            heading=info.heading, summary=info.summary, details=details
        )

    @override
    def _get_default_error_result(self) -> Any:
        """Return default result when processing fails."""
        if self.output_schema is not None:
            try:
                return self.output_schema(query_domains=["undefined"])  # type: ignore[call-arg]
            except (TypeError, ValueError) as exc:
                self.logger.warning(f"Fallback schema instantiation failed: {exc}")
        return AIMessage(content="")
