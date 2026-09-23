#!/usr/bin/env python3
"""
Test store retrieval utilities.

File: utils_pkg/tests/test_stores_utils.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import cast

import pytest
from klea_utils.stores.utils import (
    CACHE_DIR_NAME,
    REF_DOC_OVERHEAD,
    cross_encoder_rerank,
    display_person_names,
    drop_collection,
    expand_person_names,
    find_source_files,
    instantiate_vector_store,
    rerank_by_recency,
    rrf_merge,
    serialize_reference_material,
    truncate_reference_material,
)
from langchain_core.documents import Document

logger = logging.getLogger(__name__)


def test_drop_collection_chroma_and_pgvector_use_wrapper():
    """Chroma/PGVector drop via the wrapper's delete_collection."""

    class _Store:
        def __init__(self):
            self.dropped = False

        def delete_collection(self):
            self.dropped = True

    store = _Store()
    drop_collection(store, "chroma:/path", "col")
    assert store.dropped is True

    store = _Store()
    drop_collection(store, "pgvector:postgresql://host/db", "col")
    assert store.dropped is True


def test_drop_collection_qdrant_uses_raw_client():
    """Qdrant drops via its raw client (no wrapper delete_collection)."""

    class _Store:
        collection_name = "col"

        def __init__(self):
            self.dropped = None
            self._client = type(
                "_C",
                (),
                {"delete_collection": lambda self, n: setattr(store, "dropped", n)},
            )()

    store = _Store()
    drop_collection(store, "qdrant:http://localhost:6333", "col")
    assert store.dropped == "col"


def test_drop_collection_unknown_scheme():
    """An unknown scheme raises ValueError."""
    store = type("_Store", (), {"delete_collection": lambda self: None})()
    with pytest.raises(ValueError, match="Unsupported"):
        drop_collection(store, "nope:/path", "col")


class _FakeEmbeddings:
    """Minimal embedding object for Chroma; never called during init."""

    def embed_documents(self, texts):
        return [[0.0] * 8 for _ in texts]

    def embed_query(self, text):
        return [0.0] * 8


def test_instantiate_chroma_rejects_file_path(tmp_path):
    """A chroma location pointing at an existing file is rejected."""
    pytest.importorskip("langchain_chroma")
    store_file = tmp_path / "chroma.sqlite3"
    store_file.touch()

    with pytest.raises(FileNotFoundError):
        instantiate_vector_store(
            f"chroma:{store_file}",
            "test_collection",
            _FakeEmbeddings(),
            logger,
            create=True,
        )


def test_instantiate_chroma_creates_missing_folder(tmp_path):
    """create=True creates a missing store folder and initialises it."""
    pytest.importorskip("langchain_chroma")
    store_dir = tmp_path / "new-store"

    store = instantiate_vector_store(
        f"chroma:{store_dir}",
        "test_collection",
        _FakeEmbeddings(),
        logger,
        create=True,
    )
    logger.info(f"store: {store}")
    assert store is not None
    assert store_dir.is_dir()
    assert (store_dir / "chroma.sqlite3").is_file()


def test_instantiate_chroma_folder_must_exist_when_not_creating(tmp_path):
    """create=False requires an existing store folder."""
    pytest.importorskip("langchain_chroma")
    with pytest.raises(FileNotFoundError):
        instantiate_vector_store(
            f"chroma:{tmp_path / 'missing-store'}",
            "test_collection",
            _FakeEmbeddings(),
            logger,
        )


def _doc(content: str) -> Document:
    """Build a fresh Document to avoid metadata mutation leaking across tests."""
    return Document(page_content=content, metadata={"file_name": "test.md"})


