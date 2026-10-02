#!/usr/bin/env python3
"""
Initialise RAG state node

File: rag_pkg/klea_rag/nodes/init_rag.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

from klea_utils.nodes.abstract import AbstractLangGraphNode
from klea_utils.nodes.context import NodeContext

from klea_rag.schemas import EvaluateAnswerSchema, RAGState, RetrievalQueryOutput


class InitRAGState(AbstractLangGraphNode[RAGState, dict[str, Any], NodeContext]):
    """Initialise/reset RAG state before each iteration."""

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise with a logger and human-readable label."""
        super().__init__(logger, label)

    async def execute(self, state: RAGState) -> dict[str, Any]:
        """Reset state fields to their initial values."""
        self._emit_progress()
        return {
            "guard_decision": "safe",
            "text_response_eval": EvaluateAnswerSchema(),
            "message_for_user": "",
            "retrieval_attempts": 0,
            "rewrite_attempts": 0,
            "query_domains": ["undefined"],
            "retrieval_query": RetrievalQueryOutput(),
            "tool_calls": [],
            "tool_results": [],
            "reference_material": {},
        }
