#!/usr/bin/env python3
"""
Route evaluator node

File: rag_pkg/klea_rag/nodes/route_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.llm import content_to_str
from klea_utils.nodes.abstract import (
    AbstractRouterNode,
    NodeStreamData,
    NodeStreamEvent,
)
from klea_utils.stores.retrieval.base import BaseKleaRetriever

from klea_rag.schemas import RAGState


class RouteEvaluator(AbstractRouterNode):
    """Route based on Evaluator node results"""

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        retrievers: list[BaseKleaRetriever] | None = None,
        max_retrieval_attempts: int = 2,
        max_rewrite_attempts: int = 1,
        fallback_to_training_data: bool = False,
    ):
        """Initialise the evaluator node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param retrievers: Retrievers whose k is incremented/reset when
            routing between retrieval attempts
        :param max_retrieval_attempts: Combined budget for retrieval passes
            in the evaluator loop (the initial query retrieval, retrieve_more_info
            k-increases, and modify_query re-retrievals)
        :param max_rewrite_attempts: Max answer rewrites
        :param fallback_to_training_data: Whether to fall back to LLM training data
        """
        super().__init__(logger, label)
        self.retrievers = retrievers or []
        self.max_retrieval_attempts = max_retrieval_attempts
        self.max_rewrite_attempts = max_rewrite_attempts
        self.fallback_to_training_data = fallback_to_training_data

    def execute(self, state: RAGState):
        """Route based on state, set by evaluator node."""
        self.write_custom_stream({"type": "progress", "node": self.label})
        self.logger.debug(f"{state =}")
        resp = state.text_response_eval
        next_step = resp.next_step

        # Determine route
        route = None

        # good answer: give it to user
        if next_step == "continue" and (
            resp.coverage >= 0.5
            and resp.confidence >= 0.5
            and resp.relevance >= 0.5
            and resp.groundedness >= 0.5
            and resp.coherence >= 0.5
            and resp.conciseness >= 0.5
        ):
            if self.retrievers:
                for retriever in self.retrievers:
                    retriever.reset_k()
            self.logger.debug("returning: continue")
            route = "continue"
        # not a good answer: something needs to be done
        else:
            # a) try to retrieve more information
            if (
                state.retrieval_attempts < self.max_retrieval_attempts
                and self.retrievers
            ):
                # we need to modify the query
                if next_step == "modify_query" or resp.coverage < 0.3:
                    self.logger.debug("returning: modify_query")
                    route = "modify_query"
                # we need to retrieve more info
                elif next_step == "retrieve_more_info" or (
                    resp.coverage >= 0.5 and resp.confidence < 0.5
                ):
                    # limit what max k we can have, otherwise, we end up pulling the
                    # whole store..  If no store can grow k, fall back to a new query.
                    if any(r.can_inc_k() for r in self.retrievers):
                        self.logger.debug("returning: retrieve_more_info")
                        route = "retrieve_more_info"
                    else:
                        # else fallback to a new query
                        self.logger.debug("returning: modify_query")
                        route = "modify_query"

            # b) rewrite answer
            if (
                route is None
                and state.rewrite_attempts < self.max_rewrite_attempts
                and (
                    next_step == "rewrite_answer"
                    or (
                        resp.coverage >= 0.5
                        and resp.confidence >= 0.5
                        and (
                            resp.relevance < 0.5
                            or resp.groundedness < 0.5
                            or resp.coherence < 0.5
                            or resp.conciseness < 0.5
                        )
                    )
                )
            ):
                self.logger.debug("returning: rewrite_answer")
                route = "rewrite_answer"

        # Every budget is exhausted: decide how to close out.  If the context
        # is still insufficient (low coverage or confidence), fall back to
        # training data (or clarification); if the answer is ungrounded or
        # empty, ask for clarification rather than serving it; otherwise
        # deliver the best-effort grounded answer with a warning.
        if route is None:
            # context is insufficient: rewriting will not improve the answer
            # -> fall back to training data, or ask for clarification
            if resp.coverage < 0.3 or resp.confidence < 0.3:
                route = "fallback" if self.fallback_to_training_data else "undefined"
            # in addition to being incomplete (no case above applied), it is
            # also ungrounded (i.e., hallucinated)
            # OR the message is empty
            # -> ask for clarification
            elif (
                resp.groundedness < 0.3
                or not state.messages
                or not content_to_str(state.messages[-1].content).strip()
            ):
                route = "undefined"
            # cannot be improved, but is fairly covered/confident/grounded
            # -> return with warning
            else:
                route = "best_effort"
            self.logger.debug(f"returning: {route}")

        # Emit one inspection event with the routing decision and thresholds
        inspect_data = NodeStreamData(
            heading="Route Evaluation",
            summary=f"Routing decision: {route}",
            details={
                "route": route,
                "next_step": next_step,
                "retrieval_attempts": state.retrieval_attempts,
                "rewrite_attempts": state.rewrite_attempts,
                "thresholds": {
                    "max_retrieval_attempts": self.max_retrieval_attempts,
                    "max_rewrite_attempts": self.max_rewrite_attempts,
                    "fallback_to_training_data": self.fallback_to_training_data,
                },
            },
        )
        inspect_event = NodeStreamEvent(
            type="inspect", node=self.label, data=inspect_data
        )
        self.write_custom_stream(inspect_event.model_dump())

        return route