def test_rrf_merge_orders_and_dedupes_by_content():
    """RRF orders by fused rank and dedupes documents seen by both sources."""
    d1 = _doc("NeuroML standard")
    d2 = _doc("Hodgkin-Huxley action potential")
    d3 = _doc("LTP synaptic strength")
    d4 = _doc("bouton synapse morphology")

    vector_results = [(d1, 0.9), (d2, 0.8), (d3, 0.7)]
    bm25_results = [(d3, 5.2), (d2, 4.1), (d4, 3.0)]
    logger.info(
        f"vector results: {[(d.page_content, s) for d, s in vector_results]}"
        f"\nbm25 results: {[(d.page_content, s) for d, s in bm25_results]}"
    )

    merged = rrf_merge(
        [("vector store", vector_results), ("BM25", bm25_results)],
        num_refs_max=10,
    )
    logger.info(f"merged order: {[d.page_content for d, _ in merged]}")

    # d3 is rank 1 in BM25 and rank 3 in vector: 1/61 + 1/63 > 1/62 + 1/62
    assert [doc.page_content for doc, _ in merged][:2] == [
        d3.page_content,
        d2.page_content,
    ]
    # d4 was only in BM25; d1 only in vector
    contents = {doc.page_content for doc, _ in merged}
    assert len(contents) == len(merged), "results should be deduplicated"


def test_rrf_merge_caps_at_num_refs_max():
    """rrf_merge caps the result list at num_refs_max."""
    docs = [_doc(f"content {i}") for i in range(5)]
    results = [(doc, float(5 - i)) for i, doc in enumerate(docs)]

    merged = rrf_merge([("vector store", results)], num_refs_max=2)
    logger.info(f"num_refs_max=2, returned {len(merged)} documents")

    assert len(merged) == 2


def test_rrf_merge_no_cap_returns_all():
    """With num_refs_max=None, rrf_merge keeps every fused document."""
    docs = [_doc(f"content {i}") for i in range(5)]
    results = [(doc, float(5 - i)) for i, doc in enumerate(docs)]

    merged = rrf_merge([("vector store", results)], num_refs_max=None)
    logger.info(f"num_refs_max=None, returned {len(merged)} documents")

    assert len(merged) == 5


def test_rrf_merge_preserves_source_scores():
    """Docs matched by both sources carry both original scores."""
    d1 = _doc("Hodgkin-Huxley action potential")

    merged = rrf_merge(
        [("vector store", [(d1, 0.8)]), ("BM25", [(d1, 4.1)])],
        num_refs_max=10,
    )
    logger.info(f"merged source scores: {merged[0][0].metadata['_source_scores']}")

    assert len(merged) == 1
    assert merged[0][0].metadata["_source_scores"] == {
        "vector store": 0.8,
        "BM25": 4.1,
    }


class _FakeCrossEncoder:
    """Minimal stand-in; scores passages by a fixed per-content value."""

    def __init__(self, model_name: str):
        self.model_name = model_name

    def predict(self, pairs: list[tuple[str, str]]) -> list[float]:
        scores = {"low relevance": 0.2, "high relevance": 0.9, "mid relevance": 0.5}
        return [scores.get(passage, 0.0) for _, passage in pairs]


def test_cross_encoder_rerank_disabled_returns_unchanged():
    """When model_name is None, docs are returned in the original order."""
    docs = [(_doc("low relevance"), 0.8), (_doc("high relevance"), 0.2)]

    result = cross_encoder_rerank("NeuroML channels", docs, model_name=None)
    logger.info(f"disabled rerank kept order: {[d.page_content for d, _ in result]}")

    assert result == docs


def test_cross_encoder_rerank_empty_input():
    """An empty doc list stays empty."""
    assert cross_encoder_rerank("query", [], model_name="fake-model") == []


def test_cross_encoder_rerank_reorders_by_predicted_score(monkeypatch):
    """Cross-encoder scores replace RRF scores and reorder the list."""
    monkeypatch.setattr(
        "klea_utils.stores.utils._load_cross_encoder",
        lambda model_name: _FakeCrossEncoder(model_name),
    )
    d_low = _doc("low relevance")
    d_high = _doc("high relevance")
    docs = [(d_low, 0.9), (d_high, 0.1)]

    ranked = cross_encoder_rerank("NeuroML ion channels", docs, model_name="fake-model")
    logger.info(f"reranked order: {[(d.page_content, s) for d, s in ranked]}")

    assert [d.page_content for d, _ in ranked] == ["high relevance", "low relevance"]
    assert ranked[0][1] == pytest.approx(0.9)
    assert ranked[1][1] == pytest.approx(0.2)


