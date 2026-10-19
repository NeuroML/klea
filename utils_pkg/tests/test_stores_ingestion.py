#!/usr/bin/env python3
"""
Test vector store ingestion.

File: tests/test_stores_ingestion.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import gc
import json
import logging
import pickle
import tempfile
from pathlib import Path

import pytest
from klea_utils.stores.ingestion import (
    CACHE_DIR_NAME,
    TEMPLATE_FILE_NAME,
    StoresBuilder,
    _apply_store_metadata_policy,
    _ensure_doi_url,
    _first_heading_title,
    _normalize_extracted_metadata,
    _sanitize_store_metadata,
    _split_url_list,
)
from klea_utils.stores.utils import (
    expand_person_names,
    instantiate_vector_store,
    normalize_text,
)
from langchain_core.documents import Document
from ollama import ResponseError

TEST_MD_CONTENT = """# Test Document
## Section 1
This is some content in section 1. It has enough text to produce at least
one chunk for the vector store.

## Section 2
This is content in section 2. It also has enough text to produce at least
one chunk for the vector store. We need enough text here to make sure
the chunker actually produces some chunks.
"""

TEST_MD_TWO = """# Second Document
## Overview
This is another test file. It will be used to test incremental
ingestion where new files are added to an existing store.
"""


def _escape_literal(value: str) -> str:
    """Escape a string for a PDF literal-string object."""
    return value.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


def _build_pdf(path: Path, metadata: dict[str, str]) -> None:
    """Write a minimal single-page PDF with an Info dict."""
    objs: list[str] = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>",
        "<< "
        + " ".join(
            f"/{key} ({_escape_literal(value)})" for key, value in metadata.items()
        )
        + " >>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = [0] * (len(objs) + 1)
    for index, body in enumerate(objs, start=1):
        offsets[index] = len(out)
        out += f"{index} 0 obj\n{body}\nendobj\n".encode()
    xref_pos = len(out)
    out += f"xref\n0 {len(objs) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets[1:]:
        out += f"{offset:010d} 00000 n \n".encode()
    trailer = f"<< /Size {len(objs) + 1} /Root 1 0 R /Info {len(objs)} 0 R >>\n"
    out += f"trailer\n{trailer}startxref\n{xref_pos}\n%%EOF\n".encode()
    path.write_bytes(bytes(out))


def _read_bm25_corpus(path: Path) -> list[Document]:
    """Read a BM25 corpus pickle, handling both batched and legacy formats."""
    docs: list[Document] = []
    with open(path, "rb") as f:
        while True:
            try:
                docs.extend(pickle.load(f))
            except EOFError:
                break
    return docs


class TestNormalizeText:
    """Unit tests for normalize_text()."""

    def test_soft_hyphen_rejoins_split_words(self):
        """Soft hyphens (PDF line-break artifacts) are dropped in place."""
        assert normalize_text("multi-\u00adscale") == "multi-scale"
        assert (
            normalize_text("Multi-\u00adscale Model\u00ading") == "Multi-scale Modeling"
        )

    def test_no_break_spaces_become_regular_spaces(self):
        """No-break space variants map to a regular space."""
        assert normalize_text("in\u00a0neuroscience") == "in neuroscience"
        assert normalize_text("a\u2007b\u202fc") == "a b c"

    def test_zero_width_and_bom_stripped(self):
        """BOM and zero-width characters are removed entirely."""
        assert normalize_text("\ufeffleading\u200bedge") == "leadingedge"

    def test_bidi_and_variation_selectors_stripped(self):
        """Bidi marks and variation selectors are removed entirely."""
        assert normalize_text("a\u200eb\u200fc\ufe0e") == "abc"

    def test_ligatures_folded(self):
        """NFKC folds ligatures into their plain letters."""
        assert normalize_text("e\ufb00icient") == "efficient"
        assert normalize_text("\ufb01le") == "file"
        assert normalize_text("\ufb00") == "ff"

    def test_fullwidth_folded(self):
        """NFKC folds full-width forms to ASCII."""
        assert normalize_text("\uff21\uff10\uff08") == "A0("

    def test_superscripts_folded(self):
        """NFKC folds superscript digits to plain digits."""
        assert normalize_text("10\u2070\u00b9\u00b2") == "10012"

    def test_typographic_spaces_folded(self):
        """NFKC folds en/em/thin/ideographic spaces to a regular space."""
        assert normalize_text("a\u2002b\u2003c\u3000d") == "a b c d"

    def test_non_breaking_hyphen_folded(self):
        """NFKC folds the non-breaking hyphen to a regular hyphen."""
        assert normalize_text("a\u2011b") == "a\u2010b"

    def test_typographic_dashes_preserved(self):
        """Em/en dashes are meaningful punctuation and are kept unchanged."""
        assert normalize_text("a\u2013b\u2014c") == "a\u2013b\u2014c"

    def test_nfc_composition(self):
        """Canonical unicode composition (e.g. combining accents)."""
        assert normalize_text("e\u0301") == "\u00e9"
        assert normalize_text("cafe\u0301") == "caf\u00e9"

    def test_collapses_whitespace_and_strips(self):
        """Repeated spaces/tabs collapse and edges are trimmed."""
        assert normalize_text("  a\t\tb   c  ") == "a b c"

    def test_plain_text_unchanged(self):
        """Already-clean text passes through untouched."""
        assert (
            normalize_text("Plain text stays the same") == "Plain text stays the same"
        )
        assert normalize_text("") == ""

    def test_normalize_extracted_metadata(self):
        """String fields are normalized; non-string fields pass through."""
        extracted = {
            "title": "Multi-\u00adscale Model\u00ading",
            "authors": ["A\xa0Sinha", "Jane Doe"],
            "year": 2025,
            "urls": ["https://example.org/x"],
            "_metadata_complete": True,
        }
        out = _normalize_extracted_metadata(extracted)

        assert out["title"] == "Multi-scale Modeling"
        assert out["authors"] == ["A Sinha", "Jane Doe"]
        assert out["year"] == 2025
        assert out["urls"] == ["https://example.org/x"]
        assert out["_metadata_complete"] is True
        # The original dict is not mutated.
        assert extracted["title"] == "Multi-\u00adscale Model\u00ading"

    def test_split_url_list(self):
        """A urls list is expanded into url_1/url_2 keys."""
        out = _split_url_list(
            {"title": "T", "urls": ["https://a", "https://b", "https://c"]}
        )
        assert out == {
            "title": "T",
            "url_1": "https://a",
            "url_2": "https://b",
            "url_3": "https://c",
        }

    def test_split_url_list_keeps_existing_url_keys(self):
        """A singular url/source_url key is left untouched and not reused."""
        out = _split_url_list(
            {
                "url": "https://pdf",
                "source_url": "https://source",
                "urls": ["https://a", "https://b"],
            }
        )
        assert out["url"] == "https://pdf"
        assert out["source_url"] == "https://source"
        assert out["url_1"] == "https://a"
        assert out["url_2"] == "https://b"

    def test_split_url_list_skips_taken_indices(self):
        """Numbering skips indices already present in the metadata."""
        out = _split_url_list({"url_1": "https://taken", "urls": ["https://a"]})
        assert out == {"url_1": "https://taken", "url_2": "https://a"}

    def test_split_url_list_drops_empty_or_absent(self):
        """An empty/absent urls list leaves no urls keys behind."""
        assert _split_url_list({"title": "T", "urls": []}) == {"title": "T"}
        assert _split_url_list({"title": "T"}) == {"title": "T"}

    def test_ensure_doi_url_derived(self):
        """url_doi is derived from the doi field as a resolvable URL."""
        out = _ensure_doi_url({"title": "T", "doi": "10.7554/elife.95135"})
        assert out["url_doi"] == "https://doi.org/10.7554/elife.95135"
        assert out["doi"] == "10.7554/elife.95135"

    def test_ensure_doi_url_keeps_existing_url_doi(self):
        """A researcher-provided url_doi wins over the derived value."""
        out = _ensure_doi_url({"doi": "10.x/y", "url_doi": "https://custom"})
        assert out["url_doi"] == "https://custom"

    def test_ensure_doi_url_no_doi_unchanged(self):
        """No doi field means no url_doi key is added."""
        out = _ensure_doi_url({"title": "T"})
        assert out == {"title": "T"}

    def test_sanitize_store_metadata(self):
        """Empty-list and None metadata values are dropped; the rest kept."""
        meta = {
            "headings": [],
            "authors": ["X", "Y"],
            "file_name": "a.md",
            "extra": None,
            "url_doi": "https://doi.org/x",
        }
        out = _sanitize_store_metadata(meta)
        # "authors" is a person-name field, so it is expanded per name.
        assert out == {
            "authors": ["X", "x", "Y", "y"],
            "file_name": "a.md",
            "url_doi": "https://doi.org/x",
        }
        # The source dict is not mutated.
        assert meta["headings"] == []

    def test_sanitize_store_metadata_keeps_non_empty_lists(self):
        """Non-empty list values (e.g. authors) survive sanitization.

        Person-name fields are also expanded, so "X" gains its lowercase
        variant here.
        """
        assert _sanitize_store_metadata({"authors": ["X"]}) == {"authors": ["X", "x"]}

    def test_sanitize_store_metadata_drops_policy_keys(self):
        """Internal/provenance keys are dropped alongside empty values."""
        meta = {
            "title": "T",
            "journal": "J",
            "_metadata_complete": True,
            "source_path": "/x.pdf",
            "headings": [],
            "extra": None,
        }
        out = _sanitize_store_metadata(meta)
        assert out == {"title": "T", "journal": "J"}

    def test_apply_store_metadata_policy_drops_internal_and_provenance(self):
        """Underscore-prefixed and provenance keys are removed; the rest kept."""
        meta = {
            "title": "T",
            "journal": "J",
            "year": 2024,
            "_metadata_complete": True,
            "_sources": ["doi-service"],
            "_source_scores": {"vector store": 0.9},
            "source_path": "/x/y.pdf",
            "source_type": "application/pdf",
            "source_url": "https://example.com/y.pdf",
        }
        out = _apply_store_metadata_policy(meta)
        assert out == {"title": "T", "journal": "J", "year": 2024}
        # The source dict is not mutated.
        assert meta["_metadata_complete"] is True

    def test_apply_store_metadata_policy_keeps_whitelist_and_custom(self):
        """System keys, url* keys and researcher keys survive the policy."""
        meta = {
            "file_name": "a.md",
            "file_hash": "xxh64:x",
            "headings": ["S"],
            "authors": ["X"],
            "keywords": ["k"],
            "doi": "10.x/y",
            "url": "https://u",
            "url_doi": "https://doi.org/10.x/y",
            "url_orcid": "https://orcid.org/1",
            "custom_key": "researcher-value",
        }
        # "authors" is a person-name field, so it is expanded (see the
        # dedicated expansion test below); everything else passes through.
        out = _apply_store_metadata_policy(meta)
        assert out == {**meta, "authors": ["X", "x"]}

    def test_apply_store_metadata_policy_expands_person_names(self):
        """Person-name list fields gain word/lowercase variants; others don't."""
        meta = {
            "authors": ["Ankur Sinha", "Padraig Gleeson"],
            "keywords": ["cortex", "spikes"],
            "tags": ["experimental"],
        }
        out = _apply_store_metadata_policy(meta)
        expected_authors = expand_person_names(["Ankur Sinha", "Padraig Gleeson"])
        assert out["authors"] == expected_authors
        assert "Sinha" in out["authors"]
        assert "sinha" in out["authors"]
        assert "ankur" in out["authors"]
        # Non-person-name list fields are untouched.
        assert out["keywords"] == ["cortex", "spikes"]
        assert out["tags"] == ["experimental"]

    def test_sanitize_store_metadata_expands_person_names(self):
        """The final store gate applies the expansion to the stored metadata."""
        meta = {"title": "T", "authors": ["Ankur Sinha"], "headings": []}
        out = _sanitize_store_metadata(meta)
        assert out["authors"] == [
            "Ankur Sinha",
            "ankur sinha",
            "Ankur",
            "Sinha",
            "ankur",
            "sinha",
        ]
        assert "headings" not in out


