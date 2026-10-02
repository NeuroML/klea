#!/usr/bin/env python3
"""
Tests for the RAG retrieve information node.

File: rag_pkg/tests/test_retrieve_info.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any

import pytest
from klea_rag.nodes.retrieve_info import RetrieveInfoNode
from klea_rag.schemas import EvaluateAnswerSchema, RAGState, RetrievalQueryOutput
from klea_utils.stores.config import FilterFieldInfo
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


@pytest.fixture(autouse=True)
def _identity_cross_encoder(monkeypatch):
    """Pass fusion results through unchanged so tests load no real model.

    Tests that exercise cross-encoder behaviour patch this themselves.
    """
    monkeypatch.setattr(
        "klea_rag.nodes.retrieve_info.cross_encoder_rerank",
        lambda query, docs, *, model: docs,
    )


class FakeRetriever:
    """Minimal retriever returning canned results and recording k calls."""

    def __init__(self, results, name="fake"):
        self.results = results
        self.source_label = name
        self.inc_count = 0
        self.queries: list[str] = []
        self.filters: list[dict | None] = []
        self.k_values: list[dict | None] = []

    def retrieve(self, domain_name, query, metadata_filter=None, k_values=None):
        self.queries.append(query)
        self.filters.append(metadata_filter)
        self.k_values.append(k_values)
        return self.results

    def can_inc_k(self, k_values):
        return True

    def inc_k(self, k_values):
        self.inc_count += 1
        return {"NeuroML": {"store": 6}}, True


def _make_node(retrievers) -> RetrieveInfoNode:
    node = object.__new__(RetrieveInfoNode)
    node.logger = logging.getLogger("test_retrieve_info")
    node.label = "Retrieving information"
    node.retrievers = retrievers
    node.max_refs_size = 20000
    node.filter_fields_by_domain = {}
    node.cross_encoder = object()
    node.write_custom_stream = lambda event: None
    logger.info(
        f"configured retrievers: "
        f"{[(r.source_label, len(r.results)) for r in retrievers]}"
    )
    return node


def _doc(content: str) -> Document:
    return Document(page_content=content, metadata={"file_name": "test.md"})


def _doc_with_year(content: str, year: int) -> Document:
    return Document(
        page_content=content, metadata={"file_name": "test.md", "year": year}
    )


async def test_execute_reranks_equal_relevance_by_recency():
    """Newer documents are ranked ahead of older ones with equal RRF standing.

    Two documents seen by both sources at swapped ranks end up with equal
    RRF scores, so recency decides the final order: the newer one wins.
    """
    d_new = _doc_with_year("newer paper", 2024)
    d_old = _doc_with_year("older paper", 2019)
    # r1 ranks new first, r2 ranks old first -> both docs get equal RRF.
    r1 = FakeRetriever([(d_new, 0.9), (d_old, 0.8)], name="vector store")
    r2 = FakeRetriever([(d_old, 4.1), (d_new, 4.0)], name="BM25")
    node = _make_node([r1, r2])

    state = RAGState(
        query="papers",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="papers"),
    )
    result = await node.execute(state)

    refs = result["reference_material"]["NeuroML"]
    logger.info(f"reranked order: {[d.page_content for d, _ in refs]}")

    assert [doc.page_content for doc, _ in refs][:2] == [
        "newer paper",
        "older paper",
    ]
    assert refs[0][1] > refs[1][1]


async def test_execute_passes_injected_cross_encoder(monkeypatch):
    """The node passes its injected model to ``cross_encoder_rerank``."""
    sentinel = object()
    seen: dict[str, object] = {}

    def fake_rerank(query, docs, *, model):
        seen["query"] = query
        seen["model"] = model
        return docs

    monkeypatch.setattr(
        "klea_rag.nodes.retrieve_info.cross_encoder_rerank", fake_rerank
    )

    node = _make_node([FakeRetriever([(_doc("first"), 0.9)], name="vector store")])
    node.cross_encoder = sentinel

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
    )
    await node.execute(state)

    assert seen["model"] is sentinel
    assert seen["query"] == "q"


async def test_execute_skips_cross_encoder_when_none(monkeypatch):
    """With no injected cross-encoder the fused (RRF) order is kept."""
    called = False

    def fake_rerank(query, docs, *, model):
        nonlocal called
        called = True
        return docs

    monkeypatch.setattr(
        "klea_rag.nodes.retrieve_info.cross_encoder_rerank", fake_rerank
    )

    d1 = _doc("first")
    d2 = _doc("second")
    node = _make_node([FakeRetriever([(d1, 0.9), (d2, 0.1)], name="vector store")])
    node.cross_encoder = None

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
    )
    result = await node.execute(state)

    refs = result["reference_material"]["NeuroML"]
    logger.info(f"rerank skipped, RRF order: {[d.page_content for d, _ in refs]}")

    assert called is False
    assert [d.page_content for d, _ in refs][:2] == ["first", "second"]


async def test_execute_merges_retrievers_with_rrf():
    """execute() fuses results from all retrievers with RRF."""
    d1 = _doc("NeuroML standard")
    d2 = _doc("Hodgkin-Huxley action potential")
    r1 = FakeRetriever([(d1, 0.9), (d2, 0.8)], name="vector store")
    r2 = FakeRetriever([(d2, 4.1)], name="BM25")
    node = _make_node([r1, r2])

    state = RAGState(
        query="action potential",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="action potential"),
    )
    result = await node.execute(state)

    refs = result["reference_material"]["NeuroML"]
    logger.info(
        f"merged order: {[d.page_content for d, _ in refs]}"
        f"\nsource scores: {[d.metadata.get('_source_scores') for d, _ in refs]}"
    )

    assert [doc.page_content for doc, _ in refs][:2] == [
        d2.page_content,
        d1.page_content,
    ]
    # d2 was seen by both sources, so it carries both original scores
    assert refs[0][0].metadata["_source_scores"] == {
        "vector store": 0.8,
        "BM25": 4.1,
    }


async def test_execute_inc_k_on_all_retrievers_for_more_info():
    """inc_k() is called on every retriever when more info is requested.

    The grown k values are used for retrieval and returned in the state
    update, keyed by retriever.
    """
    r1 = FakeRetriever([], name="vector store")
    r2 = FakeRetriever([], name="BM25")
    node = _make_node([r1, r2])

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
        text_response_eval=EvaluateAnswerSchema(next_step="retrieve_more_info"),
    )
    result = await node.execute(state)
    logger.info(f"inc counts: r1={r1.inc_count}, r2={r2.inc_count}")

    assert r1.inc_count == 1
    assert r2.inc_count == 1
    assert r1.k_values == [{"NeuroML": {"store": 6}}]
    assert result["retrieval_k"] == {
        "vector store": {"NeuroML": {"store": 6}},
        "BM25": {"NeuroML": {"store": 6}},
    }


async def test_execute_keeps_k_without_more_info_request():
    """Without a retrieve_more_info request, k from the state is used as is."""
    r1 = FakeRetriever([], name="vector store")
    node = _make_node([r1])

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
        retrieval_k={"vector store": {"NeuroML": {"store": 7}}},
    )
    result = await node.execute(state)

    assert r1.inc_count == 0
    assert r1.k_values == [{"NeuroML": {"store": 7}}]
    assert result["retrieval_k"] == {"vector store": {"NeuroML": {"store": 7}}}


async def test_execute_truncates_reference_material_to_size_budget():
    """A small max_refs_size truncates refs; a large one keeps every doc."""
    docs = [_doc(str(i) * 50) for i in range(3)]
    node = _make_node(
        [
            FakeRetriever(
                [(d, 1.0 - i / 10) for i, d in enumerate(docs)],
                name="vector store",
            )
        ]
    )

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
    )

    # default 20000 chars keeps everything
    result = await node.execute(state)
    logger.info(f"default budget refs: {len(result['reference_material']['NeuroML'])}")
    assert len(result["reference_material"]["NeuroML"]) == 3

    # a tight budget (each doc is 50 + 200 overhead = 250) keeps only the
    # top-ranked docs: doc0 fits, doc1 crosses the 450 budget and is kept,
    # doc2 is dropped
    node.max_refs_size = 450
    result = await node.execute(state)
    refs = result["reference_material"]["NeuroML"]
    logger.info(f"tight budget refs: {len(refs)}")
    assert len(refs) == 2
    assert refs[0][0].page_content == "0" * 50


async def test_execute_normalizes_retrieval_query():
    """Artifact-laden retrieval queries reach every retriever normalized.

    Queries are LLM-generated so artifacts are rare, but they must share
    the same plain-text form as indexed chunks (soft hyphens, no-break
    spaces, etc. stripped at indexing time).
    """
    r1 = FakeRetriever([(_doc("content"), 1.0)], name="vector store")
    r2 = FakeRetriever([(_doc("content"), 1.0)], name="BM25")
    node = _make_node([r1, r2])

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(
            search_query="multi-\u00adscale model\u00ading in\u00a0neuroscience"
        ),
    )
    await node.execute(state)

    expected = "multi-scale modeling in neuroscience"
    assert r1.queries == [expected]
    assert r2.queries == [expected]


async def test_execute_passes_metadata_filter_to_retrievers():
    """The generated metadata filter reaches every retriever."""
    r1 = FakeRetriever([(_doc("content"), 1.0)], name="vector store")
    r2 = FakeRetriever([(_doc("content"), 1.0)], name="BM25")
    node = _make_node([r1, r2])

    state = RAGState(
        query="motor cortex",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(
            search_query="motor cortex",
            config_filters=[
                {"journal": {"$eq": "nature"}},
                {"year": {"$gte": 2020}},
                {"year": {"$lte": 2025}},
            ],
        ),
    )
    await node.execute(state)

    expected = {
        "$and": [
            {"journal": {"$eq": "nature"}},
            {"year": {"$gte": 2020}},
            {"year": {"$lte": 2025}},
        ]
    }
    logger.info(f"filters seen by retrievers: {r1.filters}")
    assert r1.filters == [expected]
    assert r2.filters == [expected]


async def test_execute_no_metadata_filter_is_none():
    """Without filter fields, retrievers receive metadata_filter=None."""
    r1 = FakeRetriever([(_doc("content"), 1.0)], name="vector store")
    node = _make_node([r1])

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
    )
    await node.execute(state)

    assert r1.filters == [None]


async def test_execute_labels_url_keys_in_display():
    """url_<label> keys display as 'label: <url>'; bare/numbered stay plain."""
    events: list[dict] = []

    doc = _doc("NeuroML content")
    doc.metadata.update(
        {
            "url": "https://example.org/main",
            "url_1": "https://example.org/extra",
            "url_orcid": "https://orcid.org/0000-0000-0000-0000",
        }
    )
    node: Any = _make_node([FakeRetriever([(doc, 0.9)], name="vector store")])
    node.write_custom_stream = lambda event: events.append(event)

    state = RAGState(
        query="q",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
    )
    await node.execute(state)

    display = next(e["data"]["display"] for e in events if e.get("type") == "state")
    logger.info(f"reference display markdown:\n{display}")

    assert "- orcid: https://orcid.org/0000-0000-0000-0000" in display
    assert "- https://example.org/main" in display
    assert "- https://example.org/extra" in display


async def test_execute_increments_retrieval_attempts():
    """execute() counts each retrieval pass via retrieval_attempts."""
    doc = _doc("NeuroML standard")
    node = _make_node([FakeRetriever([(doc, 0.9)], name="vector store")])

    state = RAGState(
        query="standard",
        query_domains=["NeuroML"],
        retrieval_query=RetrievalQueryOutput(search_query="standard"),
        retrieval_attempts=2,
    )
    result = await node.execute(state)

    logger.info(f"result: {result}")
    assert result["retrieval_attempts"] == 3


async def test_execute_no_retrievers_still_counts_attempt():
    """A retrieval pass with no retrievers still advances the counter."""
    node = _make_node([])

    result = await node.execute(RAGState(query="q", query_domains=["NeuroML"]))

    logger.info(f"result with no retrievers: {result}")
    assert result == {"retrieval_attempts": 1}


async def test_execute_skips_undefined_domain():
    """The 'undefined' domain is skipped and yields no references."""
    node = _make_node([FakeRetriever([(_doc("content"), 1.0)])])

    state = RAGState(
        query="q",
        query_domains=["undefined"],
        retrieval_query=RetrievalQueryOutput(search_query="q"),
    )
    result = await node.execute(state)
    logger.info(f"result with undefined domain: {result}")

    assert "reference_material" in result
    assert result["reference_material"] == {}


async def test_execute_restricts_filter_per_domain():
    """A cross-domain query only applies each domain's declared fields."""
    r1 = FakeRetriever([(_doc("content"), 1.0)], name="vector store")
    node = _make_node([r1])
    node.filter_fields_by_domain = {
        "papers": [
            FilterFieldInfo(name="journal", description="venue", value_type="string")
        ],
        "repos": [
            FilterFieldInfo(name="username", description="owner", value_type="string")
        ],
    }

    state = RAGState(
        query="q",
        query_domains=["papers", "repos"],
        retrieval_query=RetrievalQueryOutput(
            search_query="q",
            config_filters=[
                {"journal": {"$eq": "nature"}},
                {"username": {"$eq": "padraig"}},
            ],
        ),
    )
    await node.execute(state)

    # One call per domain; each receives only its own declared fields.
    assert r1.filters == [
        {"journal": {"$eq": "nature"}},
        {"username": {"$eq": "padraig"}},
    ]