def test_cross_encoder_rerank_top_k(monkeypatch):
    """top_k caps the reranked result list."""
    monkeypatch.setattr(
        "klea_utils.stores.utils._load_cross_encoder",
        lambda model_name: _FakeCrossEncoder(model_name),
    )
    docs = [
        (_doc("low relevance"), 0.3),
        (_doc("high relevance"), 0.2),
        (_doc("mid relevance"), 0.1),
    ]

    ranked = cross_encoder_rerank("query", docs, model_name="fake-model", top_k=2)
    logger.info(f"top_k=2 kept: {[d.page_content for d, _ in ranked]}")

    assert len(ranked) == 2
    assert [d.page_content for d, _ in ranked] == [
        "high relevance",
        "mid relevance",
    ]


def test_cross_encoder_rerank_preserves_source_scores(monkeypatch):
    """Per-source metadata from rrf_merge survives reranking."""
    monkeypatch.setattr(
        "klea_utils.stores.utils._load_cross_encoder",
        lambda model_name: _FakeCrossEncoder(model_name),
    )
    d1 = _doc("high relevance")
    d1.metadata["_source_scores"] = {"vector store": 0.8, "BM25": 4.1}

    ranked = cross_encoder_rerank("query", [(d1, 0.02)], model_name="fake-model")

    assert ranked[0][0].metadata["_source_scores"] == {
        "vector store": 0.8,
        "BM25": 4.1,
    }


def test_load_cross_encoder_import_error(monkeypatch):
    """Missing sentence-transformers raises a clear install hint."""
    import builtins

    import klea_utils.stores.utils as stores_utils

    stores_utils._cross_encoder_cache.clear()
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "sentence_transformers":
            raise ImportError("no sentence_transformers")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    with pytest.raises(ImportError, match=r"klea_utils\[rerank\]"):
        stores_utils._load_cross_encoder("fake-model")


def _doc_with_year(content: str, year: int | None) -> Document:
    """Build a fresh Document with a ``year`` metadata value (None to omit)."""
    metadata: dict = {"file_name": "test.md"}
    if year is not None:
        metadata["year"] = year
    return Document(page_content=content, metadata=metadata)


def test_rerank_by_recency_newer_doc_beats_equal_relevance_older():
    """With equal RRF relevance, the newer document is ranked first."""
    d_new = _doc_with_year("newer paper", 2024)
    d_old = _doc_with_year("older paper", 2019)
    # Equal pure RRF scores: relevance alone cannot separate them.
    merged = [(d_new, 0.016), (d_old, 0.016)]

    ranked = rerank_by_recency(merged)
    logger.info(f"reranked order: {[(d.page_content, s) for d, s in ranked]}")

    assert [d.page_content for d, _ in ranked] == ["newer paper", "older paper"]
    # d_new: norm_rrf=1.0, time=1.0 -> 0.9*1.0+0.1*1.0 = 1.0
    # d_old: norm_rrf=1.0, time=0.0 -> 0.9*1.0+0.1*0.0 = 0.9
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[1][1] == pytest.approx(0.9)


def test_rerank_by_recency_relevance_dominates_within_same_year():
    """Relevance (0.9 weight) still dominates; recency only breaks ties."""
    d_high = _doc_with_year("high relevance", 2020)
    d_low = _doc_with_year("low relevance", 2020)
    # Same year, so time scores are equal; pure RRF score decides.
    merged = [(d_high, 0.03), (d_low, 0.01)]

    ranked = rerank_by_recency(merged)
    logger.info(f"reranked order: {[(d.page_content, s) for d, s in ranked]}")

    assert [d.page_content for d, _ in ranked] == ["high relevance", "low relevance"]
    # norm_rrf: high=(0.03-0.01)/(0.03-0.01)=1.0, low=0.0; time=1.0 both.
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[1][1] == pytest.approx(0.1 * 1.0)


