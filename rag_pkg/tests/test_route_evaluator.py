#!/usr/bin/env python3
"""
Tests for the RAG route evaluator node.

File: rag_pkg/tests/test_route_evaluator.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, cast

from klea_rag.nodes.route_evaluator import RouteDispatcher, RouteEvaluator
from klea_rag.schemas import EvaluateAnswerSchema, RAGState
from langchain_core.messages import AIMessage

logger = logging.getLogger(__name__)


class FakeRetriever:
    """Minimal retriever recording k calls."""

    def __init__(self, name="fake", k_can_increment=True):
        self.name = name
        self.source_label = name
        self.k_can_increment = k_can_increment
        self.inc_count = 0
        self.checked_k: list[dict] = []

    def can_inc_k(self, k_values):
        self.checked_k.append(k_values)
        return self.k_can_increment

    def inc_k(self, k_values):
        self.inc_count += 1
        return k_values, self.k_can_increment


def _make_router(retrievers=None) -> RouteEvaluator:
    router = object.__new__(RouteEvaluator)
    router.logger = logging.getLogger("test_route_evaluator")
    router.label = "Routing evaluation"
    router.retrievers = retrievers or []
    router.max_retrieval_attempts = 2
    router.max_rewrite_attempts = 1
    router.fallback_to_training_data = False
    cast(Any, router).write_custom_stream = lambda event: None
    return router


def _continue_state() -> RAGState:
    """A state whose evaluator is fully satisfied."""
    return RAGState(
        query="q",
        text_response_eval=EvaluateAnswerSchema(
            confidence=0.9,
            coverage=0.9,
            relevance=0.9,
            groundedness=0.9,
            coherence=0.9,
            conciseness=0.9,
            next_step="continue",
        ),
    )


async def test_continue_does_not_touch_k():
    """Routing 'continue' neither checks nor grows k (InitRAGState resets it)."""
    r1 = FakeRetriever("vector")
    r2 = FakeRetriever("bm25")
    router = _make_router([r1, r2])

    route = (await router.execute(_continue_state()))["route"]
    logger.info(f"route: {route}")

    assert route == "continue"
    assert r1.checked_k == [] and r2.checked_k == []
    assert r1.inc_count == 0 and r2.inc_count == 0


async def test_retrieve_more_info_checks_capacity_without_mutating():
    """Routing 'retrieve_more_info' consults capacity but never grows k.

    The router only reports whether k can still grow, using each
    retriever's k from the state; the actual ``inc_k()`` is applied once by
    RetrieveInfoNode when it retrieves.
    """
    r1 = FakeRetriever("vector")
    r2 = FakeRetriever("bm25", k_can_increment=False)
    router = _make_router([r1, r2])

    # coverage >= 0.3 keeps the modify_query branch from short-circuiting
    state = RAGState(
        query="q",
        text_response_eval=EvaluateAnswerSchema(
            coverage=0.5, confidence=0.4, next_step="retrieve_more_info"
        ),
        retrieval_k={"vector": {"NeuroML": {"store": 6}}},
    )
    route = (await router.execute(state))["route"]
    logger.info(f"route: {route} | incs: r1={r1.inc_count}, r2={r2.inc_count}")

    assert route == "retrieve_more_info"
    assert r1.checked_k == [{"NeuroML": {"store": 6}}]
    assert r1.inc_count == 0
    assert r2.inc_count == 0


async def test_retrieve_more_info_via_score_override():
    """Low confidence with adequate coverage routes to retrieve_more_info
    even when the verdict said something else."""
    router = _make_router([FakeRetriever("vector")])

    state = RAGState(
        query="q",
        text_response_eval=EvaluateAnswerSchema(
            coverage=0.7, confidence=0.4, next_step="continue"
        ),
    )
    route = (await router.execute(state))["route"]

    assert route == "retrieve_more_info"


async def test_modify_query_override_wins_over_verdict():
    """Low coverage routes to modify_query even when the verdict asked for
    more info (so RetrieveInfoNode must not grow k)."""
    router = _make_router([FakeRetriever("vector")])

    state = RAGState(
        query="q",
        text_response_eval=EvaluateAnswerSchema(
            coverage=0.2, confidence=0.4, next_step="retrieve_more_info"
        ),
    )
    route = (await router.execute(state))["route"]

    assert route == "modify_query"


async def test_retrieve_more_info_without_retrievers_uses_exhausted_decision():
    """Without retrievers, 'retrieve_more_info' cannot retrieve: the routing
    falls to the exhausted-budget decision instead of continuing."""
    router = _make_router([])

    state = RAGState(
        query="q",
        messages=[AIMessage(content="a grounded but partial answer")],
        text_response_eval=EvaluateAnswerSchema(
            coverage=0.5,
            confidence=0.4,
            groundedness=0.7,
            relevance=0.6,
            coherence=0.8,
            conciseness=0.8,
            next_step="retrieve_more_info",
        ),
    )
    route = (await router.execute(state))["route"]
    logger.info(f"route with no retrievers: {route}")

    assert route == "best_effort"


async def test_rewrite_answer_directive_not_dropped_by_retrieval_budget():
    """An explicit 'rewrite_answer' directive routes to rewrite even when the
    retrieval budget and retrievers are available (retrieval actions take
    priority, but must not swallow an explicit rewrite)."""
    router = _make_router([FakeRetriever("vector")])

    state = RAGState(
        query="q",
        retrieval_attempts=0,
        rewrite_attempts=0,
        text_response_eval=EvaluateAnswerSchema(
            coverage=0.6,
            confidence=0.6,
            relevance=0.6,
            groundedness=0.2,
            coherence=0.8,
            conciseness=0.8,
            next_step="rewrite_answer",
        ),
    )
    route = (await router.execute(state))["route"]

    assert route == "rewrite_answer"


async def test_rewrite_answer_via_score_override():
    """High coverage/confidence with a weak answer routes to rewrite_answer
    even when the verdict did not ask for it."""
    router = _make_router([])

    state = RAGState(
        query="q",
        rewrite_attempts=0,
        text_response_eval=EvaluateAnswerSchema(
            coverage=0.7,
            confidence=0.7,
            relevance=0.2,
            groundedness=0.7,
            coherence=0.8,
            conciseness=0.8,
            next_step="continue",
        ),
    )
    route = (await router.execute(state))["route"]

    assert route == "rewrite_answer"


def _exhausted_state(
    coverage,
    confidence,
    groundedness,
    content="some answer",
    next_step="modify_query",
):
    """A state with all retrieval/rewrite budgets exhausted."""
    return RAGState(
        query="q",
        retrieval_attempts=5,
        rewrite_attempts=1,
        messages=[AIMessage(content=content)] if content else [],
        text_response_eval=EvaluateAnswerSchema(
            coverage=coverage,
            confidence=confidence,
            groundedness=groundedness,
            relevance=0.6,
            coherence=0.8,
            conciseness=0.8,
            next_step=next_step,
        ),
    )


async def test_exhausted_low_coverage_falls_back():
    """Exhausted with low coverage falls back to training data when enabled."""
    router = _make_router([])
    router.fallback_to_training_data = True

    route = (
        await router.execute(
            _exhausted_state(coverage=0.2, confidence=0.6, groundedness=0.7)
        )
    )["route"]

    assert route == "fallback"


async def test_exhausted_low_coverage_without_fallback_clarifies():
    """Exhausted with low coverage asks for clarification when fallback is off."""
    router = _make_router([])

    route = (
        await router.execute(
            _exhausted_state(coverage=0.2, confidence=0.6, groundedness=0.7)
        )
    )["route"]

    assert route == "undefined"


async def test_exhausted_low_confidence_falls_back():
    """Exhausted with vague context (low confidence) falls back to training data.

    Reached via the retrieve_more_info branch once k can no longer grow and
    the query budget is exhausted.
    """
    router = _make_router([FakeRetriever("vector", k_can_increment=False)])
    router.fallback_to_training_data = True

    route = (
        await router.execute(
            _exhausted_state(coverage=0.6, confidence=0.2, groundedness=0.7)
        )
    )["route"]

    assert route == "fallback"


async def test_exhausted_ungrounded_clarifies():
    """Exhausted with an ungrounded answer asks for clarification."""
    router = _make_router([])

    route = (
        await router.execute(
            _exhausted_state(coverage=0.6, confidence=0.6, groundedness=0.2)
        )
    )["route"]

    assert route == "undefined"


async def test_exhausted_empty_answer_clarifies():
    """Exhausted with an empty answer asks for clarification."""
    router = _make_router([])

    route = (
        await router.execute(
            _exhausted_state(coverage=0.6, confidence=0.6, groundedness=0.7, content="")
        )
    )["route"]

    assert route == "undefined"


async def test_exhausted_best_effort():
    """Exhausted with a grounded, non-empty answer routes to best_effort."""
    router = _make_router([])

    route = (
        await router.execute(
            _exhausted_state(coverage=0.5, confidence=0.6, groundedness=0.7)
        )
    )["route"]

    assert route == "best_effort"


async def test_dispatcher_follows_recorded_route():
    """The thin dispatcher returns exactly the route recorded in state."""
    dispatcher = RouteDispatcher(
        logger=logging.getLogger("test_route_evaluator"), label="Following route"
    )
    state = _continue_state()
    state.route = "best_effort"

    assert await dispatcher.execute(state) == "best_effort"
