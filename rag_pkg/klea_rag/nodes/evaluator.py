#!/usr/bin/env python3
"""
Evaluator node for RAG

File: rag_pkg/klea_rag/nodes/evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import extract_llm_output_content, prompt_value_to_messages
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.stores.utils import serialize_reference_material

from klea_rag.schemas import EvaluateAnswerSchema, RAGState


class Evaluator(BaseLLMNode[RAGState, EvaluateAnswerSchema]):
    """Node that evaluates a RAG-generated answer against retrieved context."""

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.0,
        "max_output_tokens": 4096,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
    ):
        """Initialise the evaluator node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=EvaluateAnswerSchema,
            memory=False,
        )

    @override
    def _get_prompt_variables(self, state: RAGState) -> dict:
        """Format prompt with question, context, and answer."""
        question = state.query
        context = serialize_reference_material(state.reference_material)
        answer = state.messages[-1].content
        if isinstance(answer, list):
            answer = "".join(
                b.get("text", "") if isinstance(b, dict) else str(b) for b in answer
            )

        return {
            "question": question,
            "context": context,
            "answer": answer,
        }

    @override
    def _update_state(
        self, result: EvaluateAnswerSchema, state: RAGState
    ) -> dict[str, Any]:
        """Update state with evaluation result and computed routing decision."""
        return {
            "text_response_eval": result,
        }

    @override
    def _get_default_error_result(self) -> EvaluateAnswerSchema:
        """Return default result when processing fails."""
        return EvaluateAnswerSchema(next_step="undefined", summary="Evaluation failed")

    @override
    def _get_inspect(self) -> NodeStreamData:
        """Return evaluation scores plus prompt and raw/processed output."""
        assert self._last_state is not None
        assert self._last_prompt is not None
        assert self._last_output is not None
        assert self._last_result is not None
        assert self._last_state_updates is not None
        eval_result = self._last_state_updates.get("text_response_eval")
        if eval_result is None:
            summary = "Evaluation failed"
            details = {}
        else:
            scores = {
                "confidence": eval_result.confidence,
                "coverage": eval_result.coverage,
                "relevance": eval_result.relevance,
                "groundedness": eval_result.groundedness,
                "coherence": eval_result.coherence,
                "conciseness": eval_result.conciseness,
            }
            summary = f"Evaluation complete: {eval_result.summary}"
            details = {
                "scores": scores,
                "next_step": eval_result.next_step,
                "summary": eval_result.summary,
            }
        details.update(
            {
                "input_prompt": prompt_value_to_messages(self._last_prompt),
                "unprocessed_output": extract_llm_output_content(self._last_output),
                "processed_output": str(self._last_result),
            }
        )
        return NodeStreamData(
            heading="Answer Evaluation", summary=summary, details=details
        )
