#!/usr/bin/env python3
"""
BM25 keyword retriever manager

See ``devdocs/adr/0012-bm25-hybrid.md`` and ``devdocs/system/store-create.md``.

File: klea_utils/stores/retrieval/bm25.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import pickle
from pathlib import Path
from typing import Any, override

from langchain_core.documents import Document

from klea_utils.stores.langchain_bm25 import BM25Retriever
from klea_utils.stores.retrieval.base import BaseKleaRetriever

from ..config import PerDomainConfig, RetrieverConfig
from ..filters import filter_docs_by_metadata

#: How many extra candidates the BM25 store fetches before post-filtering
#: when a metadata filter is active, so enough matching documents remain
#: after the filter removes non-matching results.
BM25_FILTER_MARGIN = 3


class BM25RetrieverManager(BaseKleaRetriever):
    """Manages domain-specific BM25 keyword stores.

    Each BM25 store is a pickled corpus of chunked documents written
    alongside the vector store by
    :meth:`~klea_utils.stores.ingestion.StoresBuilder.store_all` (one
    pickled list per batch; read back by looping ``pickle.load`` until
    ``EOFError``).
    Stores are loaded lazily per domain: the corpus is unpickled and used
    to build a :class:`klea_utils.stores.langchain_bm25`, which
    is queried with BM25 keyword scoring.

    A store whose corpus file is missing is skipped with a warning, so a
    misconfigured domain degrades gracefully instead of failing retrieval.

    Scalability note: this is a pure-Python in-memory index
    (``rank_bm25``).  Building and querying stay fast to well over
    ~100k chunks, but memory grows roughly with the total number of
    unique terms (one Python dict entry per term per chunk, ~100-150
    bytes each), so a single collection becomes heavy in the ~50-100k
    chunk range.  That is far beyond current corpora, but if large
    platformed deployments are ever planned, consider a proper keyword
    backend (e.g. Elasticsearch, Qdrant sparse vectors, or Postgres
    full-text search) instead.
    """

    source_label = "BM25"

    def __init__(
        self,
        config: RetrieverConfig,
        logger: logging.Logger,
        default_k: int = 5,
        k_max: int = 10,
        k_inc: int = 1,
    ):
        """Initialise the BM25 retriever manager.

        :param config: Retriever configuration for all domains
        :param logger: Logger instance (injected from orchestrator)
        :param default_k: Fallback number of documents to retrieve
        :param k_max: Fallback maximum number of documents to retrieve
        :param k_inc: Fallback amount to increase ``k`` by per ``inc_k``
        """
        super().__init__(
            config=config,
            logger=logger,
            default_k=default_k,
            k_max=k_max,
            k_inc=k_inc,
        )

    @override
    def _stores_of(self, domain: PerDomainConfig) -> list[Any]:
        """Return the BM25 stores configured for *domain*."""
        return domain.bm25_stores

    @override
    def _instantiate_store(self, path: str, name: str):
        """Load a BM25 corpus pickle and build a BM25Retriever from it.

        :param path: Path to the pickled document corpus
        :param name: Store name from the configuration
        :returns: A :class:`klea_utils.stores.langchain_bm25.BM25Retriever`,
            or ``None`` if the corpus file is missing
        """

        corpus_path = Path(path)
        if not corpus_path.is_file():
            self.logger.warning(
                f"BM25 corpus not found, skipping store '{name}': {corpus_path}"
            )
            return None

        docs: list[Document] = []
        with open(corpus_path, "rb") as f:
            # Batched corpora hold one pickled list per batch; legacy
            # corpora hold a single flat list.  Reading until EOF handles
            # both transparently.
            while True:
                try:
                    docs.extend(pickle.load(f))
                except EOFError:
                    break
        self.logger.debug(f"Loaded {len(docs)} chunks for BM25 store '{name}'")
        return BM25Retriever.from_documents(docs)

    @override
    def _retrieve_from_store(
        self,
        store: Any,
        query: str,
        k: int,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[tuple[Document, float]]:
        """Run BM25 keyword search on a single store.

        When *metadata_filter* is set, more candidates than *k* are
        fetched (a :data:`BM25_FILTER_MARGIN`) and only the matching
        subset is kept, because the ``rank_bm25`` index has no native
        filter support.  If fewer than *k* documents remain after
        filtering, retrieval degrades gracefully (the fuser simply sees
        fewer results).

        :param store: Loaded BM25 store to query
        :param query: User query string
        :param k: Number of documents to retrieve from this store
        :param metadata_filter: Optional metadata filter in the DSL (see
            :func:`klea_utils.stores.filters.validate_metadata_filter`)
        :returns: List of (document, relevance_score) tuples.  Documents
            with a non-positive score (no term overlap with the query)
            are dropped.
        """
        retriever = store.loaded_object
        processed = retriever.preprocess_func(query)
        margin = k * BM25_FILTER_MARGIN if metadata_filter is not None else k
        top_docs = retriever.vectorizer.get_top_n(processed, retriever.docs, n=margin)
        scores = retriever.vectorizer.get_scores(processed)
        # get_top_n returns the same Document objects, so map scores by id.
        score_by_id = {id(doc): score for doc, score in zip(retriever.docs, scores)}

        result = [(doc, score_by_id[id(doc)]) for doc in top_docs]
        result = [(doc, score) for doc, score in result if score > 0]

        if metadata_filter is not None:
            kept = filter_docs_by_metadata([doc for doc, _ in result], metadata_filter)
            kept_ids = {id(doc) for doc in kept}
            result = [(doc, score) for doc, score in result if id(doc) in kept_ids][:k]
            self.logger.debug(
                f"BM25 filter: kept {len(result)} after filtering with "
                f"{metadata_filter = }"
            )
        return result