class _FakeMeta:
    """Minimal stand-in for a chunk's pydantic ``meta`` object."""

    def __init__(self, headings: list[str]):
        self._headings = headings

    def model_dump(self) -> dict:
        return {"headings": self._headings}


class _FakeChunk:
    """Minimal stand-in for a Docling chunk."""

    def __init__(self, text: str, headings: list[str]):
        self._text = text
        self._headings = headings

    @property
    def meta(self) -> _FakeMeta:
        return _FakeMeta(self._headings)


class TestConvertAndChunkNormalization:
    """_convert_and_chunk normalises chunk text and headings."""

    def setup_method(self):
        self.logger = logging.getLogger("test_ingestion_normalization")

    def test_convert_and_chunk_normalizes_text_and_headings(self, monkeypatch):
        from types import SimpleNamespace

        # Skip the real biblio cascade; it operates on a Docling document
        # we are not building here.  A real title is returned so the
        # chunk-heading title fallback does not engage (it has its own
        # dedicated test).
        monkeypatch.setattr(
            "klea_utils.stores.ingestion.extract_metadata",
            lambda *args, **kwargs: {"title": "A real extracted title"},
        )

        builder = StoresBuilder(embedding_model="", logger=self.logger)

        class _FakeConverter:
            def convert(self, path):
                return SimpleNamespace(document=SimpleNamespace())

        class _FakeChunker:
            def __init__(self, chunks):
                self._chunks = chunks

            def chunk(self, dl_doc):
                return self._chunks

            def contextualize(self, chunk):
                return chunk._text

        converter = _FakeConverter()
        chunker = _FakeChunker(
            [
                _FakeChunk(
                    "multi-\u00adscale model\u00ading in\u00a0neuroscience",
                    ["Multi-\u00adscale Model\u00ading"],
                ),
                _FakeChunk(
                    "plain content",
                    ["Introduction", "eLife Assessment"],
                ),
            ]
        )
        monkeypatch.setattr(builder, "_get_converter", lambda: converter)
        monkeypatch.setattr(builder, "_get_chunker", lambda: chunker)

        docs, extracted = builder._convert_and_chunk(Path("paper.pdf"), resolver=None)

        assert extracted == {"title": "A real extracted title"}
        assert docs[0].page_content == "multi-scale modeling in neuroscience"
        assert docs[0].metadata["headings"] == ["Multi-scale Modeling"]
        assert docs[1].page_content == "plain content"
        assert docs[1].metadata["headings"] == ["Introduction", "eLife Assessment"]

    def test_convert_and_chunk_handles_none_headings(self, monkeypatch):
        """Chunks whose DocMeta.headings is None produce empty heading lists.

        docling's ``DocMeta.headings`` is ``Optional[list[str]]`` defaulting
        to ``None`` (chunks outside a heading hierarchy), so this exercises
        the real pydantic model rather than a mock that assumes a list.
        """
        from types import SimpleNamespace

        from docling_core.transforms.chunker.doc_chunk import DocChunk, DocMeta
        from docling_core.types.doc import TextItem
        from docling_core.types.doc.labels import DocItemLabel

        monkeypatch.setattr(
            "klea_utils.stores.ingestion.extract_metadata",
            lambda *args, **kwargs: {"title": "A real extracted title"},
        )

        def _doc_chunk(text: str, headings: list[str] | None) -> DocChunk:
            item = TextItem(
                self_ref="#/texts/0",
                label=DocItemLabel.TEXT,
                orig=text,
                text=text,
            )
            return DocChunk(
                text=text,
                meta=DocMeta(doc_items=[item], headings=headings),
            )

        builder = StoresBuilder(embedding_model="", logger=self.logger)

        class _FakeConverter:
            def convert(self, path):
                return SimpleNamespace(document=SimpleNamespace())

        class _FakeChunker:
            def __init__(self, chunks):
                self._chunks = chunks

            def chunk(self, dl_doc):
                return self._chunks

            def contextualize(self, chunk):
                return chunk.text

        converter = _FakeConverter()
        chunker = _FakeChunker(
            [
                _doc_chunk(
                    "multi-\u00adscale model\u00ading",
                    ["Multi-\u00adscale Model\u00ading"],
                ),
                _doc_chunk("no heading chunk", None),
            ]
        )
        monkeypatch.setattr(builder, "_get_converter", lambda: converter)
        monkeypatch.setattr(builder, "_get_chunker", lambda: chunker)

        docs, extracted = builder._convert_and_chunk(Path("paper.pdf"), resolver=None)

        assert extracted == {"title": "A real extracted title"}
        assert docs[0].metadata["headings"] == ["Multi-scale Modeling"]
        assert docs[1].page_content == "no heading chunk"
        assert docs[1].metadata["headings"] == []

    def test_convert_and_chunk_title_fallback_uses_first_heading(self, monkeypatch):
        """When the cascade falls back to the stem, the first chunk heading is used."""
        from types import SimpleNamespace

        # Empty extraction -> title missing -> chunk-heading fallback engages.
        monkeypatch.setattr(
            "klea_utils.stores.ingestion.extract_metadata",
            lambda *args, **kwargs: {},
        )

        builder = StoresBuilder(embedding_model="", logger=self.logger)

        class _FakeConverter:
            def convert(self, path):
                return SimpleNamespace(document=SimpleNamespace())

        class _FakeChunker:
            def __init__(self, chunks):
                self._chunks = chunks

            def chunk(self, dl_doc):
                return self._chunks

            def contextualize(self, chunk):
                return chunk._text

        chunker = _FakeChunker(
            [
                _FakeChunk(
                    "CONNECTOME-CONSTRAINED LATENT VARIABLE MODELS",
                    ["CONNECTOME-CONSTRAINED LATENT VARIABLE MODELS"],
                ),
                _FakeChunk("abstract text", ["ABSTRACT"]),
            ]
        )
        monkeypatch.setattr(builder, "_get_converter", lambda: _FakeConverter())
        monkeypatch.setattr(builder, "_get_chunker", lambda: chunker)

        _docs, extracted = builder._convert_and_chunk(
            Path("MiTuraga2022.pdf"), resolver=None
        )

        assert extracted["title"] == "CONNECTOME-CONSTRAINED LATENT VARIABLE MODELS"
        assert "chunk-heading" in extracted["_sources"]

    def test_convert_and_chunk_title_fallback_skips_labels(self, monkeypatch):
        """Label headings (DOI:, Highlights) are skipped for the title fallback."""
        from types import SimpleNamespace

        monkeypatch.setattr(
            "klea_utils.stores.ingestion.extract_metadata",
            lambda *args, **kwargs: {},
        )

        builder = StoresBuilder(embedding_model="", logger=self.logger)

        class _FakeConverter:
            def convert(self, path):
                return SimpleNamespace(document=SimpleNamespace())

        class _FakeChunker:
            def __init__(self, chunks):
                self._chunks = chunks

            def chunk(self, dl_doc):
                return self._chunks

            def contextualize(self, chunk):
                return chunk._text

        chunker = _FakeChunker(
            [
                _FakeChunk("citation text", []),
                _FakeChunk("doi text", ["DOI:"]),
                _FakeChunk(
                    "Potential role of a ventral nerve cord",
                    [
                        "Potential role of a ventral nerve cord central pattern generator"
                    ],
                ),
            ]
        )
        monkeypatch.setattr(builder, "_get_converter", lambda: _FakeConverter())
        monkeypatch.setattr(builder, "_get_chunker", lambda: chunker)

        _docs, extracted = builder._convert_and_chunk(
            Path("Olivares2017.pdf"), resolver=None
        )

        assert (
            extracted["title"]
            == "Potential role of a ventral nerve cord central pattern generator"
        )


