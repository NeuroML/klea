#!/usr/bin/env python3
"""
Generate retrieval query node

File: rag_pkg/klea_rag/nodes/generate_retrieval_query.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from textwrap import dedent
from typing import Any, ClassVar, override

from klea_utils.llm import (
    extract_llm_output_content,
    prompt_value_to_messages,
)
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.stores.config import FilterFieldInfo
from klea_utils.stores.filters import normalize_config_filters
from langchain_core.messages import AIMessage

from klea_rag.schemas import RAGState, RetrievalQueryOutput


class GenerateRetrievalQuery(BaseLLMNode[RAGState, RetrievalQueryOutput]):
    """Node that generates a concise retrieval query from the user's question.

    Uses structured output (:class:`RetrievalQueryOutput`) so the search
    query and any retrieval constraints are produced together from the
    user's question.  Filter fields are deployment-configured per domain:
    the system prompt lists the configured ``filter_fields`` for the
    query's domains (so the model only ever proposes existing metadata
    keys), and :meth:`_update_state` validates and normalizes the raw
    ``filters`` operands into canonical DSL clauses.
    """

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        filter_fields_by_domain: dict[str, list[FilterFieldInfo]] | None = None,
    ):
        """Initialise the node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param filter_fields_by_domain: ``{domain: [FilterFieldInfo]}``
            configured for each domain; used to list the allowed filter
            fields in the prompt and to validate the model's ``filters``
            operands
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=RetrievalQueryOutput,
            memory=True,
        )
        self.filter_fields_by_domain: dict[str, list[FilterFieldInfo]] = (
            filter_fields_by_domain or {}
        )

    def _configured_filter_fields(self, state: RAGState) -> list[FilterFieldInfo]:
        """Return the filter fields allowed for the query's domains.

        The union of the configured fields across ``state.query_domains``,
        keeping the first definition of a name when domains disagree.  When
        no domain resolves a field set (e.g. the ``undefined`` domain), the
        union of all configured domains is used so a single-domain
        deployment still surfaces its fields.
        """
        allowed: dict[str, FilterFieldInfo] = {}
        for domain in state.query_domains:
            for field in self.filter_fields_by_domain.get(domain, []):
                allowed.setdefault(field.name, field)
        if not allowed:
            for fields in self.filter_fields_by_domain.values():
                for field in fields:
                    allowed.setdefault(field.name, field)
        return list(allowed.values())

    @staticmethod
    def _format_allowed_filter_fields(fields: list[FilterFieldInfo]) -> str:
        """Render the allowed filter fields as bullet lines for the prompt.

        Descriptions are inserted verbatim: values substituted into the chat
        template are not rescanned for ``{}`` placeholders, so a
        description containing braces (e.g. an operator expression like
        ``{'$gte': x}``) renders unchanged.
        """
        if not fields:
            return "(none configured)"
        lines = []
        for field in fields:
            lines.append(f"- {field.name} ({field.value_type}): {field.description}")
        return "\n".join(lines)

    @override
    def _get_system_prompt(self, state: RAGState) -> str | list[Any]:
        """Load system prompt, optionally appending evaluator feedback."""
        system_prompt = super()._get_system_prompt(state)

        if state.retrieval_attempts > 0:
            self.logger.info("Regenerating retrieval query, updating system prompt")
            new_sentence = dedent(
                """
                Generate a new concise retrieval query from the user's question. Think about the user's intent step by step.
                Take the evaluator's feedback into account.

                Previous query: {previous}

                Evaluator feedback:

                {feedback}

                """
            )
            if isinstance(system_prompt, list):
                # Memory enabled: super() returns a ("system", text) list plus
                # recent history; replace just the text part.
                text = system_prompt[0][1]
                _sentence, _newline, rest = text.partition("\n")
                system_prompt = list(system_prompt)
                system_prompt[0] = ("system", new_sentence + rest)
            else:
                _sentence, _newline, rest = system_prompt.partition("\n")
                system_prompt = new_sentence + rest
            self.logger.debug(f"New {system_prompt =}")

        return system_prompt

    @override
    def _get_prompt_variables(self, state: RAGState) -> dict:
        """Format prompt with user query and the domain's allowed filter fields."""
        return {
            "query": state.query,
            "feedback": state.text_response_eval.summary,
            "previous": state.retrieval_query.search_query,
            "allowed_filter_fields": self._format_allowed_filter_fields(
                self._configured_filter_fields(state)
            ),
        }

    @override
    def _update_state(
        self, result: RetrievalQueryOutput, state: RAGState
    ) -> dict[str, Any]:
        """Update state with the generated search query and filters.

        Always writes a fresh :class:`RetrievalQueryOutput` instance from
        the current LLM output, so nothing from a prior turn is carried
        over.  The raw ``filters`` operands (keyed by the domain's
        configured filter-field names) are validated and normalized into
        canonical DSL clauses on ``config_filters``; operands for undeclared
        fields are dropped with a warning (see
        :func:`klea_utils.stores.filters.normalize_config_filters`).
        """
        allowed = self._configured_filter_fields(state)
        result.config_filters = normalize_config_filters(result.filters, allowed)

        messages = [*state.messages, AIMessage(content=result.search_query)]

        return {
            "messages": messages,
            "retrieval_query": result,
        }

    @override
    def _get_default_error_result(self) -> RetrievalQueryOutput:
        """Return default result when processing fails."""
        self.logger.error("Processing failed")
        return RetrievalQueryOutput()

    @override
    def _get_inspect(self) -> NodeStreamData:
        """Return search query/filters plus prompt, raw output and feedback."""
        assert self._last_state is not None
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        assert self._last_state_updates is not None
        rq = self._last_state_updates.get("retrieval_query") or RetrievalQueryOutput()
        search_query = rq.search_query
        # Display-only: this node does not bump the counter (the retrieval
        # node does). Here it holds the number of prior retrieval passes, so
        # the current query generation is labelled as the next attempt.
        state = self._last_state
        attempt = state.retrieval_attempts + 1
        action = "Regenerated" if state.retrieval_attempts > 0 else "Generated"
        details = {
            "search_query": search_query,
            "metadata_filter": rq.to_metadata_filter(),
            "retrieval_attempts": attempt,
            "input_prompt": prompt_value_to_messages(self._last_prompt),
            "unprocessed_output": extract_llm_output_content(self._last_output),
            "processed_output": str(self._last_result),
        }
        # Add evaluator feedback if this is a retry
        if state.retrieval_attempts > 0 and state.text_response_eval:
            details["evaluator_feedback"] = state.text_response_eval.summary
            details["previous_query"] = state.retrieval_query.search_query
        return NodeStreamData(
            heading="Retrieval Query Generation",
            summary=f"{action} retrieval query (attempt {attempt})",
            details=details,
        )
