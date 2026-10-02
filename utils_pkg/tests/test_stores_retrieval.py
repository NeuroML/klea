#!/usr/bin/env python3
"""
Test store retrieval code.

File: tests/test_stores_retrieval.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

# TODO: Add tests for Qdrant and PGVector backends.
# Currently only Chroma is exercised here.

import json
import logging
import os
import pickle
import tempfile
import unittest
from pathlib import Path
from typing import ClassVar

import pytest
from klea_utils.stores.config import (
    BM25StoreInfo,
    PerDomainConfig,
    RetrieverConfig,
    VectorStoreInfo,
)
from klea_utils.stores.retrieval.bm25 import BM25RetrieverManager
from klea_utils.stores.retrieval.vs import VSRetriever
from langchain_core.documents import Document
from ollama import ResponseError


class FakeStore:
    """Fake vector store that records the k used in similarity searches."""

    def __init__(self):
        self.calls = []

    def similarity_search_with_relevance_scores(self, query, k, score_threshold):
        self.calls.append({"query": query, "k": k, "score_threshold": score_threshold})
        return []


class TestStores(unittest.TestCase):
    """Docstring for TestStores."""

    def _make_retriever(self) -> VSRetriever:
        """Build a retriever with a per-store configured and a fallback store."""
        config = RetrieverConfig(
            domains={
                "NeuroML": PerDomainConfig(
                    vector_stores=[
                        VectorStoreInfo(
                            name="big",
                            path="chroma:/fake/big",
                            default_k=2,
                            k_max=4,
                            k_inc=2,
                        ),
                        VectorStoreInfo(name="small", path="chroma:/fake/small"),
                    ]
                )
            }
        )
        retriever = VSRetriever(
            config=config,
            logger=logging.getLogger("test_stores"),
            embedding_model="dummy",
            default_k=5,
            k_max=10,
            k_inc=1,
        )
        retriever.embeddings = object()
        return retriever

    def test_per_store_resolution(self):
        """Per-store k settings resolve with a fall back to the global values."""
        retriever = self._make_retriever()
        big, small = retriever.config.domains["NeuroML"].vector_stores

        # "big" has its own per-store settings
        self.assertEqual(retriever._default_k_for(big), 2)
        self.assertEqual(retriever._k_max_for(big), 4)
        self.assertEqual(retriever._k_inc_for(big), 2)

        # "small" falls back to the global values
        self.assertEqual(retriever._default_k_for(small), 5)
        self.assertEqual(retriever._k_max_for(small), 10)
        self.assertEqual(retriever._k_inc_for(small), 1)

    def test_retrieve_uses_per_store_k(self):
        """retrieve() passes each store its default k when none is given."""
        retriever = self._make_retriever()
        big, small = retriever.config.domains["NeuroML"].vector_stores
        big_fake = FakeStore()
        small_fake = FakeStore()
        big.loaded_object = big_fake
        small.loaded_object = small_fake

        retriever.retrieve("NeuroML", "some query")

        self.assertEqual(big_fake.calls[0]["k"], 2)
        self.assertEqual(small_fake.calls[0]["k"], 5)
        self.assertEqual(big_fake.calls[0]["score_threshold"], 0.15)

    def test_retrieve_uses_given_k_values(self):
        """retrieve() uses the given k values; stores missing from them use defaults."""
        retriever = self._make_retriever()
        big, small = retriever.config.domains["NeuroML"].vector_stores
        big_fake = FakeStore()
        small_fake = FakeStore()
        big.loaded_object = big_fake
        small.loaded_object = small_fake

        retriever.retrieve("NeuroML", "some query", k_values={"NeuroML": {"big": 4}})

        self.assertEqual(big_fake.calls[0]["k"], 4)
        self.assertEqual(small_fake.calls[0]["k"], 5)

    def test_inc_k_loaded_only_and_capped(self):
        """inc_k() only grows loaded stores, capped per-store at k_max."""
        retriever = self._make_retriever()
        big, small = retriever.config.domains["NeuroML"].vector_stores

        # only load "big"; "small" stays unloaded
        big.loaded_object = FakeStore()

        # big: 2 -> 4 (k_inc=2); small is not loaded, so it is not grown
        k_values, grew = retriever.inc_k({})
        self.assertTrue(grew)
        self.assertEqual(k_values, {"NeuroML": {"big": 4}})

        # big is at k_max=4 and small is still unloaded, so nothing grows
        k_values, grew = retriever.inc_k(k_values)
        self.assertFalse(grew)
        self.assertEqual(k_values, {"NeuroML": {"big": 4}})

        # load small; inc_k() now grows it 5 -> 6, capped by its own k_max
        small.loaded_object = FakeStore()
        k_values, grew = retriever.inc_k(k_values)
        self.assertTrue(grew)
        self.assertEqual(k_values, {"NeuroML": {"big": 4, "small": 6}})

    def test_inc_k_does_not_modify_given_values(self):
        """inc_k() returns new k values and leaves the given ones untouched."""
        retriever = self._make_retriever()
        big, small = retriever.config.domains["NeuroML"].vector_stores
        big.loaded_object = FakeStore()
        small.loaded_object = FakeStore()

        k_values = {"NeuroML": {"small": 6}}
        new_values, _ = retriever.inc_k(k_values)

        self.assertEqual(k_values, {"NeuroML": {"small": 6}})
        self.assertEqual(new_values, {"NeuroML": {"big": 4, "small": 7}})

    def test_can_inc_k_reports_capacity(self):
        """can_inc_k() reports whether any loaded store has room to grow k."""
        retriever = self._make_retriever()
        big, small = retriever.config.domains["NeuroML"].vector_stores

        # only load "big"; "small" stays unloaded
        big.loaded_object = FakeStore()

        # big (2, k_max=4, k_inc=2) has room
        self.assertTrue(retriever.can_inc_k({}))

        # big at k_max and the unloaded small store does not count
        self.assertFalse(retriever.can_inc_k({"NeuroML": {"big": 4}}))

        # loading small makes can_inc_k() true again (5 -> 6 within k_max=10)
        small.loaded_object = FakeStore()
        self.assertTrue(retriever.can_inc_k({"NeuroML": {"big": 4}}))

    def test_retrieval(self):
        """Test retrieval from the configured vector and BM25 stores."""
        try:
            stores_config_file = os.environ.get(
                "STORES_TEST_CONFIG", "stores-tests.json"
            )
            with open(stores_config_file, "r") as f:
                config = json.load(f)
            logger = logging.getLogger("test_stores")
            retriever_config = RetrieverConfig(**config)

            vector_stores = VSRetriever(
                config=retriever_config,
                logger=logger,
                embedding_model="ollama:bge-m3:latest",
            )
            vector_stores.setup()
            vs_results = vector_stores.retrieve("NeuroML", "NeuroML community")
            self.assertIsNotNone(vs_results)
            logger.info(f"Vector store retrieval returned {len(vs_results)} documents")

            bm25_stores = BM25RetrieverManager(
                config=retriever_config,
                logger=logger,
            )
            bm25_stores.setup()
            bm25_results = bm25_stores.retrieve("NeuroML", "NeuroML community")
            self.assertIsNotNone(bm25_results)
            logger.info(f"BM25 retrieval returned {len(bm25_results)} documents")
        except ResponseError as e:
            pytest.skip(str(e))
        except ConnectionError as e:
            pytest.skip(str(e))


class TestBM25Retriever(unittest.TestCase):
    """Test the BM25 retriever manager with a small in-memory corpus."""

    CORPUS: ClassVar[list[Document]] = [
        Document(
            page_content="NeuroML is a language for computational neuroscience models.",
            metadata={"file_name": "a.md"},
        ),
        Document(
            page_content="The Hodgkin-Huxley model describes action "
            "potential generation.",
            metadata={"file_name": "b.md"},
        ),
        Document(
            page_content="LTP is long-term potentiation of synaptic strength.",
            metadata={"file_name": "c.md"},
        ),
    ]

    def setUp(self):
        self.logger = logging.getLogger("test_bm25")
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir_path = Path(self.tmpdir.name)
        self.corpus_path = self.tmpdir_path / "corpus.pkl"
        with open(self.corpus_path, "wb") as f:
            pickle.dump(self.CORPUS, f)
        self.logger.info(
            f"Wrote BM25 corpus with {len(self.CORPUS)} docs to {self.corpus_path}"
        )

    def tearDown(self):
        self.tmpdir.cleanup()

    def _make_manager(self, store_path: str | None = None) -> BM25RetrieverManager:
        config = RetrieverConfig(
            domains={
                "NeuroML": PerDomainConfig(
                    bm25_stores=[
                        BM25StoreInfo(
                            name="nml",
                            path=store_path or str(self.corpus_path),
                            default_k=2,
                            k_max=4,
                            k_inc=1,
                        )
                    ]
                )
            }
        )
        return BM25RetrieverManager(
            config=config,
            logger=self.logger,
            default_k=5,
            k_max=10,
            k_inc=1,
        )

    def test_retrieve_ranks_and_returns_scores(self):
        """retrieve() returns ranked documents with scores."""
        manager = self._make_manager()

        res = manager.retrieve("NeuroML", "Hodgkin Huxley action potential")
        self.logger.info(
            f"query: 'Hodgkin Huxley action potential'"
            f"\nresults: {[(d.metadata['file_name'], round(float(s), 3)) for d, s in res]}"
        )

        self.assertTrue(res)
        self.assertEqual(res[0][0].metadata["file_name"], "b.md")
        for _, score in res:
            self.assertGreater(score, 0)

    def test_retrieve_drops_zero_score_docs(self):
        """Documents with no term overlap (zero score) are dropped."""
        manager = self._make_manager()

        res = manager.retrieve("NeuroML", "zzzz qqqqqq")
        self.logger.info(f"no-overlap query returned {len(res)} documents")

        self.assertEqual(res, [])

    def test_missing_corpus_warns_and_skips(self):
        """A missing corpus file is skipped with a warning, not a crash."""
        missing_path = str(self.tmpdir_path / "nope.pkl")
        self.logger.info(f"store path (does not exist): {missing_path}")
        manager = self._make_manager(store_path=missing_path)

        res = manager.retrieve("NeuroML", "anything")
        self.logger.info(f"missing-corpus query returned {len(res)} documents")

        self.assertEqual(res, [])

    def test_batched_corpus_loads(self):
        """A batched corpus (one pickled list per batch) loads like a flat one."""
        self.logger.info("Rewriting corpus as a batched pickle (2 batches)")
        with open(self.corpus_path, "wb") as f:
            pickle.dump(self.CORPUS[:2], f)
            pickle.dump(self.CORPUS[2:], f)

        manager = self._make_manager()
        res = manager.retrieve("NeuroML", "Hodgkin Huxley action potential")
        self.logger.info(f"batched-corpus query returned {len(res)} documents")

        self.assertTrue(res)
        self.assertEqual(res[0][0].metadata["file_name"], "b.md")

    def test_inc_k(self):
        """inc_k() grows the retrieval depth of loaded stores."""
        manager = self._make_manager()
        store = manager.config.domains["NeuroML"].bm25_stores[0]

        # inc_k() only grows loaded stores, so load first
        manager.load("NeuroML")
        self.assertEqual(manager._current_k({}, "NeuroML", store), 2)
        self.logger.info(f"default k: {manager._current_k({}, 'NeuroML', store)}")

        k_values, grew = manager.inc_k({})
        self.assertTrue(grew)
        self.assertEqual(manager._current_k(k_values, "NeuroML", store), 3)
        self.logger.info(f"k after inc_k(): {k_values}")


if __name__ == "__main__":
    unittest.main()