def test_rerank_by_recency_missing_year_scores_midpoint():
    """Docs without a year land mid-pack: above oldest, below newest."""
    d_new = _doc_with_year("newest", 2024)
    d_no_year = _doc("no year")
    d_old = _doc_with_year("oldest", 2019)
    merged = [(d_new, 0.016), (d_no_year, 0.016), (d_old, 0.016)]

    ranked = rerank_by_recency(merged)
    logger.info(f"reranked order: {[(d.page_content, s) for d, s in ranked]}")

    assert [d.page_content for d, _ in ranked] == [
        "newest",
        "no year",
        "oldest",
    ]
    # newest: 1.0; no year: 0.5 -> 0.95; oldest: 0.0 -> 0.9
    assert ranked[1][1] == pytest.approx(0.95)


def test_rerank_by_recency_single_distinct_score_and_year_no_div_zero():
    """Single-value ranges must not divide by zero (norm_rrf and time = 1.0)."""
    d = _doc_with_year("only doc", 2020)
    ranked = rerank_by_recency([(d, 0.016)])

    assert len(ranked) == 1
    assert ranked[0][1] == pytest.approx(1.0)


def test_rerank_by_recency_all_same_year_no_div_zero():
    """A single distinct year gives every doc time score 1.0 (no div by zero)."""
    d1 = _doc_with_year("a", 2022)
    d2 = _doc_with_year("b", 2022)
    merged = [(d1, 0.02), (d2, 0.01)]

    ranked = rerank_by_recency(merged)
    logger.info(f"reranked order: {[(d.page_content, s) for d, s in ranked]}")

    assert [d.page_content for d, _ in ranked] == ["a", "b"]
    # a: norm_rrf=1.0, time=1.0 -> 1.0; b: norm_rrf=0.0, time=1.0 -> 0.1
    assert ranked[0][1] == pytest.approx(1.0)
    assert ranked[1][1] == pytest.approx(0.1)


def test_rerank_by_recency_empty_input():
    """Empty input returns an empty list."""
    assert rerank_by_recency([]) == []


def test_serialize_reference_material_omits_scores():
    """serialize_reference_material does not show relevance scores to the LLM."""
    d1 = _doc("Hodgkin-Huxley action potential")
    d1.metadata["_source_scores"] = {"vector store": 0.8723, "BM25": 3.2100}

    text = serialize_reference_material({"NeuroML": [(d1, 0.0323)]})
    logger.info(f"serialized text:\n{text}")

    assert "relevance" not in text
    assert "vector store" not in text
    assert "_source_scores" not in text


def test_serialize_reference_material_keeps_ranked_ordering():
    """Chunks are still emitted in score order, just without the scores."""
    d1 = Document(
        page_content="first chunk",
        metadata={"file_name": "paper.pdf", "headings": ["Intro"]},
    )
    d2 = Document(
        page_content="second chunk",
        metadata={"file_name": "paper.pdf", "headings": ["Intro", "Methods"]},
    )

    text = serialize_reference_material({"NeuroML": [(d2, 0.9), (d1, 0.7)]})
    logger.info(f"serialized text:\n{text}")

    # d2 (higher score) is emitted as the first chunk.
    assert text.index("second chunk") < text.index("first chunk")
    assert "relevance" not in text


def test_serialize_reference_material_groups_chunks_by_file():
    """Multiple chunks of one file emit document metadata once."""
    d1 = Document(
        page_content="chunk one content",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro"],
            "authors": ["A. Sinha"],
            "year": 2020,
        },
    )
    d2 = Document(
        page_content="chunk two content",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro", "Methods"],
            "authors": ["A. Sinha"],
            "year": 2020,
        },
    )

    text = serialize_reference_material({"NeuroML": [(d1, 0.9), (d2, 0.7)]})
    logger.info(f"serialized text:\n{text}")

    # Document-level metadata appears once, on the source header.
    assert text.count("Metadata: authors=['A. Sinha'] | year=2020") == 1
    assert "Source document 1/1: [paper.pdf]" in text
    # Both chunks are present, numbered within the single source document.
    assert "chunk one content" in text
    assert "chunk two content" in text
    assert text.count("Chunk 1:") == 1
    assert text.count("Chunk 2:") == 1


