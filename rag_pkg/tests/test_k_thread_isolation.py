#!/usr/bin/env python3
"""
Tests that the retrieval depth (k) is isolated per thread and per query.

File: rag_pkg/tests/test_k_thread_isolation.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
from typing import Any

from klea_rag.nodes.init_rag import InitRAGState
from klea_rag.nodes.retrieve_info import RetrieveInfoNode
from klea_rag.nodes.route_evaluator import RouteDispatcher, RouteEvaluator
from klea_rag.schemas import EvaluateAnswerSchema, RAGState, RetrievalQueryOutput
from klea_utils.stores.config import PerDomainConfig, RetrieverConfig, VectorStoreInfo
from klea_utils.stores.retrieval.base import BaseKleaRetriever
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.graph import END, START, StateGraph

logger = logging.getLogger(__name__)

DOMAIN = "NeuroML"

# good on every metric: routes to "continue"
GOOD = EvaluateAnswerSchema(
    next_step="continue",
    coverage=0.9,
    confidence=0.9,
    relevance=0.9,
    groundedness=0.9,
    coherence=0.9,
    conciseness=0.9,
)

# enough coverage but low confidence: routes to "retrieve_more_info" while
# the retrieval budget lasts, then to "fallback"
WEAK = EvaluateAnswerSchema(
    next_step="retrieve_more_info",
    coverage=0.5,
    confidence=0.1,
    relevance=0.5,
    groundedness=0.5,
    coherence=0.5,
    conciseness=0.5,
)


class RecordingRetriever(BaseKleaRetriever):
    """Real retriever manager whose only store records the k it is asked for."""

    source_label = "vector store"

    def __init__(self, config: RetrieverConfig, logger: logging.Logger, **kwargs):
        super().__init__(config, logger, **kwargs)
        self.calls: list[tuple[str, int]] = []

    def _stores_of(self, domain: PerDomainConfig) -> list[Any]:
        return domain.vector_stores

    def _instantiate_store(self, path: str, name: str) -> Any:
        return object()

    def _retrieve_from_store(self, store, query, k, metadata_filter=None):
        self.calls.append((query, k))
        return []

    def ks_for(self, query: str) -> list[int]:
        """Return the k values used, in order, for one query."""
        return [k for q, k in self.calls if q == query]


def _make_retriever() -> RecordingRetriever:
    config = RetrieverConfig(
        domains={
            DOMAIN: PerDomainConfig(
                vector_stores=[VectorStoreInfo(name="docs", path="chroma:/fake/docs")]
            )
        }
    )
    retriever = RecordingRetriever(
        config, logging.getLogger("test_k_thread_isolation"), default_k=5, k_max=10
    )
    retriever.load_all_stores()
    return retriever


def _build_graph(retriever, evaluate, max_retrieval_attempts=5):
    """Compile init -> prepare -> retrieve -> evaluate -> route with real nodes.

    Only the LLM nodes are replaced: ``prepare`` stands in for the
    classifier and query generator, ``evaluate`` for the answer evaluator.
    """

    async def prepare(state: RAGState) -> dict[str, Any]:
        return {
            "query_domains": [DOMAIN],
            "retrieval_query": RetrievalQueryOutput(search_query=state.query),
        }

    init = InitRAGState(logger, "init")
    retrieve = RetrieveInfoNode(logger, "retrieve", retrievers=[retriever])
    route_node = RouteEvaluator(
        logger=logger,
        label="route",
        retrievers=[retriever],
        max_retrieval_attempts=max_retrieval_attempts,
        max_rewrite_attempts=0,
        fallback_to_training_data=True,
    )
    dispatcher = RouteDispatcher(logger=logger, label="dispatch")

    workflow = StateGraph(RAGState)
    workflow.add_node("init", init.execute)
    workflow.add_node("prepare", prepare)
    workflow.add_node("retrieve", retrieve.execute)
    workflow.add_node("evaluate", evaluate)
    workflow.add_node(route_node.label, route_node.execute)
    workflow.add_edge(START, "init")
    workflow.add_edge("init", "prepare")
    workflow.add_edge("prepare", "retrieve")
    workflow.add_edge("retrieve", "evaluate")
    workflow.add_edge("evaluate", route_node.label)
    workflow.add_conditional_edges(
        route_node.label,
        dispatcher.execute,
        {
            "retrieve_more_info": "retrieve",
            "continue": END,
            "best_effort": END,
            "fallback": END,
            "undefined": END,
            "modify_query": END,
            "rewrite_answer": END,
        },
    )
    serde = JsonPlusSerializer(
        allowed_msgpack_modules=[EvaluateAnswerSchema, RetrievalQueryOutput]
    )
    return workflow.compile(checkpointer=InMemorySaver(serde=serde))


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


async def test_concurrent_threads_do_not_share_k():
    """One chat growing or finishing must not change another chat's k.

    Thread A grows k once and pauses mid-question.  Thread B then runs a
    whole question and finishes with "continue".  B must start at the
    default k, and A must carry on from its own k when it resumes.
    """
    retriever = _make_retriever()
    a_paused = asyncio.Event()
    b_done = asyncio.Event()
    verdicts = {"question A": [WEAK, WEAK, GOOD], "question B": [GOOD]}
    seen = {"question A": 0, "question B": 0}

    async def evaluate(state: RAGState) -> dict[str, Any]:
        n = seen[state.query]
        seen[state.query] += 1
        if state.query == "question A" and n == 1:
            a_paused.set()
            await b_done.wait()
        return {"text_response_eval": verdicts[state.query][n]}

    graph = _build_graph(retriever, evaluate)

    task_a = asyncio.create_task(graph.ainvoke({"query": "question A"}, _config("A")))
    await asyncio.wait_for(a_paused.wait(), timeout=5)
    await graph.ainvoke({"query": "question B"}, _config("B"))
    b_done.set()
    await asyncio.wait_for(task_a, timeout=5)

    logger.info(
        f"A ks: {retriever.ks_for('question A')}, "
        f"B ks: {retriever.ks_for('question B')}"
    )
    assert retriever.ks_for("question B") == [5]
    assert retriever.ks_for("question A") == [5, 6, 7]


async def test_k_resets_for_next_query_after_fallback():
    """A query that ends without "continue" must not leak k to the next one.

    Every answer is weak, so k grows until the retrieval budget runs out
    and the query ends with "fallback".  The next query on the same
    thread must start again from the default k.
    """
    retriever = _make_retriever()

    async def evaluate(state: RAGState) -> dict[str, Any]:
        return {"text_response_eval": WEAK}

    graph = _build_graph(retriever, evaluate, max_retrieval_attempts=3)

    await graph.ainvoke({"query": "first"}, _config("A"))
    await graph.ainvoke({"query": "second"}, _config("A"))

    logger.info(
        f"first ks: {retriever.ks_for('first')}, "
        f"second ks: {retriever.ks_for('second')}"
    )
    assert retriever.ks_for("first") == [5, 6, 7]
    assert retriever.ks_for("second") == [5, 6, 7]