class TestFirstHeadingTitle:
    """_first_heading_title falls back to the first non-label heading."""

    def _docs(self, *heading_lists):
        docs = []
        for heads in heading_lists:
            docs.append(
                Document(
                    page_content="content",
                    metadata={"headings": heads} if heads else {},
                )
            )
        return docs

    def test_uses_first_non_empty_heading(self):
        assert (
            _first_heading_title(
                self._docs([], ["DOI:"], ["Potential role of a ventral nerve cord"])
            )
            == "Potential role of a ventral nerve cord"
        )

    def test_skips_label_headings(self):
        assert _first_heading_title(
            self._docs(["Highlights"], ["ABSTRACT"], ["Real Title"])
        ) == ("Real Title")

    def test_no_headings_returns_none(self):
        assert _first_heading_title(self._docs([], [])) is None

    def test_all_labels_returns_none(self):
        assert _first_heading_title(self._docs(["Review"], ["DOI:"])) is None


class TestIngestion:
    """Test vector store ingestion."""

    def setup_method(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.tmpdir_path = Path(self.tmpdir.name)
        self.logger = logging.getLogger("test_ingestion")

    def teardown_method(self):
        # Chroma keeps its index files open, and Windows cannot delete open
        # files: stop the cached Chroma systems before removing the store.
        try:
            from chromadb.api.shared_system_client import SharedSystemClient
        except ImportError:
            pass
        else:
            SharedSystemClient.clear_system_cache()
        gc.collect()
        self.tmpdir.cleanup()

    def _use_in_process_workers(self, monkeypatch):
        """Replace the worker-spawn seam with an in-process worker run.

        Conversion always happens in subprocess workers now; tests swap
        the spawn seam for a direct :func:`convert_batch_worker` call so
        they stay fast and hermetic while still exercising the real
        conversion + caching code.
        """

        def fake_dispatch(builder, pending, config, batch_size):
            from klea_utils.stores.chunk_worker import convert_batch_worker

            return convert_batch_worker(config, pending)

        monkeypatch.setattr(
            StoresBuilder, "_dispatch_conversion_batches", fake_dispatch
        )

    def test_prune_cache_removes_orphans_keeps_current(self):
        """Prune removes entries whose hash matches no current file."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        current = "xxh64:abcdef1234567890"
        for name in (
            "xxh64_abcdef1234567890.pkl",  # matches a current file
            "xxh64_deadbeefdeadbeef.pkl",  # orphan (renamed/removed file)
            "xxh64_0123456789abcdef.pkl",  # orphan (changed file)
        ):
            (cache_dir / name).write_bytes(b"stale")
        # Non-chunk-cache files must be left untouched.
        doi_cache = cache_dir / "doi-cache.json"
        doi_cache.write_text('{"10.1234/test": {}}')
        unrelated = cache_dir / "readme.txt"
        unrelated.write_text("not cache data")

        builder._prune_cache(self.tmpdir_path, {current})

        assert (cache_dir / "xxh64_abcdef1234567890.pkl").exists()
        assert not (cache_dir / "xxh64_deadbeefdeadbeef.pkl").exists()
        assert not (cache_dir / "xxh64_0123456789abcdef.pkl").exists()
        assert doi_cache.exists()
        assert unrelated.exists()

    def test_prune_cache_no_cache_dir_is_noop(self):
        """Prune on a source dir without a cache directory does nothing."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._prune_cache(self.tmpdir_path, {"xxh64:abcdef1234567890"})

    def test_prune_cache_all_current_keeps_everything(self):
        """No entries are removed when every cache file is current."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        current = {"xxh64:abc", "xxh64:def"}
        for name in ("xxh64_abc.pkl", "xxh64_def.pkl"):
            (cache_dir / name).write_bytes(b"ok")

        builder._prune_cache(self.tmpdir_path, current)

        assert (cache_dir / "xxh64_abc.pkl").exists()
        assert (cache_dir / "xxh64_def.pkl").exists()

    @pytest.mark.localonly
    def test_build_chroma(self):
        """Test building a chroma store from a source directory."""
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)
        self.logger.info(f"Wrote test file: {md_file}")

        store_dir = self.tmpdir_path / "chroma_store"
        store_uri = f"chroma:{store_dir}"
        collection_name = "test_build"
        self.logger.info(f"Store URI: {store_uri}, collection: {collection_name}")

        try:
            builder = StoresBuilder(
                embedding_model="ollama:bge-m3:latest",
                logger=self.logger,
            )
            self.logger.info("Builder set up with Ollama embeddings")
            builder.build(
                source_dir=str(self.tmpdir_path),
                store_uri=store_uri,
                collection_name=collection_name,
            )
            self.logger.info("Build completed")

            store = instantiate_vector_store(
                store_uri, collection_name, builder.embeddings, self.logger
            )
            result = store.get()
            self.logger.info(f"Store contains {len(result['ids'])} chunks")
            assert result["ids"], "No documents found in store"
            assert len(result["ids"]) > 0

            hnsw_space = store._chroma_collection.configuration["hnsw"]["space"]
            self.logger.info(f"Chroma HNSW space: {hnsw_space}")
            assert hnsw_space == "cosine"

            first_meta = result["metadatas"][0]
            assert "file_hash" in first_meta
            assert "file_name" in first_meta
            assert "headings" in first_meta
            assert first_meta["file_name"] == "test.md"
            self.logger.info(
                f"First chunk metadata verified: headings={first_meta['headings']}"
            )

        except ResponseError as e:
            pytest.skip(str(e))
        except ConnectionError as e:
            pytest.skip(str(e))

    @pytest.mark.localonly
    def test_build_idempotent(self):
        """Test that building twice produces the same result."""
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)
        self.logger.info(f"Wrote test file: {md_file}")

        store_dir = self.tmpdir_path / "chroma_store"
        store_uri = f"chroma:{store_dir}"
        collection_name = "test_idempotent"

        try:
            builder = StoresBuilder(
                embedding_model="ollama:bge-m3:latest",
                logger=self.logger,
            )
            self.logger.info("Builder set up with Ollama embeddings")

            builder.build(
                source_dir=str(self.tmpdir_path),
                store_uri=store_uri,
                collection_name=collection_name,
            )
            self.logger.info("First build completed")

            store = instantiate_vector_store(
                store_uri, collection_name, builder.embeddings, self.logger
            )
            first_result = store.get()
            first_count = len(first_result["metadatas"])
            self.logger.info(f"First build: {first_count} chunks")

            hnsw_space = store._chroma_collection.configuration["hnsw"]["space"]
            self.logger.info(f"Chroma HNSW space: {hnsw_space}")
            assert hnsw_space == "cosine"

            builder.build(
                source_dir=str(self.tmpdir_path),
                store_uri=store_uri,
                collection_name=collection_name,
            )
            self.logger.info("Second (idempotent) build completed")

            second_result = store.get()
            second_count = len(second_result["metadatas"])
            self.logger.info(f"Second build: {second_count} chunks")

            assert first_count == second_count, (
                f"Expected same count after idempotent build "
                f"({first_count} != {second_count})"
            )
            self.logger.info("Idempotency verified -- counts match")

        except ResponseError as e:
            pytest.skip(str(e))
        except ConnectionError as e:
            pytest.skip(str(e))

    @pytest.mark.localonly
    def test_build_incremental(self):
        """Test adding a new file after initial build."""
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)
        self.logger.info(f"Wrote initial test file: {md_file}")

        store_dir = self.tmpdir_path / "chroma_store"
        store_uri = f"chroma:{store_dir}"
        collection_name = "test_incremental"

        try:
            builder = StoresBuilder(
                embedding_model="ollama:bge-m3:latest",
                logger=self.logger,
            )
            self.logger.info("Builder set up with Ollama embeddings")

            builder.build(
                source_dir=str(self.tmpdir_path),
                store_uri=store_uri,
                collection_name=collection_name,
            )
            self.logger.info("Initial build completed")

            store = instantiate_vector_store(
                store_uri, collection_name, builder.embeddings, self.logger
            )
            first_count = len(store.get()["metadatas"])
            self.logger.info(f"Initial build: {first_count} chunks")

            md_two = self.tmpdir_path / "another.md"
            md_two.write_text(TEST_MD_TWO)
            self.logger.info(f"Added new test file: {md_two}")

            builder.build(
                source_dir=str(self.tmpdir_path),
                store_uri=store_uri,
                collection_name=collection_name,
            )
            self.logger.info("Incremental build completed")

            second_count = len(store.get()["metadatas"])
            self.logger.info(f"Incremental build: {second_count} chunks")

            assert second_count > first_count, (
                f"Expected more chunks after incremental build "
                f"({second_count} <= {first_count})"
            )
            self.logger.info(
                f"Incremental verified -- added {second_count - first_count} more chunks"
            )

        except ResponseError as e:
            pytest.skip(str(e))
        except ConnectionError as e:
            pytest.skip(str(e))

    def test_do_ocr_defaults_to_true(self):
        """OCR is enabled by default, preserving the previous behaviour."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder.do_ocr is True

    def test_do_ocr_false_constructs_converter(self):
        """do_ocr=False is stored and builds a converter without error."""
        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        assert builder.do_ocr is False
        converter = builder._get_converter()
        assert converter is not None
        assert converter is builder._get_converter()

    def test_cache_round_trips_extracted_metadata(self):
        """Cached entries restore both chunks and extracted metadata."""
        doc = Document(
            page_content="Some content.", metadata={"headings": ["Section 1"]}
        )
        extracted = {
            "title": "T",
            "_metadata_complete": True,
            "_sources": ["docling"],
        }
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._save_to_cache([doc], extracted, self.tmpdir_path, "xxh64:abc")

        loaded = builder._load_from_cache(self.tmpdir_path, "xxh64:abc")
        assert loaded is not None
        docs, restored = loaded
        assert docs[0].page_content == "Some content."
        assert restored == extracted

    def test_cache_load_handles_legacy_format(self):
        """Legacy plain-list cache entries load with empty extraction."""
        doc = Document(page_content="Legacy.", metadata={})
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        with open(cache_dir / "xxh64_legacy.pkl", "wb") as f:
            pickle.dump([doc], f)

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        loaded = builder._load_from_cache(self.tmpdir_path, "xxh64:legacy")
        assert loaded == ([doc], {})

    def test_cache_load_moves_corrupt_empty_entry_aside(self):
        """A 0-byte cache entry (torn write) loads as None and is moved aside."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        (cache_dir / "xxh64_empty.pkl").write_bytes(b"")

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder._load_from_cache(self.tmpdir_path, "xxh64:empty") is None
        assert not (cache_dir / "xxh64_empty.pkl").exists()
        assert (cache_dir / "xxh64_empty.pkl.corrupt").read_bytes() == b""

    def test_cache_load_moves_corrupt_truncated_entry_aside(self):
        """A non-empty but corrupt cache entry loads as None and is moved aside."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        payload = b"\x80\x04\x95garbage"
        (cache_dir / "xxh64_trunc.pkl").write_bytes(payload)

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder._load_from_cache(self.tmpdir_path, "xxh64:trunc") is None
        assert not (cache_dir / "xxh64_trunc.pkl").exists()
        assert (cache_dir / "xxh64_trunc.pkl.corrupt").read_bytes() == payload

    def test_cache_save_is_atomic_and_valid(self):
        """Save writes atomically: a valid entry and no stray temp files."""
        doc = Document(page_content="Atomic.", metadata={"headings": ["A"]})
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._save_to_cache([doc], {"title": "T"}, self.tmpdir_path, "xxh64:atom")

        loaded = builder._load_from_cache(self.tmpdir_path, "xxh64:atom")
        assert loaded is not None
        docs, extracted = loaded
        assert docs[0].page_content == "Atomic."
        assert extracted == {"title": "T"}

        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        assert [p.name for p in cache_dir.glob("*.tmp")] == []

    def test_prune_cache_cleans_healed_corrupt_artifacts(self):
        """A .corrupt artifact is pruned once a valid entry is regenerated."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        (cache_dir / "xxh64_healed.pkl").write_bytes(b"valid")
        (cache_dir / "xxh64_healed.pkl.corrupt").write_bytes(b"")

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._prune_cache(self.tmpdir_path, {"xxh64:healed"})
        assert (cache_dir / "xxh64_healed.pkl").exists()
        assert not (cache_dir / "xxh64_healed.pkl.corrupt").exists()

    def test_prune_cache_keeps_corrupt_artifact_without_regenerated_entry(self):
        """A .corrupt artifact is kept while its source lacks a cache entry."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        (cache_dir / "xxh64_broken.pkl.corrupt").write_bytes(b"")

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._prune_cache(self.tmpdir_path, {"xxh64:broken"})
        assert (cache_dir / "xxh64_broken.pkl.corrupt").exists()

    def test_prune_cache_removes_orphaned_corrupt_artifacts(self):
        """A .corrupt artifact whose source file is gone is pruned."""
        cache_dir = self.tmpdir_path / CACHE_DIR_NAME
        cache_dir.mkdir()
        (cache_dir / "xxh64_orphan.pkl.corrupt").write_bytes(b"")

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._prune_cache(self.tmpdir_path, {"xxh64:somethingelse"})
        assert not (cache_dir / "xxh64_orphan.pkl.corrupt").exists()

    @pytest.mark.localonly
    def test_chunk_all_prefills_metadata_template(self, monkeypatch):
        """chunk_all pre-fills the per-file DEFAULT template entry.

        Runs the biblio extraction cascade on a real PDF conversion: the
        PDF Info dict (title/author/keywords) lands in the DEFAULT entry
        along with the internal ``_metadata_complete`` / ``_sources``
        flags.  A re-run hits the cache and must restore the same
        extraction rather than degrading to regex-only.
        """
        _build_pdf(
            self.tmpdir_path / "paper.pdf",
            {
                "Title": "Synthetic Title",
                "Author": "Jane Doe",
                "Keywords": "alpha, beta",
            },
        )

        # Force Docling onto the CPU: the layout model has no kernel for
        # this machine's GPU (Quadro P1000, CC 6.1) and would otherwise
        # raise CUDA error: no kernel image is available.
        monkeypatch.setenv("DOCLING_DEVICE", "cpu")
        monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
        self._use_in_process_workers(monkeypatch)
        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        file_headings = builder.chunk_all(self.tmpdir_path)

        default = file_headings["paper.pdf"]["DEFAULT"]
        self.logger.info(f"pre-filled DEFAULT: {default}")
        assert default["title"] == "Synthetic Title"
        assert default["authors"] == ["Jane Doe"]
        assert default["keywords"] == ["alpha", "beta"]
        assert default["_metadata_complete"] is True
        assert "pdf-info" in default["_sources"]

        # Cache hit: the persisted full extraction is restored unchanged.
        file_headings2 = builder.chunk_all(self.tmpdir_path)
        assert file_headings2["paper.pdf"]["DEFAULT"] == default

    def test_load_metadata_map_normalizes_heading_keys(self):
        """Heading keys with typographic artifacts are normalized on load.

        Users may paste keys (e.g. from a PDF) containing soft hyphens or
        no-break spaces; they must still match the normalized chunk headings.
        """
        map_path = self.tmpdir_path / "metadata-map.json"
        map_path.write_text(
            json.dumps(
                {
                    "DEFAULT": {"topic": "fallback"},
                    "Multi-\u00adscale Model\u00ading in\u00a0neuroscience": {
                        "topic": "nml"
                    },
                }
            )
        )
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        loaded = builder._load_metadata_map(str(map_path))

        assert loaded["DEFAULT"] == {"topic": "fallback"}
        assert loaded["Multi-scale Modeling in neuroscience"] == {"topic": "nml"}
        assert (
            loaded.get("Multi-\u00adscale Model\u00ading in\u00a0neuroscience") is None
        )

    def test_resolve_metadata_map_explicit_wins(self):
        """An explicit metadata-map path beats the template fallback."""
        explicit = self.tmpdir_path / "explicit.json"
        explicit.write_text(json.dumps({"test.md": {"DEFAULT": {"url": "explicit"}}}))
        template = self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(json.dumps({"test.md": {"DEFAULT": {"url": "template"}}}))

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        out = builder._resolve_metadata_map(self.tmpdir_path, str(explicit))
        assert out is not None
        assert out["test.md"]["DEFAULT"] == {"url": "explicit"}

    def test_resolve_metadata_map_falls_back_to_template(self):
        """Without an explicit path, the template map is used when present."""
        template = self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(json.dumps({"test.md": {"DEFAULT": {"url": "template"}}}))

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        out = builder._resolve_metadata_map(self.tmpdir_path, None)
        assert out is not None
        assert out["test.md"]["DEFAULT"] == {"url": "template"}

    def test_resolve_metadata_map_none_without_map(self):
        """No explicit path and no template resolves to None.

        build() handles this case by generating the template internally; the
        store CLI treats it as an error (store consumes what chunk wrote).
        """
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder._resolve_metadata_map(self.tmpdir_path, None) is None

    def test_resolve_metadata_map_errors_on_empty_explicit_map(self):
        """An explicit --metadata-map that is {} carries no metadata."""
        explicit = self.tmpdir_path / "explicit.json"
        explicit.write_text(json.dumps({}))

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        with pytest.raises(ValueError, match="no entries"):
            builder._resolve_metadata_map(self.tmpdir_path, str(explicit))

    def test_resolve_metadata_map_errors_on_empty_template(self):
        """An empty template map is treated the same as no map at all."""
        template = self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(json.dumps({}))

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        with pytest.raises(ValueError, match="no entries"):
            builder._resolve_metadata_map(self.tmpdir_path, None)

    def test_build_generates_template_when_no_map(self, monkeypatch):
        """build() is one-shot: with no map given it generates and consumes one.

        The chunk phase produces the map (exactly what ``chunk`` would write
        to metadata-map.template.json) and the store phase then consumes it,
        so a plain ``build`` needs no prior ``chunk`` and no ``--metadata-map``
        -- it just skips the human review step.
        """
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        captured: dict = {}

        def _fake_chunk_all(
            source_path,
            force=False,
            worker_mem_limit=None,
            worker_batch_size=200,
        ):
            captured["chunk_kwargs"] = {
                "force": force,
                "worker_mem_limit": worker_mem_limit,
            }
            # Cache-only worker shape: only the headings come back.
            return {"test.md": {"DEFAULT": {"journal": "Journal of X"}}}

        def _fake_load_and_fold(source_path, metadata_map, strict=True):
            captured["store_map"] = metadata_map
            captured["fold_strict"] = strict
            doc = Document(page_content="x", metadata={"headings": []})
            yield ("xxh64:x", [doc], Path("test.md"))

        def _fake_store_all(
            results,
            store_uri,
            collection_name,
            source_dir,
            force=False,
            bm25_path=None,
        ):
            captured["stored"] = list(results)

        monkeypatch.setattr(builder, "chunk_all", _fake_chunk_all)
        monkeypatch.setattr(builder, "_load_and_fold_results", _fake_load_and_fold)
        monkeypatch.setattr(builder, "store_all", _fake_store_all)
        builder.build(
            source_dir=str(self.tmpdir_path),
            store_uri="chroma:/tmp/x",
            collection_name="c",
        )

        # The chunk phase ran once, cache-only, with the default worker limit.
        assert captured["chunk_kwargs"]["worker_mem_limit"] == 4 * 1024**3
        # The generated template was consumed by the store phase.
        assert captured["store_map"] is not None
        assert captured["store_map"]["test.md"]["DEFAULT"]["journal"] == "Journal of X"
        assert captured["fold_strict"] is False
        assert len(captured["stored"]) == 1
        # The template was written to the cache folder so it can be reviewed
        # for a later store.
        assert (self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME).is_file()

    def test_build_uses_tolerant_fold_phase(self, tmp_path, monkeypatch):
        """build's store phase skips uncached files instead of aborting.

        A file whose conversion failed has no cache entry; ``build`` logs
        and stores the rest rather than raising like the strict ``store``
        command does.
        """
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        seen: dict = {}

        def _fake_chunk_all(*args, **kwargs):
            return {"test.md": {"DEFAULT": {}}}

        def _fake_load_and_fold(source_path, metadata_map, strict=True):
            seen["strict"] = strict
            return iter([])

        def _fake_store_all(results, *args, **kwargs):
            seen["stored"] = list(results)

        monkeypatch.setattr(builder, "chunk_all", _fake_chunk_all)
        monkeypatch.setattr(builder, "_load_and_fold_results", _fake_load_and_fold)
        monkeypatch.setattr(builder, "store_all", _fake_store_all)

        # An explicit map keeps the test focused on fold-phase tolerance
        # (otherwise build would generate and load the template first).
        map_path = tmp_path / "map.json"
        map_path.write_text(json.dumps({"test.md": {"DEFAULT": {}}}))
        builder.build(
            source_dir=str(tmp_path),
            store_uri="chroma:/tmp/x",
            collection_name="c",
            metadata_map_path=str(map_path),
        )
        assert seen["strict"] is False

    def test_resolve_metadata_matches_normalized_headings(self):
        """_resolve_metadata matches normalized chunk headings to map keys."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Multi-scale Modeling"],
            {
                "paper.pdf": {
                    "DEFAULT": {"topic": "fallback"},
                    "Multi-scale Modeling": {"topic": "nml"},
                }
            },
        )
        assert meta == {"topic": "nml"}

    def test_resolve_metadata_empty_heading_falls_through_to_default(self):
        """An empty heading placeholder must not strip the DEFAULT metadata."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Introduction"],
            {
                "paper.pdf": {
                    "DEFAULT": {"topic": "fallback", "year": 2020},
                    "Introduction": {},
                }
            },
        )
        assert meta == {"topic": "fallback", "year": 2020}

    def test_resolve_metadata_empty_heading_falls_to_later_heading(self):
        """Empty headings fall through to a more specific (later) match."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Intro", "Methods", "Results"],
            {
                "paper.pdf": {
                    "DEFAULT": {"topic": "fallback"},
                    "Intro": {},
                    "Methods": {},
                    "Results": {"topic": "results"},
                }
            },
        )
        assert meta == {"topic": "results"}

    def test_resolve_metadata_empty_everything_returns_default(self):
        """When every matching heading is empty, DEFAULT is returned."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Intro"],
            {
                "paper.pdf": {
                    "DEFAULT": {"topic": "fallback"},
                    "Intro": {},
                }
            },
        )
        assert meta == {"topic": "fallback"}

    def test_resolve_metadata_filled_heading_beats_empty_default(self):
        """A filled heading entry wins over an empty DEFAULT."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Intro"],
            {
                "paper.pdf": {
                    "DEFAULT": {},
                    "Intro": {"url": "https://example.com/intro"},
                }
            },
        )
        assert meta == {"url": "https://example.com/intro"}

    def test_resolve_metadata_all_empty_returns_empty_dict(self):
        """When the file exists but has no metadata anywhere, return {}."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Intro"],
            {
                "paper.pdf": {
                    "DEFAULT": {},
                    "Intro": {},
                }
            },
        )
        assert meta == {}

    def test_resolve_metadata_merges_heading_url_over_default(self):
        """A heading that only sets url keeps the DEFAULT bibliographic fields."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Introduction"],
            {
                "paper.pdf": {
                    "DEFAULT": {
                        "authors": ["A. Sinha"],
                        "year": 2020,
                        "journal": "eLife",
                    },
                    "Introduction": {"url": "https://example.com/intro"},
                }
            },
        )
        assert meta == {
            "authors": ["A. Sinha"],
            "year": 2020,
            "journal": "eLife",
            "url": "https://example.com/intro",
        }

    def test_resolve_metadata_heading_overrides_default_field(self):
        """A heading-specific key wins over the DEFAULT value for that key."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Methods"],
            {
                "paper.pdf": {
                    "DEFAULT": {"url": "https://example.com/paper"},
                    "Methods": {"url": "https://example.com/methods"},
                }
            },
        )
        assert meta == {"url": "https://example.com/methods"}

    def test_resolve_metadata_merge_only_for_first_nonempty_heading(self):
        """Empty headings fall through before the merge happens."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Intro", "Methods"],
            {
                "paper.pdf": {
                    "DEFAULT": {"authors": ["A. Sinha"], "year": 2020},
                    "Intro": {},
                    "Methods": {"url": "https://example.com/methods"},
                }
            },
        )
        assert meta == {
            "authors": ["A. Sinha"],
            "year": 2020,
            "url": "https://example.com/methods",
        }

    def test_resolve_metadata_matches_full_template_chain_key(self):
        """A template-style full heading-chain key resolves for a deep chunk."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Chapter 1", "2.1 Neurons"],
            {
                "paper.pdf": {
                    "DEFAULT": {"authors": ["A. Sinha"], "year": 2020},
                    "Chapter 1 > 2.1 Neurons": {"url": "https://e.com/section"},
                }
            },
        )
        assert meta == {
            "authors": ["A. Sinha"],
            "year": 2020,
            "url": "https://e.com/section",
        }

    def test_resolve_metadata_deeper_chain_beats_shallower(self):
        """The most specific populated chain entry wins."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Chapter 1", "2.1 Neurons"],
            {
                "paper.pdf": {
                    "DEFAULT": {},
                    "Chapter 1": {"url": "https://e.com/chapter"},
                    "Chapter 1 > 2.1 Neurons": {"url": "https://e.com/section"},
                }
            },
        )
        assert meta == {"url": "https://e.com/section"}

    def test_resolve_metadata_empty_leaf_inherits_ancestor(self):
        """A leaf section with no metadata inherits its nearest ancestor's."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Chapter 1", "2.1 Neurons"],
            {
                "paper.pdf": {
                    "DEFAULT": {"authors": ["A. Sinha"]},
                    "Chapter 1 > 2.1 Neurons": {},
                    "Chapter 1": {"url": "https://e.com/chapter"},
                }
            },
        )
        assert meta == {"authors": ["A. Sinha"], "url": "https://e.com/chapter"}

    def test_resolve_metadata_shorter_suffix_beats_ancestor(self):
        """A shorter suffix (closer to the leaf) beats a shallower ancestor."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        meta = builder._resolve_metadata(
            "paper.pdf",
            ["Chapter 1", "2", "2.1 Neurons"],
            {
                "paper.pdf": {
                    "DEFAULT": {},
                    "Chapter 1": {"url": "https://e.com/chapter"},
                    "2 > 2.1 Neurons": {"url": "https://e.com/subsection"},
                }
            },
        )
        assert meta == {"url": "https://e.com/subsection"}

    def test_fold_metadata_map_raises_when_file_missing(self):
        """The map must contain every ingested file (keyed by filename).

        A map not keyed by the source filename (e.g. the flat heading-keyed
        url-map format from the single-page doc generator) must abort
        ingestion so the misconfiguration is impossible to miss.
        """
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        docs = [
            Document(page_content="chunk", metadata={"headings": ["Intro"]}),
        ]

        # Deliberately keyed by a filename that does not exist in the source
        # directory, mirroring the flat url-map format that has no per-file
        # wrapper at all.
        metadata_map = {"other.md": {"DEFAULT": {"url": "https://example.com"}}}
        with pytest.raises(ValueError, match="test.md"):
            builder._fold_metadata_map(Path("test.md"), docs, metadata_map)

    def test_fold_metadata_map_strips_internal_keys(self):
        """Internal/provenance keys from the map are not folded into chunks.

        The template's pre-filled DEFAULT carries ``_metadata_complete`` and
        ``_sources`` (extraction provenance) plus ``source_type``; folding
        must strip them so neither the vector store nor the BM25 corpus
        (which shares these chunk objects) leaks them.
        """
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        docs = [
            Document(page_content="chunk one", metadata={"headings": ["Intro"]}),
            Document(page_content="chunk two", metadata={"headings": ["Intro"]}),
        ]

        metadata_map = {
            "test.md": {
                "DEFAULT": {
                    "title": "T",
                    "journal": "Journal of X",
                    "_metadata_complete": True,
                    "_sources": ["docling"],
                    "source_type": "text/markdown",
                }
            }
        }
        builder._fold_metadata_map(Path("test.md"), docs, metadata_map)
        for doc in docs:
            assert doc.metadata["title"] == "T"
            assert doc.metadata["journal"] == "Journal of X"
            assert "_metadata_complete" not in doc.metadata
            assert "_sources" not in doc.metadata
            assert "source_type" not in doc.metadata

    def test_fold_metadata_map_warns_when_nothing_resolves(self, caplog):
        """A map entry that resolves no metadata is worth a warning.

        A file present in the map whose DEFAULT is empty (and whose headings
        match no entry) contributes no metadata -- that is a legitimate
        researcher choice, but worth a warning so an accidentally-empty entry
        is easy to spot.
        """
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        docs = [
            Document(page_content="chunk", metadata={"headings": ["Intro"]}),
        ]

        metadata_map = {"test.md": {"DEFAULT": {}}}
        with caplog.at_level(logging.WARNING):
            builder._fold_metadata_map(Path("test.md"), docs, metadata_map)

        assert "No metadata resolved for test.md" in caplog.text

    def test_fold_metadata_map_no_warning_when_resolves(self, caplog):
        """Folding stays quiet when the metadata map resolves for a file."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        docs = [
            Document(page_content="chunk", metadata={"headings": ["Intro"]}),
        ]

        metadata_map = {"test.md": {"DEFAULT": {"url": "https://example.com"}}}
        with caplog.at_level(logging.WARNING):
            builder._fold_metadata_map(Path("test.md"), docs, metadata_map)

        assert "No metadata resolved" not in caplog.text

    def test_load_and_fold_results_raises_on_uncached_file(self):
        """_load_and_fold_results refuses to load a file with no cache entry."""
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        with pytest.raises(ValueError, match="test.md.*cache"):
            # The generator raises only once iterated (store_all does this).
            list(builder._load_and_fold_results(self.tmpdir_path, None))

    def test_load_and_fold_skips_uncached_when_not_strict(self, caplog):
        """strict=False skips a missing cache entry instead of raising.

        :meth:`StoresBuilder.build` uses this: a file whose conversion
        failed is logged and skipped so the rest of the corpus is stored.
        """
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        with caplog.at_level(logging.ERROR):
            results = list(
                builder._load_and_fold_results(self.tmpdir_path, None, strict=False)
            )

        assert results == []
        assert "No cache entry for test.md" in caplog.text

    def test_load_and_fold_results_succeeds_when_all_cached(self):
        """_load_and_fold_results passes when every file is already cached."""
        from klea_utils.stores.ingestion import _hash_file

        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        # Populate the cache directly (a converted chunk as the worker
        # would write it).
        doc = Document(page_content="cached", metadata={"headings": ["S"]})
        builder._save_to_cache([doc], {}, self.tmpdir_path, _hash_file(md_file))

        results = list(builder._load_and_fold_results(self.tmpdir_path, None))
        assert len(results) == 1
        assert results[0][2].name == "test.md"

    def test_load_and_fold_results_applies_metadata_map(self):
        """_load_and_fold_results folds the metadata map into cached chunks."""
        from klea_utils.stores.ingestion import _hash_file

        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        doc = Document(page_content="cached", metadata={"headings": ["S"]})
        builder._save_to_cache([doc], {}, self.tmpdir_path, _hash_file(md_file))

        metadata_map = {"test.md": {"DEFAULT": {"year": 2020, "journal": "J"}}}
        results = list(builder._load_and_fold_results(self.tmpdir_path, metadata_map))
        assert len(results) == 1
        for doc in results[0][1]:
            assert doc.metadata["year"] == 2020
            assert doc.metadata["journal"] == "J"

    def test_chunk_all_converts_uncached_files(self, monkeypatch):
        """Uncached files are converted in workers and cached."""
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        self._use_in_process_workers(monkeypatch)
        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        file_headings = builder.chunk_all(self.tmpdir_path)
        assert set(file_headings) == {"test.md"}
        assert (self.tmpdir_path / CACHE_DIR_NAME).is_dir()

    def test_chunk_all_worker_mode_cached_in_parent_dispatches_uncached(
        self, monkeypatch
    ):
        """Worker mode builds cached entries in-parent, dispatches the rest.

        The worker path (``worker_mem_limit`` set, no metadata map) must
        handle cache hits in the parent -- never spawning a worker for
        them -- and hand only the uncached files to the conversion
        dispatcher.  The dispatch seam is replaced with an in-process run
        so no subprocess is spawned.  The mem limit is huge so the
        in-process worker never defers (the cap itself is tested
        separately with a mocked RSS probe).
        """
        md1 = self.tmpdir_path / "one.md"
        md1.write_text(TEST_MD_CONTENT)

        self._use_in_process_workers(monkeypatch)
        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        # Cache one.md via the plain in-process path first.
        builder.chunk_all(self.tmpdir_path)

        # Distinct content so two.md gets its own hash/cache entry.
        md2 = self.tmpdir_path / "two.md"
        md2.write_text(TEST_MD_TWO)

        dispatched: dict[str, list[tuple[str, str]]] = {}

        def fake_dispatch(self, pending, config, batch_size):
            from klea_utils.stores.chunk_worker import convert_batch_worker

            dispatched["items"] = list(pending)
            return convert_batch_worker(config, pending)

        monkeypatch.setattr(
            StoresBuilder, "_dispatch_conversion_batches", fake_dispatch
        )
        file_headings = builder.chunk_all(
            self.tmpdir_path,
            worker_mem_limit=10**15,
            worker_batch_size=10,
        )

        assert set(file_headings) == {"one.md", "two.md"}
        # Only the uncached file reached the dispatcher.
        assert [Path(p).name for p, _ in dispatched["items"]] == ["two.md"]

    def test_chunk_all_worker_mode_force_dispatches_all(self, monkeypatch):
        """--force sends every file to the workers, none handled in-parent."""
        md1 = self.tmpdir_path / "one.md"
        md1.write_text(TEST_MD_CONTENT)
        md2 = self.tmpdir_path / "two.md"
        md2.write_text(TEST_MD_CONTENT)

        self._use_in_process_workers(monkeypatch)
        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        # Cache both first so the test proves force ignores the cache.
        builder.chunk_all(self.tmpdir_path)

        dispatched: dict[str, list[tuple[str, str]]] = {}

        def fake_dispatch(self, pending, config, batch_size):
            from klea_utils.stores.chunk_worker import convert_batch_worker

            dispatched["items"] = list(pending)
            return convert_batch_worker(config, pending)

        monkeypatch.setattr(
            StoresBuilder, "_dispatch_conversion_batches", fake_dispatch
        )
        file_headings = builder.chunk_all(
            self.tmpdir_path,
            force=True,
            worker_mem_limit=10**15,
        )

        assert set(file_headings) == {"one.md", "two.md"}
        assert sorted(Path(p).name for p, _ in dispatched["items"]) == [
            "one.md",
            "two.md",
        ]

    def test_load_and_fold_results_warns_on_empty_cached_chunks(self, caplog):
        """_load_and_fold_results warns for a cache entry with zero chunks."""
        from klea_utils.stores.ingestion import _hash_file

        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        # Cache an empty-chunk entry directly (simulates a scanned PDF that
        # was converted to nothing), then load it on the store path.
        builder._save_to_cache([], {}, self.tmpdir_path, _hash_file(md_file))

        with caplog.at_level(logging.WARNING):
            # Iterate the generator so the per-file warnings are emitted.
            list(builder._load_and_fold_results(self.tmpdir_path, None))

        assert "No cached chunks for test.md" in caplog.text

    def test_write_heading_template_preserves_existing_when_empty(self):
        """An empty chunk run must not clobber an existing template."""
        existing = {"paper.pdf": {"DEFAULT": {"title": "T"}}}
        template = self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text(json.dumps(existing))

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.write_heading_template({}, self.tmpdir_path)

        assert json.loads(template.read_text()) == existing

    def test_write_heading_template_writes_literal_utf8(self):
        """Accented characters are written literally, not \\u-escaped.

        The template is a human-edited file; an author such as "B\u00f3ris
        Marin" must appear as-is so the editor can see the exact text.
        """
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.write_heading_template(
            {
                "paper.pdf": {
                    "DEFAULT": {
                        "title": "Caf\u00e9 science",
                        "authors": ["B\u00f3ris Marin"],
                    }
                }
            },
            self.tmpdir_path,
        )

        text = (self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME).read_text()
        assert "B\u00f3ris Marin" in text
        assert "\\u00f3" not in text
        assert json.loads(text)["paper.pdf"]["DEFAULT"]["authors"] == [
            "B\u00f3ris Marin"
        ]

    def test_write_heading_template_empty_no_existing_no_write(self):
        """An empty chunk run with no existing template writes nothing."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.write_heading_template({}, self.tmpdir_path)

        assert not (self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME).exists()

    def test_build_raises_when_nothing_chunked(self, monkeypatch):
        """build() fails loudly instead of storing nothing and reporting done."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        monkeypatch.setattr(builder, "chunk_all", lambda *a, **k: {})

        with pytest.raises(RuntimeError, match="No files were successfully chunked"):
            builder.build(str(self.tmpdir_path), "chroma:/tmp/x", "c")

    def test_find_files_excludes_template(self):
        """The generated template in .klea-cache/ is not ingestible.

        The template now lives in the cache folder, which _find_files
        skips wholesale; a stray template in the source dir would be an
        unsupported extension anyway.
        """
        template = self.tmpdir_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text("{}")
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder._find_files(self.tmpdir_path) == []

    def test_find_files_excludes_loaded_metadata_map(self):
        """The --metadata-map file is excluded when it lives in source_dir."""
        src = self.tmpdir_path / "doc.md"
        src.write_text("# Doc\n")
        map_path = self.tmpdir_path / "metadata.json"
        map_path.write_text(json.dumps({"paper.pdf": {"DEFAULT": {}}}))

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder._load_metadata_map(str(map_path))

        assert builder._find_files(self.tmpdir_path) == [src]

    def test_find_files_includes_non_metadata_json(self):
        """Other .json files (docling json_docling) stay ingestible."""
        src = self.tmpdir_path / "docling-doc.json"
        src.write_text("{}")
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder._find_files(self.tmpdir_path) == [src]

    def test_find_files_excludes_configured_store_dir(self):
        """The configured store directory is never ingested."""
        src = self.tmpdir_path / "doc.md"
        src.write_text("# Doc\n")
        store = self.tmpdir_path / "celegans-store"
        store.mkdir()
        (store / "chroma.sqlite3").write_bytes(b"store")

        builder = StoresBuilder(embedding_model="", logger=self.logger, store_dir=store)
        assert builder._find_files(self.tmpdir_path) == [src]

    def test_find_files_excludes_chroma_store_heuristically(self):
        """A nested store is excluded even without an explicit store_dir."""
        src = self.tmpdir_path / "doc.md"
        src.write_text("# Doc\n")
        store = self.tmpdir_path / "celegans-store"
        store.mkdir()
        (store / "chroma.sqlite3").write_bytes(b"store")

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        assert builder._find_files(self.tmpdir_path) == [src]

    def test_find_files_keeps_real_source_with_sqlite_name(self):
        """A .md source alongside a store folder is still found."""
        src = self.tmpdir_path / "doc.md"
        src.write_text("# Doc\n")
        store = self.tmpdir_path / "celegans-store"
        store.mkdir()
        (store / "chroma.sqlite3").write_bytes(b"store")

        builder = StoresBuilder(embedding_model="", logger=self.logger, store_dir=store)
        assert builder._find_files(self.tmpdir_path) == [src]

    def test_store_all_sanitizes_metadata_for_upsert(self, monkeypatch):
        """Empty-list/None metadata is dropped at upsert; originals intact.

        Chroma rejects empty-list metadata values, so ``headings: []`` is
        removed from the copies sent to the store.  The source documents
        (and hence the BM25 corpus) keep ``headings: []``.
        """

        class FakeStore:
            def __init__(self):
                self.added: list[Document] = []
                self.dropped = False

            def add_documents(self, docs):
                self.added.extend(docs)

            def delete_collection(self):
                self.dropped = True

            def delete(self, ids=None, **kwargs):
                pass

        fake = FakeStore()
        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: fake,
        )
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.embeddings = object()

        doc = Document(
            page_content="content",
            metadata={
                "file_name": "a.md",
                "headings": [],
                "authors": ["X"],
                "extra": None,
            },
        )
        builder.store_all(
            [("xxh64:abc", [doc], Path("a.md"))],
            "chroma:/tmp/x",
            "col",
            self.tmpdir_path,
            force=True,
        )

        assert fake.dropped is True
        assert len(fake.added) == 1
        assert fake.added[0].id == "a.md:0"
        meta = fake.added[0].metadata
        assert "headings" not in meta
        assert "extra" not in meta
        assert meta["authors"] == ["X", "x"]
        assert meta["file_name"] == "a.md"
        assert doc.metadata["headings"] == []

    def test_store_all_batches_and_logs_progress(self, monkeypatch, caplog):
        """store_all embeds in batches and logs bounded progress lines.

        A single ``add_documents`` call embeds every chunk in one request,
        which can take minutes with no output; batching reports progress at
        10% milestones while keeping the final per-file line.
        """

        class FakeStore:
            def __init__(self):
                self.calls: list[int] = []
                self.added_ids: list[str] = []

            def add_documents(self, docs):
                self.calls.append(len(docs))
                self.added_ids.extend(d.id for d in docs)

            def delete_collection(self):
                pass

            def delete(self, ids=None, **kwargs):
                pass

        fake = FakeStore()
        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: fake,
        )
        builder = StoresBuilder(
            embedding_model="",
            logger=self.logger,
            embed_batch_size=4,
        )
        builder.embeddings = object()

        # a.md must exist in the source dir: store_all reports progress
        # against the source-directory file count (a lazy results iterable
        # has no length), matching production where results come from
        # _find_files of the same directory.
        (self.tmpdir_path / "a.md").write_text(TEST_MD_CONTENT)

        docs = [
            Document(
                page_content=f"content {i}",
                metadata={"file_name": "a.md"},
            )
            for i in range(10)
        ]
        with caplog.at_level(logging.INFO):
            builder.store_all(
                [("xxh64:abc", docs, Path("a.md"))],
                "chroma:/tmp/x",
                "col",
                self.tmpdir_path,
                force=True,
            )

        assert fake.calls == [4, 4, 2]
        assert fake.added_ids == [f"a.md:{i}" for i in range(10)]
        assert "Stored 4/10 chunks (40%) from a.md" in caplog.text
        assert "Stored 8/10 chunks (80%) from a.md" in caplog.text
        assert "Added 10 chunks from a.md (1/1)" in caplog.text

    def test_store_all_consumes_lazy_load_and_fold_results(self, monkeypatch, caplog):
        """store_all consumes _load_and_fold_results as a lazy generator.

        The store command feeds the generator straight into store_all so
        each file's chunks are released after storing (memory bounded per
        file) instead of accumulating a full results list.  Progress is
        reported against the source-directory file count, since a
        generator has no length.
        """

        class FakeStore:
            def __init__(self):
                self.added_ids: list[str] = []

            def add_documents(self, docs):
                self.added_ids.extend(d.id for d in docs)

            def delete_collection(self):
                pass

            def delete(self, ids=None, **kwargs):
                pass

        fake = FakeStore()
        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: fake,
        )
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        from klea_utils.stores.ingestion import _hash_file

        builder = StoresBuilder(embedding_model="", logger=self.logger, do_ocr=False)
        # Populate the cache directly (as the worker would have written it)
        # so the lazy fold path has something to stream.
        cached = Document(page_content="cached", metadata={"headings": ["S"]})
        builder._save_to_cache([cached], {}, self.tmpdir_path, _hash_file(md_file))
        builder.embeddings = object()

        results = builder._load_and_fold_results(self.tmpdir_path, None)
        assert iter(results) is results  # lazy generator, not a materialised list
        with caplog.at_level(logging.INFO):
            builder.store_all(
                results,
                "chroma:/tmp/x",
                "col",
                self.tmpdir_path,
                force=True,
            )

        assert fake.added_ids
        assert all(doc_id.startswith("test.md:") for doc_id in fake.added_ids)
        assert "Added " in caplog.text and "from test.md (1/1)" in caplog.text

    def test_store_all_writes_bm25_inline_from_generator(self, monkeypatch):
        """store_all writes the BM25 corpus inline while storing lazily.

        With a lazy generator as results, the BM25 corpus is still written
        (from the same chunks, in place, during the store loop) and no
        materialised results list is needed -- memory stays bounded per
        file even when a BM25 corpus is requested.
        """

        class FakeStore:
            def __init__(self):
                self.added_ids: list[str] = []

            def add_documents(self, docs):
                self.added_ids.extend(d.id for d in docs)

            def delete_collection(self):
                pass

            def delete(self, ids=None, **kwargs):
                pass

        fake = FakeStore()
        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: fake,
        )
        md_file = self.tmpdir_path / "test.md"
        md_file.write_text(TEST_MD_CONTENT)

        builder = StoresBuilder(
            embedding_model="", logger=self.logger, do_ocr=False, embed_batch_size=2
        )
        # Populate the cache directly (as the worker would have written it)
        # so the lazy fold path has something to stream.
        from klea_utils.stores.ingestion import _hash_file

        cached = Document(page_content="cached", metadata={"headings": ["S"]})
        builder._save_to_cache([cached], {}, self.tmpdir_path, _hash_file(md_file))
        builder.embeddings = object()

        bm25_path = self.tmpdir_path / "corpus.pkl"
        results = builder._load_and_fold_results(self.tmpdir_path, None)
        assert iter(results) is results
        builder.store_all(
            results,
            "chroma:/tmp/x",
            "col",
            self.tmpdir_path,
            force=True,
            bm25_path=str(bm25_path),
        )

        assert bm25_path.is_file()
        loaded = _read_bm25_corpus(bm25_path)
        assert loaded
        assert all(doc.metadata["file_name"] == "test.md" for doc in loaded)

    def test_manifest_round_trip(self):
        """The store manifest is written and reloaded."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        manifest = {
            "version": 1,
            "collection": "col",
            "files": {"a.md": {"file_hash": "xxh64:abc", "num_chunks": 2}},
        }
        builder._save_manifest(self.tmpdir_path, "col", manifest)

        loaded = builder._load_manifest(self.tmpdir_path, "col")
        assert loaded == manifest

    def test_manifest_missing_is_fresh(self):
        """A missing manifest yields an empty file mapping."""
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        manifest = builder._load_manifest(self.tmpdir_path, "col")
        assert manifest["files"] == {}

    def test_manifest_corrupt_is_fresh(self):
        """A corrupt manifest is ignored and treated as fresh."""
        path = self.tmpdir_path / CACHE_DIR_NAME / "col.manifest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("not json")

        builder = StoresBuilder(embedding_model="", logger=self.logger)
        manifest = builder._load_manifest(self.tmpdir_path, "col")
        assert manifest["files"] == {}

    def test_store_all_incremental_skips_unchanged(self, monkeypatch):
        """Unchanged files (hash matches manifest) are skipped."""
        calls: list[int] = []

        class FakeStore:
            def add_documents(self, docs):
                calls.append(len(docs))

            def delete(self, ids=None, **kwargs):
                calls.append(-len(ids or []))

        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: FakeStore(),
        )
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.embeddings = object()
        # Pre-seed the manifest: a.md already stored with this hash.
        builder._save_manifest(
            self.tmpdir_path,
            "col",
            {
                "version": 1,
                "collection": "col",
                "files": {"a.md": {"file_hash": "xxh64:abc", "num_chunks": 2}},
            },
        )

        doc = Document(page_content="x", metadata={"file_name": "a.md"})
        builder.store_all(
            [("xxh64:abc", [doc], Path("a.md"))],
            "chroma:/tmp/x",
            "col",
            self.tmpdir_path,
        )

        assert calls == []

    def test_store_all_incremental_replaces_changed(self, monkeypatch):
        """A changed file has its old chunk IDs deleted, then is re-added."""
        deleted: list[list[str]] = []
        added_ids: list[str] = []

        class FakeStore:
            def add_documents(self, docs):
                added_ids.extend(d.id for d in docs)

            def delete(self, ids=None, **kwargs):
                deleted.append(ids or [])

        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: FakeStore(),
        )
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.embeddings = object()
        # Manifest says a.md had 3 chunks with the old hash.
        builder._save_manifest(
            self.tmpdir_path,
            "col",
            {
                "version": 1,
                "collection": "col",
                "files": {"a.md": {"file_hash": "xxh64:old", "num_chunks": 3}},
            },
        )

        docs = [
            Document(page_content=f"x{i}", metadata={"file_name": "a.md"})
            for i in range(2)
        ]
        builder.store_all(
            [("xxh64:new", docs, Path("a.md"))],
            "chroma:/tmp/x",
            "col",
            self.tmpdir_path,
        )

        # Old IDs 0..2 deleted, then the two new chunks added.
        assert deleted == [["a.md:0", "a.md:1", "a.md:2"]]
        assert added_ids == ["a.md:0", "a.md:1"]
        manifest = builder._load_manifest(self.tmpdir_path, "col")
        assert manifest["files"]["a.md"] == {"file_hash": "xxh64:new", "num_chunks": 2}

    def test_store_all_no_force_never_prunes_absent_files(self, monkeypatch):
        """Files in the manifest but absent from the source are left alone."""
        deleted: list[list[str]] = []

        class FakeStore:
            def add_documents(self, docs):
                pass

            def delete(self, ids=None, **kwargs):
                deleted.append(ids or [])

        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: FakeStore(),
        )
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.embeddings = object()
        # Manifest knows about removed.md, but it is not in the results.
        builder._save_manifest(
            self.tmpdir_path,
            "col",
            {
                "version": 1,
                "collection": "col",
                "files": {"removed.md": {"file_hash": "xxh64:r", "num_chunks": 5}},
            },
        )

        doc = Document(page_content="x", metadata={"file_name": "a.md"})
        builder.store_all(
            [("xxh64:abc", [doc], Path("a.md"))],
            "chroma:/tmp/x",
            "col",
            self.tmpdir_path,
        )

        # a.md added; removed.md untouched.
        assert deleted == []
        manifest = builder._load_manifest(self.tmpdir_path, "col")
        assert "removed.md" in manifest["files"]

    def test_store_all_force_drops_and_rebuilds(self, monkeypatch):
        """--force drops the collection and re-stores every file."""
        dropped = []
        added_ids: list[str] = []

        class FakeStore:
            def add_documents(self, docs):
                added_ids.extend(d.id for d in docs)

            def delete_collection(self):
                dropped.append(True)

        monkeypatch.setattr(
            "klea_utils.stores.ingestion.instantiate_vector_store",
            lambda *a, **k: FakeStore(),
        )
        builder = StoresBuilder(embedding_model="", logger=self.logger)
        builder.embeddings = object()

        doc = Document(page_content="x", metadata={"file_name": "a.md"})
        builder.store_all(
            [("xxh64:abc", [doc], Path("a.md"))],
            "chroma:/tmp/x",
            "col",
            self.tmpdir_path,
            force=True,
        )

        assert dropped == [True]
        assert added_ids == ["a.md:0"]
        manifest = builder._load_manifest(self.tmpdir_path, "col")
        assert manifest["files"]["a.md"] == {"file_hash": "xxh64:abc", "num_chunks": 1}


def test_store_dir_resolves_local_and_remote_schemes():
    """_store_dir returns local folders but None for remote schemes."""
    from klea_utils.ui.stores_create import _store_dir

    chroma_dir = _store_dir("chroma:/tmp/store")
    assert chroma_dir is not None
    assert chroma_dir.name == "store"

    lance_dir = _store_dir("lancedb:/tmp/lance")
    assert lance_dir is not None
    assert lance_dir.name == "lance"

    assert _store_dir("qdrant:http://localhost:6333") is None
    assert _store_dir("pgvector:postgresql://host/db") is None
    assert _store_dir("no-scheme-path") is None


if __name__ == "__main__":
    pytest.main()