async def test_execute_domain_without_declared_fields_gets_no_filter():
    """A domain declaring no fields never receives a combined filter."""
    r1 = FakeRetriever([(_doc("content"), 1.0)], name="vector store")
    node = _make_node([r1])
    node.filter_fields_by_domain = {
        "papers": [
            FilterFieldInfo(name="journal", description="venue", value_type="string")
        ],
        "repos": [],
    }

    state = RAGState(
        query="q",
        query_domains=["papers", "repos"],
        retrieval_query=RetrievalQueryOutput(
            search_query="q",
            config_filters=[
                {"journal": {"$eq": "nature"}},
                {"username": {"$eq": "padraig"}},
            ],
        ),
    )
    await node.execute(state)

    assert r1.filters == [
        {"journal": {"$eq": "nature"}},
        None,
    ]


async def test_execute_single_domain_identity_with_configured_map():
    """A single domain declaring both fields keeps the combined filter."""
    r1 = FakeRetriever([(_doc("content"), 1.0)], name="vector store")
    node = _make_node([r1])
    node.filter_fields_by_domain = {
        "papers": [
            FilterFieldInfo(name="journal", description="venue", value_type="string"),
            FilterFieldInfo(name="username", description="owner", value_type="string"),
        ],
    }

    state = RAGState(
        query="q",
        query_domains=["papers"],
        retrieval_query=RetrievalQueryOutput(
            search_query="q",
            config_filters=[
                {"journal": {"$eq": "nature"}},
                {"username": {"$eq": "padraig"}},
            ],
        ),
    )
    await node.execute(state)

    assert r1.filters == [
        {"$and": [{"journal": {"$eq": "nature"}}, {"username": {"$eq": "padraig"}}]}
    ]