def test_serialize_reference_material_chunk_numbering_resets_per_file():
    """Chunk numbering restarts for each source document."""
    d1 = Document(
        page_content="one",
        metadata={"file_name": "a.pdf", "headings": ["Intro"]},
    )
    d2 = Document(
        page_content="two",
        metadata={"file_name": "a.pdf", "headings": ["Intro", "Methods"]},
    )
    d3 = Document(
        page_content="three",
        metadata={"file_name": "b.pdf", "headings": ["Intro"]},
    )

    text = serialize_reference_material({"NeuroML": [(d1, 0.9), (d2, 0.7), (d3, 0.8)]})
    logger.info(f"serialized text:\n{text}")

    # Each source document numbers its own chunks from 1.
    assert "Chunk 1: Intro" in text
    assert "Chunk 2: Intro > Methods" in text
    # b.pdf only has one chunk, so it gets Chunk 1, not Chunk 3.
    assert text.count("Chunk 1:") == 2
    assert text.count("Chunk 2:") == 1


def test_serialize_reference_material_chunk_metadata_differs_inline():
    """Chunk-level metadata that differs from the file-level is inline."""
    d1 = Document(
        page_content="intro content",
        metadata={
            "file_name": "docs/index.md",
            "headings": ["Home"],
            "url": "https://example.com/",
        },
    )
    d2 = Document(
        page_content="guide content",
        metadata={
            "file_name": "docs/index.md",
            "headings": ["Home", "Guide"],
            "url": "https://example.com/",
            "url_section": "https://example.com/guide",
        },
    )

    text = serialize_reference_material({"Docs": [(d1, 0.8), (d2, 0.6)]})
    logger.info(f"serialized text:\n{text}")

    # Shared url only once (file-level); the section url inline.
    assert text.count("url=https://example.com/") == 1
    assert "url_section=https://example.com/guide" in text


def test_serialize_reference_material_different_urls_per_chunk():
    """Chunks with different urls keep their own url inline, not the file's."""
    d1 = Document(
        page_content="intro content",
        metadata={
            "file_name": "docs/index.md",
            "headings": ["Home"],
            "url": "https://example.com/home",
        },
    )
    d2 = Document(
        page_content="guide content",
        metadata={
            "file_name": "docs/index.md",
            "headings": ["Home", "Guide"],
            "url": "https://example.com/guide",
        },
    )

    text = serialize_reference_material({"Docs": [(d1, 0.8), (d2, 0.6)]})
    logger.info(f"serialized text:\n{text}")

    # Both urls present; each chunk carries its own.
    assert "url=https://example.com/home" in text
    assert "url=https://example.com/guide" in text
    assert text.count("url=https://example.com/home") == 1
    assert text.count("url=https://example.com/guide") == 1
    # Neither url was hoisted to the file level (they differ).
    assert not text.startswith("Metadata: url=")


def test_serialize_reference_material_shared_url_hoisted_once():
    """An identical url across all chunks is emitted once on the file level."""
    d1 = Document(
        page_content="one",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro"],
            "url": "https://doi.org/10.1/x",
        },
    )
    d2 = Document(
        page_content="two",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro", "Methods"],
            "url": "https://doi.org/10.1/x",
        },
    )

    text = serialize_reference_material({"NeuroML": [(d1, 0.9), (d2, 0.7)]})
    logger.info(f"serialized text:\n{text}")

    assert text.count("url=https://doi.org/10.1/x") == 1
    assert "Metadata: url=https://doi.org/10.1/x" in text


def test_serialize_reference_material_shared_fields_once_per_file():
    """Shared bibliographic fields appear once per file, never per chunk."""
    d1 = Document(
        page_content="one",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro"],
            "authors": ["A. Sinha"],
            "year": 2020,
        },
    )
    d2 = Document(
        page_content="two",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro", "Methods"],
            "authors": ["A. Sinha"],
            "year": 2020,
        },
    )

    text = serialize_reference_material({"NeuroML": [(d1, 0.9), (d2, 0.7)]})
    logger.info(f"serialized text:\n{text}")

    assert text.count("authors=['A. Sinha']") == 1
    assert text.count("year=2020") == 1
    assert "Metadata: authors=['A. Sinha'] | year=2020" in text


