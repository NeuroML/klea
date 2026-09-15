#!/usr/bin/env python3
"""
Schemas used in the RAG

File: klea_rag/schemas.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Any, Literal

from klea_utils.graph.state import BaseGraphSchema
from klea_utils.mcp.access import AccessLevel
from pydantic import BaseModel, Field


class EvaluateAnswerSchema(BaseModel):
    """Evaluation of LLM generated answer. Descriptions given in the main prompt"""

    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    coverage: float = Field(default=0.0, ge=0.0, le=1.0)
    relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    groundedness: float = Field(default=0.0, ge=0.0, le=1.0)
    coherence: float = Field(default=0.0, ge=0.0, le=1.0)
    conciseness: float = Field(default=0.0, ge=0.0, le=1.0)
    next_step: Literal[
        "continue", "retrieve_more_info", "modify_query", "rewrite_answer", "undefined"
    ] = Field(default="undefined", validate_default=True)
    summary: str = ""


class RetrievalQueryOutput(BaseModel):
    """Structured output of the retrieval-query generator.

    Holds the search query and the retrieval constraints the generator
    derives from the user's question.  All filter fields are
    deployment-configured: the generator emits an operand mapping,
    ``filters`` (keyed by the domain's ``filter_fields`` names), which the
    ``GenerateRetrievalQuery`` node validates and normalizes via
    ``klea_utils.stores.filters.normalize_config_filters`` into the
    canonical single-clause DSL dicts carried by ``config_filters``.  The
    backend-agnostic metadata filter is produced by
    :meth:`to_metadata_filter` from ``config_filters`` and consumed by
    the retrievers.
    """

    search_query: str = ""
    #: Filter operands exactly as the query generator produced them, keyed
    #: by the deployment's configured filter-field names.  This is the
    #: LLM-facing surface; it is not executed directly (the node
    #: normalizes it into ``config_filters``).
    filters: dict[str, Any] = Field(default_factory=dict)
    #: Canonical DSL clauses from the domain's ``filter_fields``
    #: configuration (see ``normalize_config_filters``).  Each entry is a
    #: single-clause dict (e.g. ``{"repository_type": {"$eq": "github"}}``
    #: or a top-level ``{"$and": [...]}``); merged into the metadata
    #: filter by :meth:`to_metadata_filter`.
    config_filters: list[dict[str, Any]] = Field(default_factory=list)

    def to_metadata_filter(self) -> dict[str, Any] | None:
        """Return the normalized metadata filter, or ``None``.

        Combines the validated configured-domain clauses
        (``config_filters``) into a single filter, wrapping several
        constraints in ``$and``.  ``filters`` is ignored here: the raw
        operands are only meaningful once normalized (which happens once,
        in the query-generation node).  Returns ``None`` when no
        constraint is set.
        """
        clauses: list[dict[str, Any]] = list(self.config_filters)
        if not clauses:
            return None
        if len(clauses) == 1:
            return clauses[0]
        return {"$and": clauses}


class RAGState(BaseGraphSchema):
    """The state of the graph

    Inherits the shared fields (query, messages, tool calls/results, usage
    metrics, summary/message fields) from
    :class:`klea_utils.graph.state.BaseGraphSchema` (ADR-0032) and adds the
    RAG-specific ones.
    """

    # schema for this is computed at run time for the classifier node
    query_domains: list[str] = Field(default=["undefined"], validate_default=True)
    text_response_eval: EvaluateAnswerSchema = EvaluateAnswerSchema()

    # Fixed by design (ADR-0037): the RAG retrieves, never mutates.
    access_level: AccessLevel = "read_only"

    # reference material from retrievals
    reference_material: dict[str, list[tuple]] = Field(default_factory=dict)

    # number of retrieval passes in the evaluator loop (the initial query
    # retrieval, retrieve_more_info k-increases, and modify_query
    # re-retrievals); incremented once per RetrieveInfoNode execution
    retrieval_attempts: int = 0

    # number of answer rewrite attempts in evaluator loop
    rewrite_attempts: int = 0

    # generated retrieval query (and any retrieval filters) for the
    # current round
    retrieval_query: RetrievalQueryOutput = RetrievalQueryOutput()
