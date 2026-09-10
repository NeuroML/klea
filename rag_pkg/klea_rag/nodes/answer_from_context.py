#!/usr/bin/env python3
"""
Generate an answer from provided reference material

File: rag_pkg/klea_rag/nodes/answer_from_context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, ClassVar, override

from klea_utils.llm import (
    extract_llm_output_content,
    prompt_value_to_messages,
    split_output_by_section,
)
from klea_utils.nodes.abstract import NodeStreamData
from klea_utils.nodes.base import BaseLLMNode
from klea_utils.stores.utils import serialize_reference_material
from klea_utils.tools import textualize_tool_results
from langchain.messages import AIMessage
from pydantic import BaseModel, Field

from klea_rag.schemas import RAGState


class AnswerSchema(BaseModel):
    answer: str = ""
    references: list[str] = Field(default_factory=list)


class AnswerFromContext(BaseLLMNode[RAGState, AnswerSchema]):
    """Generate an answer from the provided context"""

    model_type = "chat"
    model_defaults: ClassVar[dict[str, Any]] = {
        "temperature": 0.3,
        "max_output_tokens": 4096,
    }

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        llm_models: dict[str, Any],
        memory: bool = False,
    ):
        """Initialise the node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param llm_models: ``{role: LLMModel}`` dict (from ``BaseLangGraph.llm_models``)
        :param memory: Whether to include conversation memory in the prompt
        """
        super().__init__(
            logger=logger,
            label=label,
            llm_models=llm_models,
            output_schema=AnswerSchema,
            memory=memory,
        )

    @override
    def _get_prompt_variables(self, state: RAGState) -> dict:
        """Format prompt with question and serialized reference material."""
        reference_material = state.reference_material
        reference_material_text = serialize_reference_material(reference_material)

        # Add tool results to the reference material (per-tool capped to avoid
        # starvation; no total cap — per-tool 2500 is the bound)
        if state.tool_results:
            tool_text = textualize_tool_results(
                state.tool_results,
                max_len_per_tool=2500,
            )
            reference_material_text += "\n" + tool_text

        return {
            "query": state.query,
            "reference_material": reference_material_text,
        }

    @override
    def _update_state(self, result: AnswerSchema, state: RAGState) -> dict[str, Any]:
        """Update state with the generated answer and formatted references."""
        _thought, answer = split_output_by_section(result.answer, "<think>", "</think>")
        refs = result.references

        full_answer = self._update_reference_list(answer, refs)
        res_message = AIMessage(content=full_answer)
        self.logger.debug(res_message.pretty_repr())

        messages = [*state.messages, res_message]

        is_rewrite = state.text_response_eval.next_step == "rewrite_answer"
        return {
            "messages": messages,
            "reference_material": state.reference_material,
            "rewrite_attempts": state.rewrite_attempts + 1
            if is_rewrite
            else state.rewrite_attempts,
        }

    def _update_reference_list(self, answer: str, references: list[str]) -> str:
        """Update answer with reference list

        Override with ``pass`` to skip reference listing entirely,
        or override with custom formatting logic.

        We rely on the LLM to generate an output with a reference list, since
        we want it to only list references that it used in the answer.

        :param answer: The answer returned from the LLM, with references
        :returns: answer text with formatted references if available
        """
        full_answer = f"{answer}"
        newrefs = list({r.strip() for r in references})

        if len(newrefs):
            full_answer += "\n\nReferences:\n\n"
            for r in newrefs:
                full_answer += f"- {r}\n"
            self.logger.debug(f"{full_answer = }")
        else:
            self.logger.debug("No references included.")

        return full_answer

    @override
    def _get_info(self) -> NodeStreamData:
        """Return answer generation summary."""
        assert self._last_state_updates is not None
        answer = ""
        refs = []
        result = self._last_result
        if isinstance(result, AnswerSchema):
            answer = result.answer
            refs = result.references
        preview = answer[:120] + "..." if len(answer) > 120 else answer
        return NodeStreamData(
            heading="Answer Generation",
            summary=f"Generated answer ({len(answer)} chars, {len(refs)} references)",
            details={
                "answer_preview": preview,
                "char_count": len(answer),
                "reference_count": len(refs),
                "references": refs,
            },
        )

    @override
    def _get_debug(self) -> NodeStreamData:
        """Return info + input prompt, raw output, and processed output."""
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
        return AnswerSchema(answer="", references=[])