def test_serialize_reference_material_orders_files_by_best_score():
    """Files are ordered by their best chunk's score."""
    d1 = Document(
        page_content="a",
        metadata={"file_name": "low.pdf", "year": 2020},
    )
    d2 = Document(
        page_content="b",
        metadata={"file_name": "high.pdf", "year": 2021},
    )
    d3 = Document(
        page_content="c",
        metadata={"file_name": "high.pdf", "year": 2021},
    )

    text = serialize_reference_material({"NeuroML": [(d1, 0.3), (d2, 0.9), (d3, 0.8)]})
    logger.info(f"serialized text:\n{text}")

    assert text.index("high.pdf") < text.index("low.pdf")


def test_serialize_reference_material_no_file_name_falls_back_to_no_file():
    """Docs without a file_name still serialize under a '(no file)' group."""
    d1 = Document(page_content="lonely chunk", metadata={"year": 2020})

    text = serialize_reference_material({"NeuroML": [(d1, 0.5)]})
    logger.info(f"serialized text:\n{text}")

    assert "(no file)" in text
    assert "lonely chunk" in text


def _budgeted_refs(n_docs, content_len):
    """Build ``{domain: [(doc, score)]}`` in RRF order with known sizes."""
    return {
        "NeuroML": [(_doc("a" * content_len), float(n_docs - i)) for i in range(n_docs)]
    }


def test_truncate_reference_material_keeps_top_ranked_within_budget():
    """A tight budget keeps the top-ranked docs and stops at the budget."""
    refs = _budgeted_refs(5, 50)
    doc_size = 50 + REF_DOC_OVERHEAD  # 250

    # budget fits doc1 (250); doc2 crosses (500 > 450) so it is the crossing
    # doc, kept; doc3 and beyond are dropped
    budgeted = truncate_reference_material(refs, max_chars=450)
    logger.info(f"budget=450 -> {len(budgeted['NeuroML'])} docs")

    kept = budgeted["NeuroML"]
    assert [doc.page_content for doc, _ in kept] == ["a" * 50] * 2
    assert len(kept) < 5
    assert doc_size <= 450 < 2 * doc_size


def test_truncate_reference_material_large_budget_admits_lower_ranked():
    """A generous budget admits the lower-ranked docs too (k-increase case)."""
    refs = _budgeted_refs(5, 50)
    doc_size = 50 + REF_DOC_OVERHEAD

    budgeted = truncate_reference_material(refs, max_chars=5 * doc_size)
    logger.info(f"budget=5*doc_size -> {len(budgeted['NeuroML'])} docs")

    assert len(budgeted["NeuroML"]) == 5


def test_truncate_reference_material_keeps_oversized_single_doc():
    """A single doc larger than the whole budget is still kept."""
    refs = {"NeuroML": [(_doc("b" * 5000), 1.0)]}

    budgeted = truncate_reference_material(refs, max_chars=2000)
    logger.info(f"oversized doc kept: {len(budgeted['NeuroML'])} docs")

    assert len(budgeted["NeuroML"]) == 1


def test_truncate_reference_material_global_across_domains():
    """Budget is shared but round-robin ensures fairness across domains."""
    refs = {
        "A": [(_doc("c" * 50), 0.9), (_doc("c" * 50), 0.8)],
        "B": [(_doc("c" * 50), 0.7)],
    }
    # Budget 450 with doc size 250 (50+200 overhead): round-robin gives
    # A1 (250), B1 (250) -> total 500 crosses but B1 is kept as fair share,
    # A2 is then skipped.
    budgeted = truncate_reference_material(refs, max_chars=450)
    logger.info(f"domain counts: A={len(budgeted['A'])}, B={len(budgeted['B'])}")

    assert len(budgeted["A"]) == 1
    assert len(budgeted["B"]) == 1


def test_find_source_files_excludes_cache_dir(tmp_path):
    """The .klea-cache directory is never ingestible."""
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n")
    cached = tmp_path / CACHE_DIR_NAME / "xxh64_hash.pkl"
    cached.parent.mkdir()
    cached.write_bytes(b"cache")
    assert find_source_files(tmp_path) == [src]


def test_find_source_files_excludes_metadata_map_path(tmp_path):
    """The linted metadata-map file is excluded when it lives in source_dir."""
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n")
    map_path = tmp_path / "metadata-map.json"
    map_path.write_text('{"doc.md": {"DEFAULT": {}}}')
    assert find_source_files(tmp_path, metadata_map_path=map_path) == [src]


def test_find_source_files_excludes_configured_store_dir(tmp_path):
    """The configured store directory is never ingested."""
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n")
    store = tmp_path / "celegans-store"
    store.mkdir()
    (store / "chroma.sqlite3").write_bytes(b"store")
    assert find_source_files(tmp_path, store_dir=store) == [src]


def test_find_source_files_excludes_chroma_store_heuristically(tmp_path):
    """A nested store is excluded even without an explicit store_dir."""
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n")
    store = tmp_path / "celegans-store"
    store.mkdir()
    (store / "chroma.sqlite3").write_bytes(b"store")
    assert find_source_files(tmp_path) == [src]


def test_find_source_files_skips_unsupported_extensions(tmp_path):
    """Unsupported extensions are skipped; a logger gets the warning."""
    src = tmp_path / "doc.md"
    src.write_text("# Doc\n")
    bogus = tmp_path / "notes.xyz"
    bogus.write_text("not a supported doc format")
    assert find_source_files(tmp_path) == [src]

    assert find_source_files(tmp_path, logger=logging.getLogger(__name__)) == [src]


def test_find_source_files_sorted_and_files_only(tmp_path):
    """Subdirectories are not returned and results are sorted."""
    sub = tmp_path / "sub"
    sub.mkdir()
    b = tmp_path / "b.md"
    b.write_text("# B\n")
    a = tmp_path / "a.md"
    a.write_text("# A\n")
    (sub / "c.md").write_text("# C\n")
    assert find_source_files(tmp_path) == [a, b, sub / "c.md"]


def test_expand_person_names_adds_word_and_lowercase_variants():
    """Each full name keeps its word tokens, lowercase forms, and lowered full name."""
    assert expand_person_names(["Ankur Sinha"]) == [
        "Ankur Sinha",
        "ankur sinha",
        "Ankur",
        "Sinha",
        "ankur",
        "sinha",
    ]


def test_expand_person_names_is_idempotent():
    """Re-expanding an expanded list is a no-op (store policy re-applies)."""
    once = expand_person_names(["Ankur Sinha", "Padraig Gleeson"])
    assert expand_person_names(once) == once


def test_expand_person_names_dedupes_and_skips_non_strings():
    assert expand_person_names(cast(list[str], ["Alice", "alice", 42, None])) == [
        "Alice",
        "alice",
    ]


def test_display_person_names_keeps_full_names_drops_variants():
    """Only the real names show; word/lowercase variants are hidden."""
    expanded = expand_person_names(["Ankur Sinha", "Padraig Gleeson"])
    assert display_person_names(expanded) == ["Ankur Sinha", "Padraig Gleeson"]


def test_display_person_names_keeps_genuine_single_name_author():
    """A single-word name is kept unless it makes up a longer entry."""
    assert display_person_names(["Plato"]) == ["Plato"]
    assert display_person_names(["Sinha", "Ankur Sinha"]) == ["Ankur Sinha"]


def test_serialize_reference_material_hides_person_name_variants():
    """Expanded author variants do not leak into the answer LLM's references."""
    d1 = Document(
        page_content="one",
        metadata={
            "file_name": "paper.pdf",
            "headings": ["Intro"],
            "authors": expand_person_names(["Ankur Sinha", "Padraig Gleeson"]),
            "year": 2020,
        },
    )
    text = serialize_reference_material({"Docs": [(d1, 0.9)]})
    assert "authors=['Ankur Sinha', 'Padraig Gleeson']" in text
    assert "sinha" not in text
    assert "ankur" not in text
