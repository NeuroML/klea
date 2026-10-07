#!/usr/bin/env python3
"""
Store ingestion -- convert documents, chunk, embed, and write to stores

See ``devdocs/system/store-create.md``; ADRs 0001, 0002, 0021 and 0022.

File: klea_utils/stores/ingestion.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import os
import pickle
import tempfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

import xxhash
from langchain_core.documents import Document

from ..biblio.extract import Resolver, extract_metadata, extract_metadata_from_text
from ..llm import setup_embedding
from .metadata import (
    PERSON_NAME_FILTER_FIELDS,
    STORE_DROPPED_METADATA_KEYS,
)
from .utils import (
    CACHE_DIR_NAME,
    TEMPLATE_FILE_NAME,
    drop_collection,
    expand_person_names,
    find_source_files,
    instantiate_vector_store,
    normalize_text,
)

if TYPE_CHECKING:
    from .chunk_worker import ChunkItemResult, ChunkWorkerConfig

#: Default per-worker RSS cap for subprocess conversion.  Includes the
#: ~1-1.5 GiB Docling's models occupy at worker startup.
DEFAULT_WORKER_MEM_LIMIT = 4 * 1024**3

#: Default maximum files handed to a single conversion worker.
DEFAULT_WORKER_BATCH_SIZE = 200

#: Heading texts that are never a real document title.  When the title
#: extraction falls back to the filename stem, the first chunk heading is
#: used as a title fallback instead -- but journal banners and section
#: labels (e.g. ``Review``, ``Highlights``, ``DOI:``) are not titles, so
#: they are skipped.  Lowercased for comparison.
_TITLE_SKIP_HEADINGS = frozenset(
    {
        "review",
        "research",
        "research article",
        "highlights",
        "abstract",
        "author summary",
        "summary",
        "introduction",
        "doi:",
        "doi",
        "editorial",
        "front matter",
        "correspondence",
        "*for correspondence:",
    }
)


def _first_heading_title(docs: list[Document]) -> str | None:
    """Return the first chunk heading that looks like a title, or ``None``.

    Scans *docs* in document order for the first non-empty heading chain
    and returns its first element, skipping headings that are journal
    banners or section labels (see :data:`_TITLE_SKIP_HEADINGS`).  This
    is a fallback for documents whose title Docling's layout model does
    not label as a ``TITLE`` item (e.g. some conference preprints), used
    only when the extraction cascade falls back to the filename stem.

    :param docs: Chunked documents in document order
    :returns: First heading that looks like a title, or ``None``
    """
    for doc in docs:
        headings = doc.metadata.get("headings") or []
        if not headings:
            continue
        candidate = headings[0].strip()
        if candidate.lower() in _TITLE_SKIP_HEADINGS:
            continue
        return candidate
    return None


class StoresBuilder:
    """Build stores from a directory of source documents.

    Uses Docling for document conversion and token-aware chunking, then
    embeds chunks and writes them to a vector store backend.  Optionally
    also writes the combined chunked corpus for BM25 retrieval.
    """

    DEFAULT_MAX_TOKENS = 450
    DEFAULT_MERGE_PEERS = True
    DEFAULT_TOKENIZER_MODEL = "BAAI/bge-m3"

    #: Number of chunks embedded per ``add_documents`` call in
    #: :meth:`store_all`.  Batching gives the embedding phase (which can take
    #: minutes for large corpora) a progress signal between calls; embedding
    #: backends like Ollama send all texts in a single request otherwise.
    #: The value is mostly a progress-granularity knob, not a throughput one.
    DEFAULT_EMBED_BATCH_SIZE = 256

    def __init__(
        self,
        embedding_model: str,
        logger: logging.Logger,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        merge_peers: bool = DEFAULT_MERGE_PEERS,
        tokenizer_model: str = DEFAULT_TOKENIZER_MODEL,
        do_ocr: bool = True,
        embed_batch_size: int = DEFAULT_EMBED_BATCH_SIZE,
        store_dir: Path | None = None,
    ):
        """Initialise the builder.

        :param embedding_model: Embedding model identifier
            (e.g. ``"ollama:bge-m3:latest"``).  Only needed when
            :meth:`store_all` will be called.
        :param logger: Logger instance
        :param max_tokens: Maximum tokens per chunk
        :param merge_peers: Whether the chunker should merge peer
            elements (e.g. consecutive paragraphs)
        :param tokenizer_model: HuggingFace tokenizer model used for
            token-aware chunking
        :param do_ocr: Whether Docling should OCR pages during PDF
            conversion.  Keep enabled for scanned/image-based PDFs;
            disabling it speeds up conversion of text-based PDFs
            significantly.
        :param embed_batch_size: Chunks per ``add_documents`` call when
            writing to the vector store
        :param store_dir: Vector store directory (e.g. a Chroma store
            folder) that may live inside the source directory and must
            be excluded from ingestion.  ``None`` for remote backends
            with no local folder.
        """
        self.embedding_model = embedding_model
        self.logger = logging.getLogger(f"{logger.name}.{self.__class__.__name__}")
        self.max_tokens = max_tokens
        self.merge_peers = merge_peers
        self.tokenizer_model = tokenizer_model
        self.do_ocr = do_ocr
        self.embed_batch_size = embed_batch_size

        self.embeddings = None
        self._converter = None
        self._chunker = None
        self._metadata_map_path: Path | None = None
        self.store_dir = store_dir.resolve() if store_dir else None

        self.logger.info(
            f"StoresBuilder initialised (max_tokens={max_tokens}, "
            f"merge_peers={merge_peers}, tokenizer={tokenizer_model}, "
            f"do_ocr={do_ocr})"
        )

    def build(
        self,
        source_dir: str,
        store_uri: str,
        collection_name: str,
        force: bool = False,
        metadata_map_path: str | None = None,
        bm25_path: str | None = None,
        worker_mem_limit: int | None = DEFAULT_WORKER_MEM_LIMIT,
        worker_batch_size: int = DEFAULT_WORKER_BATCH_SIZE,
    ) -> None:
        """Full pipeline: chunk documents and write them to a vector store.

        Composes the two memory-bounded paths: :meth:`chunk_all` runs in
        worker-isolated, cache-only mode (uncached files are converted in
        short-lived subprocesses), then :meth:`_load_and_fold_results`
        streams the cached chunks into :meth:`store_all`.  No phase holds
        the whole corpus in memory, so very large corpora stay bounded.

        When no *metadata_map_path* is given, the map is generated in the
        chunk phase from the extracted bibliographic metadata (and written
        to ``metadata-map.template.json``, exactly as
        :meth:`write_heading_template` does) and consumed in the store
        phase -- so ``build`` works without a prior ``chunk``, at the cost
        of no review step.  For the review-driven flow (``chunk``, edit
        the template, ``store``), pass the map explicitly or let ``store``
        auto-fall back to the template.

        Files whose conversion failed have no cache entry and are skipped
        with an error; everything else is stored.

        :param source_dir: Path to a directory containing source documents
        :param store_uri: Vector store URI (e.g. ``chroma:/path``)
        :param collection_name: Collection name for the store
        :param force: Re-process all files even if unchanged
        :param metadata_map_path: Optional path to a metadata map JSON file
        :param bm25_path: Optional path to write the combined BM25 corpus to
        :param worker_mem_limit: Maximum RSS per conversion worker in
            bytes; ``None`` (default) keeps the chunk phase in-process
        :param worker_batch_size: Maximum files handed to a single worker
        """
        source_path = Path(source_dir).resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source directory not found: {source_path}")

        self.logger.info(
            f"Starting full pipeline: {source_dir} -> "
            f"collection '{collection_name}' at {store_uri}"
        )

        # Chunk phase (cache-only): uncached files are converted -- in
        # subprocess workers when *worker_mem_limit* is set -- and cached.
        # Nothing beyond the per-file heading chains is held in memory.
        # The metadata map is deliberately not passed here: folding
        # happens against the cached chunks in the store phase.
        file_headings = self.chunk_all(
            source_path,
            force=force,
            worker_mem_limit=worker_mem_limit,
            worker_batch_size=worker_batch_size,
        )
        if not file_headings:
            raise RuntimeError(f"No files were successfully chunked from {source_path}")

        if metadata_map_path:
            metadata_map = self._resolve_metadata_map(source_path, metadata_map_path)
        else:
            # One-shot quick start: no --metadata-map was given, so generate
            # the template from what was just chunked (exactly as the chunk
            # command would) and consume it -- the same fold and missing-file
            # behaviour as the chunk -> store workflow, just without a review
            # step in between.  The template file is written too, so it can
            # be reviewed and re-stored later.
            self.write_heading_template(file_headings, source_path)
            metadata_map = self._load_metadata_map(
                str(self._cache_dir(source_path) / TEMPLATE_FILE_NAME)
            )

        # Store phase (streaming): cached chunks are loaded, folded with
        # the map, and stored one file at a time.  Files that failed the
        # chunk phase have no cache entry and are skipped here.
        results = self._load_and_fold_results(source_path, metadata_map, strict=False)
        self.store_all(
            results,
            store_uri,
            collection_name,
            source_path,
            force=force,
            bm25_path=bm25_path,
        )
        self.logger.info(f"Ingestion complete for collection '{collection_name}'")

    def chunk_all(
        self,
        source_path: Path,
        force: bool = False,
        worker_mem_limit: int | None = DEFAULT_WORKER_MEM_LIMIT,
        worker_batch_size: int = DEFAULT_WORKER_BATCH_SIZE,
    ) -> dict[str, dict[str, Any]]:
        """Convert, chunk, and cache all files in memory-bounded workers.

        Uncached files are converted in short-lived subprocess workers
        (:mod:`klea_utils.stores.chunk_worker`) so Docling's
        per-conversion memory leak is reclaimed on worker exit; cache
        hits are handled in-process.  The chunks live in the on-disk
        cache -- they are never accumulated in this process -- so the run
        stays memory-bounded on corpora of any size.  Cache entries whose
        source file no longer exists are pruned at the end.

        Callers that need the chunked documents read them back from the
        cache with :meth:`_load_and_fold_results` (the ``store`` command
        and :meth:`build`), which streams them one file at a time.

        :param source_path: Resolved source directory path
        :param force: Re-process all files even if cached
        :param worker_mem_limit: Maximum RSS per conversion worker in
            bytes; the limit includes the ~1-1.5 GiB Docling's models
            occupy at worker startup, so headroom for its per-conversion
            growth is roughly the limit minus that.  ``None`` disables
            the cap (workers are then bounded only by
            *worker_batch_size*).
        :param worker_batch_size: Maximum files handed to any single
            conversion worker before it is restarted.  A worker stops at
            whichever comes first: its memory cap or this batch size.
        :returns: ``file_headings`` -- a ``{file_name: {"DEFAULT":
            {extracted metadata}, "heading > heading": {}, ...}}`` dict
            for template generation, pre-filled with the
            automatically-extracted bibliographic metadata (see
            :func:`~klea_utils.biblio.extract.extract_metadata`)
        """
        files = self._find_files(source_path)
        self.logger.info(f"Found {len(files)} ingestible files in {source_path}")
        total = len(files)
        resolver = self._make_resolver(source_path)

        file_headings: dict[str, dict[str, Any]] = {}
        current_hashes: set[str] = set()
        pending: list[tuple[str, str]] = []

        # Phase A (in-process): hash every file, build entries for cache
        # hits (with the legacy fallback extraction), and collect the
        # uncached files for the workers.  The parent stays lean -- it
        # never loads Docling's models.
        for ctr, file_path in enumerate(files, 1):
            file_hash = _hash_file(file_path)
            current_hashes.add(file_hash)
            if force:
                pending.append((str(file_path), file_hash))
                continue
            cached = self._load_from_cache(source_path, file_hash)
            if cached is None:
                pending.append((str(file_path), file_hash))
                continue
            docs, extracted = cached
            self.logger.debug(
                f"Using cached chunks for: {file_path.name} ({ctr}/{total})"
            )
            if not extracted:
                # No persisted extraction (e.g. a legacy cache entry
                # written before the biblio cascade existed), so run the
                # text-only extraction over the cached chunks.
                extracted = self._extract_metadata_fallback(file_path, docs, resolver)
            file_headings[file_path.name] = self._build_file_headings_entry(
                file_path.name, extracted, docs
            )

        # Phase B (subprocess workers): convert+cache the uncached files.
        if pending:
            from .chunk_worker import ChunkWorkerConfig

            config = ChunkWorkerConfig(
                source_path=str(source_path),
                max_tokens=self.max_tokens,
                tokenizer_model=self.tokenizer_model,
                do_ocr=self.do_ocr,
                mem_limit_bytes=worker_mem_limit,
                log_level=self.logger.getEffectiveLevel(),
            )
            for item in self._dispatch_conversion_batches(
                pending, config, worker_batch_size
            ):
                if item.file_headings_entry is not None:
                    file_headings[item.file_name] = item.file_headings_entry

        self._prune_cache(source_path, current_hashes)
        return file_headings

    def _extract_metadata_fallback(
        self,
        file_path: Path,
        docs: list[Document],
        resolver: Resolver | None,
    ) -> dict[str, Any]:
        """Run the text-only biblio extraction over cached chunks.

        Cache-hit only: never used for freshly converted files (those
        get the full biblio cascade from :meth:`_convert_and_chunk`).
        Used when a cache entry predates the full cascade and holds no
        persisted extraction: regex + pdf-info (for PDFs) + DOI
        resolution over the chunk text.  Shared by the in-process and
        worker :meth:`chunk_all` paths.

        :param file_path: Source file the chunks came from
        :param docs: Cached chunked documents
        :param resolver: DOI resolver for the extraction, or ``None``
        :returns: Flat bibliographic metadata dict
        """
        cached_text = "\n".join(doc.page_content for doc in docs if doc.page_content)
        extracted = (
            extract_metadata_from_text(
                cached_text,
                str(file_path),
                pdf_path=(
                    str(file_path) if file_path.suffix.lower() == ".pdf" else None
                ),
                resolver=resolver,
            )
            if cached_text
            else {}
        )
        self.logger.warning(
            f"No cached metadata extraction for {file_path.name}, "
            f"falling back to text-only extraction "
            f"({len(extracted)} fields); re-run with --force to "
            f"regenerate the full extraction"
        )
        return extracted

    def _dispatch_conversion_batches(
        self,
        pending: list[tuple[str, str]],
        config: "ChunkWorkerConfig",
        batch_size: int,
    ) -> "list[ChunkItemResult]":
        """Convert *pending* files in subprocess workers (test seam).

        The default implementation delegates to
        :func:`klea_utils.stores.chunk_worker.dispatch_conversion_batches`,
        which spawns a fresh worker process per batch so each worker's
        Docling memory leak is reclaimed on exit.  Tests override this
        method to run the worker function in-process.

        :param pending: ``(absolute_file_path, file_hash)`` pairs
        :param config: Worker configuration
        :param batch_size: Max files handed to a single worker
        :returns: One :class:`ChunkItemResult` per item
        """
        from .chunk_worker import dispatch_conversion_batches

        return dispatch_conversion_batches(self.logger, config, pending, batch_size)

    def _build_file_headings_entry(
        self,
        file_name: str,
        extracted: dict[str, Any],
        docs: list[Document],
    ) -> dict[str, dict[str, Any]]:
        """Build a file's metadata-map template entry from its extraction.

        The ``DEFAULT`` entry carries the normalised, url-split,
        doi-ensured extracted metadata; one empty entry per unique heading
        chain found in the chunks is added so the template lists every
        section for the user to fill in.  Shared by :meth:`chunk_all`
        (both its in-process and subprocess-worker paths) so every file's
        entry is built identically.

        :param file_name: Source file name (template key)
        :param extracted: Flat bibliographic metadata dict for the file
        :param docs: Chunked documents for the file
        :returns: ``{"DEFAULT": {...}, "heading > heading": {}, ...}``
        """
        normalized_default = _normalize_extracted_metadata(extracted)
        if normalized_default != extracted:
            self.logger.debug(f"Normalised extracted metadata for {file_name}")
        split_default = _split_url_list(normalized_default)
        if split_default != normalized_default:
            self.logger.debug(f"Split url list into per-url keys for {file_name}")
        default_metadata = _ensure_doi_url(split_default)
        if default_metadata != split_default:
            self.logger.debug(f"Derived url_doi for {file_name}")
        file_entry: dict[str, dict[str, Any]] = {"DEFAULT": default_metadata}
        for doc in docs:
            headings = doc.metadata.get("headings", [])
            if headings:
                key = " > ".join(headings)
                if key not in file_entry:
                    file_entry[key] = {}
        return file_entry

    def _fold_metadata_map(
        self,
        file_path: Path,
        docs: list[Document],
        metadata_map: dict[str, dict[str, Any]],
    ) -> None:
        """Apply the per-file metadata map to *docs* (in place).

        Raises when *file_path* has no entry in the map, and warns when
        no chunk resolves metadata from it.  Shared by :meth:`chunk_all`
        (convert path) and :meth:`_load_and_fold_results` (cache-only
        store path) so both fold the map identically.

        :param file_path: Source file whose chunks are being enriched
        :param docs: Chunked documents for the file (mutated in place)
        :param metadata_map: Per-file metadata map keyed by source filename
        """
        if file_path.name not in metadata_map:
            raise ValueError(
                f"No metadata map entry for {file_path.name}. "
                f"Add a '{file_path.name}' entry (a DEFAULT entry "
                f"is enough) to the metadata map; run "
                f"'klea-stores-create chunk' to regenerate the "
                f"template."
            )
        resolved_count = 0
        for doc in docs:
            meta = self._resolve_metadata(
                file_path.name,
                doc.metadata.get("headings"),
                metadata_map,
            )
            if meta:
                doc.metadata.update(_apply_store_metadata_policy(meta))
                resolved_count += 1
        if resolved_count == 0:
            self.logger.warning(
                f"No metadata resolved for {file_path.name} from the "
                f"metadata map. Check that the map is keyed by the "
                f"source filename and that the chunk headings (or a "
                f"DEFAULT entry) provide metadata."
            )

    def _load_and_fold_results(
        self,
        source_path: Path,
        metadata_map: dict[str, dict[str, Any]] | None,
        strict: bool = True,
    ) -> Iterator[tuple[str, list[Document], Path]]:
        """Load cached chunks and fold the metadata map into them.

        Cache-only: every source file must already have a cache entry
        (run ``klea-stores-create chunk`` or ``build`` first).  In the
        default strict mode a file with no cache entry raises
        ``ValueError`` instead of being converted on the fly -- this is
        the ``store`` command's contract.  With *strict* false (used by
        :meth:`build`, whose chunk phase just logged the conversion
        failure) such files are skipped with an error so the rest of the
        corpus is still stored.

        This is a generator: it yields ``(file_hash, docs, file_path)``
        per file, so the caller (:meth:`store_all`) consumes and releases
        each file's chunks before the next file is loaded.  Memory stays
        bounded per file instead of holding the whole corpus, which is
        what lets the ``store`` command scale to large corpora.

        :param source_path: Resolved source directory path
        :param metadata_map: Metadata map for heading-based enrichment
        :param strict: Raise on files with no cache entry (default);
            when false, skip them with an error instead
        :returns: An iterator of ``(file_hash, docs, file_path)`` tuples
            ready for :meth:`store_all`
        :raises ValueError: When *strict* is true and a source file has
            no cache entry
        """
        files = self._find_files(source_path)
        self.logger.info(f"Found {len(files)} ingestible files in {source_path}")

        for ctr, file_path in enumerate(files, 1):
            file_hash = _hash_file(file_path)
            cached = self._load_from_cache(source_path, file_hash)
            if cached is None:
                if strict:
                    raise ValueError(
                        f"No cache entry for {file_path.name}. The cache-only "
                        f"store command requires every file to be converted "
                        f"first; run 'klea-stores-create chunk' (or 'build') "
                        f"to convert it."
                    )
                self.logger.error(
                    f"No cache entry for {file_path.name} (its conversion "
                    f"failed); skipping it -- fix the file and re-run to "
                    f"store it"
                )
                continue
            docs, _ = cached
            self.logger.debug(
                f"Using cached chunks for: {file_path.name} ({ctr}/{len(files)})"
            )
            if not docs:
                self.logger.warning(
                    f"No cached chunks for {file_path.name}; it was converted "
                    f"to zero chunks (likely a scanned/image PDF with OCR "
                    f"disabled). Re-run 'klea-stores-create chunk' with OCR "
                    f"enabled or re-classify with 'klea-stores-create pre-check'."
                )

            for doc in docs:
                doc.metadata.update(
                    {
                        "file_hash": file_hash,
                        "file_name": file_path.name,
                    }
                )

            if metadata_map:
                self._fold_metadata_map(file_path, docs, metadata_map)

            yield file_hash, docs, file_path

    def store_all(
        self,
        results: Iterable[tuple[str, list[Document], Path]],
        store_uri: str,
        collection_name: str,
        source_dir: Path,
        force: bool = False,
        bm25_path: str | None = None,
    ) -> None:
        """Write chunked documents to a vector store.

        Incremental by default: a store manifest
        (``<source_dir>/.klea-cache/<collection>.manifest.json``) records
        which files are in the collection and how many chunks each has,
        so unchanged files are skipped, changed files have their old
        chunk IDs deleted and are re-added, and new files are added.
        Files absent from the source directory are left untouched
        (never pruned).

        With ``force`` the whole collection is dropped and rebuilt from
        scratch (see :func:`klea_utils.stores.utils.drop_collection`),
        then the manifest is rewritten.  This is the portable way to
        update a collection, since documents within a collection cannot
        be updated in place across all backends.

        Chunk IDs are deterministic (``<file_name>:<chunk_index>``) so
        deletion by ID works on every backend.

        *results* may be any iterable, including the lazy generator from
        :meth:`_load_and_fold_results`; each file's chunks are released
        once they are stored, keeping memory bounded per file.  When a
        BM25 corpus is requested it is written inline from the same
        chunks during the store loop, one pickled batch per
        :attr:`embed_batch_size` chunk (read back by looping
        ``pickle.load`` until ``EOFError``).

        :param results: Iterable of ``(file_hash, docs, file_path)``
            tuples from :meth:`chunk_all` or :meth:`_load_and_fold_results`
        :param store_uri: Vector store URI
        :param collection_name: Collection name for the store
        :param source_dir: Resolved source directory (for the manifest)
        :param force: Drop the collection and re-store everything
        :param bm25_path: Optional path to write the combined BM25 corpus to
        """
        if self.embeddings is None:
            self.logger.info(f"Initialising embedding model ({self.embedding_model})")
            self.embeddings = setup_embedding(self.embedding_model, self.logger)
        assert store_uri and collection_name

        # BM25 is written inline from the same chunks during the store
        # loop (one pickled list per embed_batch_size), so the results
        # stay lazy either way and memory stays bounded per file.  The
        # file is only opened once a first batch is ready, so an empty
        # corpus leaves no file behind.
        bm25_path_obj = Path(bm25_path) if bm25_path else None
        bm25_file = None
        bm25_batch: list[Document] = []
        if bm25_path_obj is not None:
            bm25_path_obj.parent.mkdir(parents=True, exist_ok=True)
        total = len(self._find_files(source_dir))

        manifest_path = self._manifest_path(source_dir, collection_name)
        if not force and not manifest_path.is_file():
            # First store for this collection, or the cache/manifest was
            # deleted: everything is treated as new.  Let the user know the
            # manifest is load-bearing for future incremental runs.
            self.logger.info(
                f"No store manifest found at {manifest_path}; all files will "
                f"be stored.  The manifest is written here and reused for "
                f"incremental updates -- keep it."
            )
        manifest = self._load_manifest(source_dir, collection_name)

        self.logger.info(f"Opening vector store '{collection_name}' at {store_uri}")
        store = instantiate_vector_store(
            store_uri,
            collection_name,
            self.embeddings,
            self.logger,
            create=True,
        )

        if force:
            self.logger.info(f"Force: dropping collection '{collection_name}'")
            drop_collection(store, store_uri, collection_name)
            store = instantiate_vector_store(
                store_uri,
                collection_name,
                self.embeddings,
                self.logger,
                create=True,
            )
            manifest = {"version": 1, "collection": collection_name, "files": {}}

        raw_files = manifest.get("files")
        if not isinstance(raw_files, dict):
            raw_files = {}
            manifest["files"] = raw_files
        manifest_files: dict[str, Any] = raw_files
        for ctr, (file_hash, docs, file_path) in enumerate(results, 1):
            file_name = file_path.name
            known: dict[str, Any] | None = manifest_files.get(file_name)

            # The BM25 corpus holds every file's chunks, including ones
            # skipped as unchanged below, so accumulate before the skip.
            if bm25_path_obj is not None:
                bm25_batch.extend(docs)
                if len(bm25_batch) >= self.embed_batch_size:
                    if bm25_file is None:
                        # Deliberately not a context manager: the file is
                        # opened only when the first batch is ready (an
                        # empty corpus leaves no file) and closed after the
                        # loop, across many batches.
                        bm25_file = open(bm25_path_obj, "wb")  # noqa: SIM115
                    pickle.dump(bm25_batch, bm25_file)
                    bm25_batch = []

            if not force and known and known.get("file_hash") == file_hash:
                self.logger.debug(
                    f"Skipping unchanged file: {file_name} ({ctr}/{total})"
                )
                continue

            # A changed file: drop its previously-stored chunk IDs first
            # (deterministic ``file_name:idx``), so re-adding updates in
            # place and a shrunken file leaves no stale rows behind.
            if known:
                old_chunks = known.get("num_chunks", 0)
                old_ids = [f"{file_name}:{i}" for i in range(old_chunks)]
                store.delete(ids=old_ids)
                self.logger.debug(
                    f"Deleted {len(old_ids)} previously stored chunks for {file_name}"
                )

            # Chroma rejects empty-list and None metadata values on upsert.
            # The cache keeps ``headings: []`` as an explicit "no headings
            # found" marker, so it (and any other empty/None value) is
            # dropped only here, from copies -- the originals (used for the
            # BM25 corpus) keep their metadata intact.
            sanitized_docs = [
                Document(
                    page_content=doc.page_content,
                    metadata=_sanitize_store_metadata(doc.metadata),
                    id=f"{file_name}:{idx}",
                )
                for idx, doc in enumerate(docs)
            ]
            # Embed in batches: a single ``add_documents`` call embeds every
            # chunk in one request (Ollama sends all texts at once), which can
            # take minutes with no output.  Batching reports progress at 10%
            # milestones so the output stays bounded for any corpus size.
            num_docs = len(sanitized_docs)
            last_pct = -1
            for i in range(0, num_docs, self.embed_batch_size):
                store.add_documents(sanitized_docs[i : i + self.embed_batch_size])
                done = min(i + self.embed_batch_size, num_docs)
                pct = done * 100 // num_docs
                if pct >= last_pct + 10:
                    last_pct = pct
                    self.logger.info(
                        f"Stored {done}/{num_docs} chunks ({pct}%) from {file_name}"
                    )
            self.logger.info(
                f"Added {num_docs} chunks from {file_name} ({ctr}/{total})"
            )
            manifest_files[file_name] = {
                "file_hash": file_hash,
                "num_chunks": num_docs,
            }

        self._save_manifest(source_dir, collection_name, manifest)

        if bm25_path_obj is not None:
            if bm25_file is None and not bm25_batch:
                # Nothing converted: no corpus to write.
                self.logger.warning("No documents to write to BM25 store, skipping")
            else:
                # Flush the final partial batch (a corpus smaller than one
                # batch never opened the file mid-loop).
                if bm25_file is None:
                    bm25_file = open(bm25_path_obj, "wb")  # noqa: SIM115
                if bm25_batch:
                    pickle.dump(bm25_batch, bm25_file)
                bm25_file.close()
                self.logger.info(f"Wrote BM25 store to {bm25_path}")

    def write_heading_template(
        self, file_headings: dict[str, dict[str, Any]], source_dir: Path
    ) -> None:
        """Write a metadata-map template JSON file organised per source file.

        Each file gets a ``"DEFAULT"`` placeholder and one entry per
        unique heading chain found in that file.  The user fills in the
        ``{}`` with their metadata key-value pairs.

        The template is written into the source directory's cache folder
        (``<source_dir>/.klea-cache/metadata-map.template.json``), the
        same place the chunk cache and ``doi-cache.json`` live.  To
        review it, copy it out (e.g. to ``metadata-map.json``), edit,
        and pass the copy to ``klea-stores-create store
        --metadata-map <path>``.

        Refuses to write when *file_headings* is empty (no files were
        chunked): an existing template is preserved rather than clobbered
        with an empty one.

        :param file_headings: ``{file_name: {"DEFAULT": {},
            "heading > heading": {}, ...}, ...}`` from :meth:`chunk_all`
        :param source_dir: Resolved source directory path (template is
            written into its cache folder)
        """
        out_path = self._cache_dir(source_dir) / TEMPLATE_FILE_NAME
        if not file_headings:
            if out_path.is_file():
                self.logger.warning(
                    f"No files chunked; keeping existing template {out_path}"
                )
                return
            self.logger.warning(
                f"No files chunked and no existing template at {out_path}"
            )
            return
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # ensure_ascii=False keeps accented characters (e.g. "B\u00f3ris")
        # as literal UTF-8 in the file, so the human editing the template
        # can see exactly what text a heading/author contains.
        with open(out_path, "w") as f:
            json.dump(file_headings, f, indent=4, ensure_ascii=False)
            f.write("\n")
        total_chains = sum(len(v) - 1 for v in file_headings.values())
        self.logger.info(
            f"Metadata map template written to {out_path} "
            f"({len(file_headings)} files, {total_chains} heading chains)"
        )

    def _cache_dir(self, source_dir: Path) -> Path:
        """Return the cache directory path inside *source_dir*.

        :param source_dir: Resolved source directory path
        :returns: Path to ``<source_dir>/.klea-cache/``
        """
        return source_dir / CACHE_DIR_NAME

    def _manifest_path(self, source_dir: Path, collection_name: str) -> Path:
        """Return the store manifest path for a collection.

        The manifest records which files (and how many chunks each) are
        in a collection, so ``store`` can do incremental updates without
        querying the vector store (which is not portable across
        backends).  It lives in the source directory's cache folder
        alongside the chunk cache and ``doi-cache.json``.

        :param source_dir: Resolved source directory path
        :param collection_name: Collection name for the store
        :returns: Path to ``<cache_dir>/<collection>.manifest.json``
        """
        return self._cache_dir(source_dir) / f"{collection_name}.manifest.json"

    def _load_manifest(self, source_dir: Path, collection_name: str) -> dict[str, Any]:
        """Load the store manifest, tolerating a missing or corrupt file.

        A missing manifest (first store, or a store created before
        manifests existed) yields an empty manifest so all files are
        treated as new.

        :param source_dir: Resolved source directory path
        :param collection_name: Collection name for the store
        :returns: Manifest dict with a ``files`` mapping of
            ``{file_name: {"file_hash": str, "num_chunks": int}}``
        """
        path = self._manifest_path(source_dir, collection_name)
        empty: dict[str, Any] = {
            "version": 1,
            "collection": collection_name,
            "files": {},
        }
        if not path.is_file():
            return empty
        try:
            with open(path) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Could not read store manifest {path}: {e}")
            return empty
        if not isinstance(data, dict) or not isinstance(data.get("files"), dict):
            self.logger.warning(f"Malformed store manifest {path}; ignoring")
            return empty
        return data

    def _save_manifest(
        self,
        source_dir: Path,
        collection_name: str,
        manifest: dict,
    ) -> None:
        """Write the store manifest to disk, tolerating failures."""
        path = self._manifest_path(source_dir, collection_name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "w") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)
                f.write("\n")
        except OSError as e:
            self.logger.warning(f"Could not write store manifest {path}: {e}")

    def _cache_path(self, source_dir: Path, file_hash: str) -> Path:
        """Return the cache file path for a given file hash.

        The ``:`` in the hash is replaced with ``_`` for filesystem
        safety (``:`` is allowed in most Linux filesystems but is
        problematic on Windows and some networked FSes).

        :param source_dir: Resolved source directory path
        :param file_hash: xxhash digest of the source file
        :returns: Path to ``<cache_dir>/<file_hash>.pkl``
        """
        safe_hash = file_hash.replace(":", "_")
        return self._cache_dir(source_dir) / f"{safe_hash}.pkl"

    def _prune_cache(self, source_dir: Path, current_hashes: set[str]) -> None:
        """Remove cache entries whose hash matches no current source file.

        Cache entries are keyed by the xxhash of their source file, so an
        entry whose hash is not in *current_hashes* can never be a future
        cache hit (the source file was renamed, removed, or changed, or
        the entry predates a pipeline change).  Called after every
        :meth:`chunk_all` run so the cache always mirrors the source
        directory and users never need to clean it by hand.

        ``.corrupt`` artifacts (cache entries moved aside by
        :meth:`_load_from_cache` because they were unreadable) are pruned
        once their failure is resolved: the source file is gone, or a
        valid cache entry was regenerated this run.  An artifact whose
        source is current but still has no valid cache entry is kept, as
        it is the only evidence of a failure that has not healed.

        Only ``*.pkl`` chunk-cache files and ``*.pkl.corrupt`` artifacts
        are touched; other cache files (e.g. ``doi-cache.json``) are left
        alone.

        :param source_dir: Resolved source directory path
        :param current_hashes: xxhash digests of all source files found
            during this run (including files that failed to convert)
        """
        cache_dir = self._cache_dir(source_dir)
        if not cache_dir.is_dir():
            return
        valid_names = {
            self._cache_path(source_dir, file_hash).name for file_hash in current_hashes
        }
        pruned: list[Path] = []
        for path in cache_dir.glob("*.pkl"):
            if path.name not in valid_names:
                try:
                    path.unlink()
                    pruned.append(path)
                except OSError as exc:
                    self.logger.warning(
                        f"Could not remove stale cache entry {path}: {exc}"
                    )
        for path in cache_dir.glob("*.pkl.corrupt"):
            stem = path.name[: -len(".corrupt")]
            # Keep artifacts whose source is current but still lacks a
            # valid cache entry (the failure has not healed); prune the
            # rest -- the source file is gone or was regenerated this run.
            if stem in valid_names and not (cache_dir / stem).is_file():
                continue
            try:
                path.unlink()
                pruned.append(path)
            except OSError as exc:
                self.logger.warning(
                    f"Could not remove stale corrupt artifact {path}: {exc}"
                )
        if pruned:
            self.logger.info(
                f"Pruned {len(pruned)} stale cache entr{'y' if len(pruned) == 1 else 'ies'} "
                f"from {cache_dir}"
            )
            self.logger.debug(f"Pruned: {[p.name for p in pruned]}")

    def _save_to_cache(
        self,
        docs: list[Document],
        extracted: dict[str, Any],
        source_dir: Path,
        file_hash: str,
    ) -> None:
        """Pickle *docs* and their extracted metadata to the cache directory.

        Creates the cache directory if it does not exist.  The cache
        entry is a ``(docs, extracted)`` tuple so that cache hits can
        restore the full-cascade bibliographic extraction instead of
        degrading to a weaker regex-only pass.  (Legacy cache entries
        hold a plain list of docs; :meth:`_load_from_cache` handles both.)

        The write is atomic: the pickle is dumped to a temp file in the
        cache directory and then ``os.replace``d onto the final ``.pkl``
        path, so a crash or kill mid-write can never leave a truncated
        or empty cache entry that would poison future runs.

        :param docs: List of chunked documents to cache
        :param extracted: Bibliographic metadata extracted for the file
        :param source_dir: Resolved source directory path
        :param file_hash: xxhash digest of the source file
        """
        cache_dir = self._cache_dir(source_dir)
        cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(source_dir, file_hash)
        tmp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=cache_dir,
                prefix=f"{path.stem}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = Path(f.name)
                pickle.dump((docs, extracted), f)
            os.replace(tmp_path, path)
        finally:
            # A failed dump/replace must not leave a stray temp file; after
            # a successful os.replace the temp path no longer exists.
            if tmp_path is not None and tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
        self.logger.info(f"Cached {len(docs)} chunks to {path}")

    def _load_from_cache(
        self, source_dir: Path, file_hash: str
    ) -> tuple[list[Document], dict[str, Any]] | None:
        """Load pickled chunks and their extracted metadata from the cache.

        To inspect cached chunks from a Python shell::

            import pickle
            from pathlib import Path
            for p in Path("<source_dir>/.klea-cache/").glob("*.pkl"):
                data = pickle.load(open(p, "rb"))
                docs, extracted = data if isinstance(data, tuple) else (data, {})
                print(p.stem, docs[0].metadata.get("headings"), extracted)

        Handles legacy cache entries (a plain list of documents) by
        returning an empty extracted dict for them.

        A corrupt or unreadable entry (empty/truncated file, malformed
        pickle, or a pickle whose class definitions changed between
        versions) is treated as a cache miss: the file is moved aside as
        ``<hash>.pkl.corrupt`` (preserving the bytes for debugging) with
        a warning so the source document is re-converted on the next
        pass, rather than aborting the run.  :meth:`_prune_cache` removes
        the artifact once a valid entry is regenerated.

        :param source_dir: Resolved source directory path
        :param file_hash: xxhash digest of the source file
        :returns: ``(docs, extracted)``, or ``None`` if the cache file
            does not exist or is corrupt
        """
        path = self._cache_path(source_dir, file_hash)
        if not path.is_file():
            return None
        self.logger.debug(f"Cache hit: {path.name}")
        try:
            with open(path, "rb") as f:
                data = pickle.load(f)
        except (
            EOFError,
            pickle.UnpicklingError,
            AttributeError,
            ImportError,
            ValueError,
        ) as exc:
            # A torn write (e.g. from a crash mid-pickle.dump) or a stale
            # entry from an older pipeline can leave an unreadable cache
            # file.  Move it aside (preserving the bytes for debugging)
            # and treat it as a cache miss so the source is re-converted;
            # :meth:`_prune_cache` removes the artifact once the entry is
            # regenerated.
            corrupt_path = path.with_name(path.name + ".corrupt")
            try:
                path.replace(corrupt_path)
                self.logger.warning(
                    f"Discarding corrupt cache entry {path.name} "
                    f"(moved to {corrupt_path.name}): {exc}"
                )
            except OSError as exc:
                self.logger.warning(
                    f"Could not move corrupt cache entry {path} aside: {exc}"
                )
            return None
        if isinstance(data, tuple):
            docs, extracted = data
            return docs, extracted or {}
        return data, {}

    # ------------------------------------------------------------------
    # Metadata map helpers
    # ------------------------------------------------------------------

    def _load_metadata_map(self, metadata_map_path: str) -> dict[str, dict[str, Any]]:
        """Load and validate a metadata map JSON file.

        The file must contain a JSON object with string keys (heading
        text) and dict values of metadata key-value pairs.  An optional
        ``DEFAULT`` key provides a fallback when no heading matches.

        :param metadata_map_path: Path to the JSON file
        :returns: Mapping of heading text to metadata dicts
        :raises FileNotFoundError: If the path does not exist
        :raises ValueError: If the JSON is not well-formed
        """
        path = Path(metadata_map_path)
        if not path.is_file():
            raise FileNotFoundError(f"Metadata map file not found: {path}")
        # Remember the exact file so _find_files can exclude it from the
        # ingestible set when it lives inside the source directory.
        self._metadata_map_path = path.resolve()
        self.logger.info(f"Loading metadata map from {path}")
        with open(path) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise TypeError(
                f"Metadata map must be a JSON object (dict), got {type(data).__name__}"
            )
        for k, v in data.items():
            if not isinstance(k, str):
                raise TypeError(
                    f"Metadata map keys must be strings, got {type(k).__name__}"
                )
            if not isinstance(v, dict):
                raise TypeError(
                    f"Values in metadata map must be dicts, "
                    f"got {type(v).__name__} for key {k!r}"
                )
        # Normalise heading keys so user-filled keys (possibly pasted with
        # typographic artifacts) match the normalised chunk headings.  The
        # ``DEFAULT`` key passes through unchanged.
        changed_keys = [k for k in data if normalize_text(k) != k]
        if changed_keys:
            self.logger.debug(
                f"Normalised {len(changed_keys)} heading keys in metadata map"
            )
        data = {normalize_text(k): v for k, v in data.items()}
        self.logger.info(f"Loaded metadata map with {len(data)} entries from {path}")
        return data

    def _resolve_metadata_map(
        self,
        source_path: Path,
        metadata_map_path: str | None,
    ) -> dict[str, dict[str, Any]] | None:
        """Resolve the metadata map to use for ingestion.

        An explicit *metadata_map_path* always wins.  Otherwise, when the
        source directory contains the generated
        ``metadata-map.template.json``, it is used as an auto-fallback (its
        per-file ``DEFAULT`` entries are pre-filled with extracted
        bibliographic metadata).  Returns ``None`` when neither exists --
        :meth:`build` then generates a map from what ``chunk`` produced,
        while the ``store`` CLI aborts (``store`` is expected to consume the
        template a prior ``chunk`` wrote).

        Raises ``ValueError`` when the resolved map is empty (``{}``): an
        empty map carries no metadata at all and is almost certainly a
        mistake, even when passed explicitly with ``--metadata-map``.

        :param source_path: Resolved source directory path
        :param metadata_map_path: Explicit metadata map path, or ``None``
        :returns: Loaded metadata map, or ``None`` when no map exists
        :raises ValueError: If the resolved map is empty
        """
        map_source = metadata_map_path
        if metadata_map_path:
            metadata_map = self._load_metadata_map(metadata_map_path)
        else:
            template = self._cache_dir(source_path) / TEMPLATE_FILE_NAME
            if not template.is_file():
                return None
            self.logger.info(
                f"No --metadata-map given; auto-falling back to {template}"
            )
            map_source = str(template)
            metadata_map = self._load_metadata_map(map_source)
        if not metadata_map:
            raise ValueError(
                f"The metadata map from {map_source} has no entries. "
                f"Add one entry per source file (a DEFAULT entry is "
                f"enough) before storing."
            )
        return metadata_map

    def _resolve_metadata(
        self,
        file_name: str,
        headings: list[str] | None,
        metadata_map: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        """Resolve a metadata dict for a chunk using the per-file metadata map.

        Looks up the file in the map, then matches the heading chain
        from most specific to least specific: the full joined chain, then
        progressively shorter suffixes, then progressively shallower
        ancestor chains.  The first non-empty matching entry is merged
        over ``DEFAULT`` (gap-fill: heading-specific keys win, ``DEFAULT``
        fills everything else), so a heading that only sets e.g. a ``url``
        still inherits the file's authors/year/journal, and a leaf section
        with no metadata inherits its nearest ancestor's.  An entry that
        matches a heading but is empty (a ``{}`` placeholder the user did
        not fill in) falls through to the next candidate, and finally to
        ``DEFAULT``.

        :param file_name: Source filename to look up in the map
        :param headings: Heading hierarchy for the chunk (most specific
            last), or ``None``
        :param metadata_map: Per-file metadata map
            ``{file_name: {"DEFAULT": {}, "heading": {...}}}``
        :returns: Matched metadata dict, or ``None``
        """
        file_map = metadata_map.get(file_name)
        if file_map is None:
            if metadata_map:
                self.logger.debug(
                    f"No metadata map entry for {file_name}; nothing to "
                    f"resolve (falling back to no metadata)"
                )
            return None
        fallback = file_map.get("DEFAULT")
        if headings:
            # Match the heading hierarchy from most specific to least
            # specific.  The template keys written by
            # :meth:`write_heading_template` are full heading chains
            # (e.g. ``"Chapter > Section"``), so try the joined chain
            # first, then progressively shorter suffixes (e.g.
            # ``"Section"``), then progressively shallower ancestor
            # chains (e.g. ``"Chapter"``).  This makes the autogenerated
            # template keys actually consumable: a researcher can fill in
            # a chain key for a deep section, a shorter suffix for a
            # chapter, or a single heading, and the most specific
            # populated entry wins -- a leaf section with no metadata of
            # its own inherits the nearest ancestor's.  Single-heading
            # maps still work -- a one-element suffix is just the
            # individual heading.
            suffixes = [" > ".join(headings[i:]) for i in range(len(headings))]
            prefixes = [
                " > ".join(headings[:i]) for i in range(len(headings) - 1, 0, -1)
            ]
            candidates = suffixes + [p for p in prefixes if p not in suffixes]
            self.logger.debug(
                f"Resolving metadata for {file_name}: trying keys {candidates} in order"
            )
            for key in candidates:
                if key in file_map:
                    matched = file_map[key]
                    if matched:
                        merged = {**(fallback or {}), **matched}
                        self.logger.debug(
                            f"Resolved metadata for {file_name}: '{key}' "
                            f"(merged over DEFAULT)"
                        )
                        return merged
                    # Empty placeholder the user left unfilled; keep
                    # looking (fall through to DEFAULT) rather than
                    # returning metadata that strips the DEFAULT values.
                    self.logger.debug(
                        f"Empty metadata entry for {file_name}: "
                        f"'{key}'; falling through"
                    )
                else:
                    self.logger.debug(
                        f"No map entry for {file_name}: '{key}'; "
                        f"trying the next candidate"
                    )
        if fallback:
            self.logger.debug(f"Resolved DEFAULT metadata for {file_name}")
        else:
            self.logger.debug(
                f"No metadata resolved for {file_name} from the map "
                f"(no matching heading entry and no DEFAULT)"
            )
        return fallback

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _find_files(self, source_dir: Path) -> list[Path]:
        """Return the ingestible files in *source_dir*.

        Thin wrapper over :func:`~klea_utils.stores.utils.find_source_files`
        that supplies this builder's loaded metadata map and configured
        store directory as exclusions.

        :param source_dir: Directory to walk recursively
        :returns: Sorted list of files with supported extensions
        """
        return find_source_files(
            source_dir,
            metadata_map_path=self._metadata_map_path,
            store_dir=self.store_dir,
            logger=self.logger,
        )

    def _ensure_tokenizer(self) -> None:
        """Download the HuggingFace tokenizer used for token-aware chunking
        if it is not already cached locally.

        ..  TODO:: Allow overriding ``tokenizer_model`` via an environment
            variable (e.g. ``KLEA_INGEST_TOKENIZER_MODEL``) or a local
            filesystem path so that air-gapped deployments can point at
            pre-downloaded tokenizer files.
        """
        from transformers import AutoTokenizer

        self.logger.debug(f"Ensuring tokenizer '{self.tokenizer_model}' is available")
        AutoTokenizer.from_pretrained(self.tokenizer_model)

    def _get_converter(self):
        """Lazily initialise and return the Docling
        :class:`~docling.document_converter.DocumentConverter` singleton.

        When :attr:`do_ocr` is ``False``, the PDF pipeline is configured
        with OCR disabled, which speeds up conversion of text-based PDFs
        significantly (scanned/image-based PDFs then lose their
        embedded text).

        :returns: Shared :class:`~docling.document_converter.DocumentConverter`
            instance
        """
        if self._converter is None:
            self.logger.debug(
                f"Initialising Docling DocumentConverter (do_ocr={self.do_ocr})"
            )
            from docling.document_converter import (
                DocumentConverter,
                PdfFormatOption,
            )

            if self.do_ocr:
                self._converter = DocumentConverter()
            else:
                # Lazy: the pipeline-options imports pull in docling's
                # pipeline machinery; only needed when OCR is disabled.
                from docling.datamodel.base_models import InputFormat
                from docling.datamodel.pipeline_options import PdfPipelineOptions

                options = PdfPipelineOptions()
                options.do_ocr = False
                self._converter = DocumentConverter(
                    format_options={
                        InputFormat.PDF: PdfFormatOption(pipeline_options=options)
                    }
                )
        return self._converter

    def _get_chunker(self):
        """Lazily initialise and return the
        :class:`~docling.chunking.HybridChunker`
        configured with the instance tokenizer and chunking parameters.

        :returns: Configured :class:`~docling.chunking.HybridChunker` instance
        """
        if self._chunker is None:
            self.logger.debug(
                f"Initialising HybridChunker "
                f"(max_tokens={self.max_tokens}, merge_peers={self.merge_peers})"
            )
            from docling.chunking import HybridChunker
            from docling_core.transforms.chunker.tokenizer.huggingface import (
                HuggingFaceTokenizer,
            )
            from transformers import AutoTokenizer

            hf_tokenizer = AutoTokenizer.from_pretrained(self.tokenizer_model)
            tokenizer = HuggingFaceTokenizer(
                tokenizer=hf_tokenizer, max_tokens=self.max_tokens
            )
            self._chunker = HybridChunker(
                tokenizer=tokenizer, merge_peers=self.merge_peers
            )
        return self._chunker

    def _convert_and_chunk(
        self, file_path: Path, resolver: Resolver | None
    ) -> tuple[list[Document], dict[str, Any]]:
        """Convert ``file_path`` with Docling, chunk with the
        :class:`~docling.chunking.HybridChunker`,
        and return :class:`~langchain_core.documents.Document` objects
        alongside the automatically-extracted bibliographic metadata.

        Each document's metadata includes a ``headings`` list (the heading
        hierarchy for the chunk).  The extracted metadata (see
        :func:`~klea_utils.biblio.extract.extract_metadata`) is used to
        pre-fill the per-file template ``DEFAULT`` entry; it is not
        attached to the chunks themselves.

        :param file_path: Path to the source document file
        :param resolver: DOI resolver for the extraction cascade, or
            ``None`` to skip DOI resolution
        :returns: ``(docs, extracted)`` where *docs* is the list of
            chunked :class:`~langchain_core.documents.Document` objects
            ready for embedding and *extracted* is the flat
            bibliographic metadata dict
        """
        converter = self._get_converter()
        chunker = self._get_chunker()

        self.logger.info(f"Converting {file_path.name} with Docling")
        result = converter.convert(str(file_path))
        dl_doc = result.document

        docs: list[Document] = []
        for chunk in chunker.chunk(dl_doc=dl_doc):
            raw_text = chunker.contextualize(chunk=chunk)
            chunk_text = normalize_text(raw_text)
            meta = chunk.meta.model_dump()
            # DocMeta.headings is Optional[list[str]] (default None) for
            # chunks not under a heading hierarchy; normalise to [].
            raw_headings = meta.get("headings") or []
            headings = [normalize_text(heading) for heading in raw_headings]

            if chunk_text != raw_text:
                self.logger.debug(
                    f"Normalised chunk text: {len(raw_text)} -> {len(chunk_text)} chars"
                )
            if headings != raw_headings:
                self.logger.debug(f"Normalised headings: {raw_headings} -> {headings}")

            doc = Document(
                page_content=chunk_text,
                metadata={"headings": headings},
            )
            docs.append(doc)

        extracted = extract_metadata(
            dl_doc,
            str(file_path),
            pdf_path=str(file_path) if file_path.suffix.lower() == ".pdf" else None,
            resolver=resolver,
        )

        # Fall back to the first chunk heading when the cascade produced
        # only the filename stem (the merge tiers' last resort).  The
        # chunker's heading detection is layout-aware and often recovers
        # the real title (e.g. conference preprints) where Docling did not
        # label a TITLE item.  See _first_heading_title.
        if not extracted.get("title") or extracted["title"] == Path(file_path).stem:
            heading_title = _first_heading_title(docs)
            if heading_title:
                self.logger.debug(
                    f"Title fallback for {file_path.name}: "
                    f"using first chunk heading {heading_title!r}"
                )
                extracted["title"] = heading_title
                sources = extracted.setdefault("_sources", [])
                if "chunk-heading" not in sources:
                    sources.append("chunk-heading")

        return docs, extracted

    def _make_resolver(self, source_path: Path) -> Resolver:
        """Build a DOI resolver for a source directory.

        The resolver caches resolved DOIs under the source directory's
        ``.klea-cache/`` and picks up the ``KLEA_INGEST_MAILTO``
        polite-pool address from the environment.

        :param source_path: Resolved source directory path
        :returns: A configured
            :class:`~klea_utils.biblio.doi.DoiResolver`
        """
        # Lazy: importing the DOI resolver pulls in httpx; it is only
        # needed when documents are being converted.
        from ..biblio.doi import DoiResolver

        return DoiResolver(cache_dir=self._cache_dir(source_path))


def _hash_file(file_path: Path) -> str:
    """Return an xxhash hex digest of a file's contents.

    :param file_path: Path to the file to hash
    :returns: Hex digest string prefixed with ``"xxh64:"``
    """
    h = xxhash.xxh64()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return f"xxh64:{h.hexdigest()}"


def _apply_store_metadata_policy(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return *metadata* with internal and provenance keys removed.

    The stored-metadata key policy is a whitelist + metadata-map
    pass-through: the always-stored keys
    (:data:`ALWAYS_STORED_METADATA_KEYS` plus any ``url*`` key) and
    whatever the researcher put in the metadata map are kept, while
    ``_``-prefixed internal keys and the provenance keys in
    :data:`STORE_DROPPED_METADATA_KEYS` are dropped.  Applied both when the
    metadata map is folded into chunks (:meth:`StoresBuilder.chunk_all`) and
    as a final gate in :func:`_sanitize_store_metadata`.

    Person-name list fields (:data:`PERSON_NAME_FILTER_FIELDS`) are
    additionally expanded with per-word variants (see
    :func:`klea_utils.stores.utils.expand_person_names`), so an exact
    ``$contains`` filter matches a partial name.  The expansion is
    idempotent, so re-applying the policy is safe.

    :param metadata: Document metadata dict
    :returns: Copy of *metadata* without internal/provenance keys
    """
    filtered = {
        key: value
        for key, value in metadata.items()
        if not (key.startswith("_") or key in STORE_DROPPED_METADATA_KEYS)
    }
    for key in PERSON_NAME_FILTER_FIELDS:
        value = filtered.get(key)
        if isinstance(value, list):
            filtered[key] = expand_person_names(value)
    return filtered


def _sanitize_store_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of *metadata* ready for a vector-store upsert.

    Two filters are applied, each on a copy:

    1. **Key policy** (:func:`_apply_store_metadata_policy`) -- drops
       ``_``-prefixed internal keys and provenance keys, so nothing leaks
       into the store no matter how it entered the chunk metadata.
    2. **Value sanitization** -- Chroma rejects empty-list and ``None``
       values on upsert.  The chunk cache deliberately keeps ``headings:
       []`` as an explicit "no headings found" marker, so the empty list
       (and any other empty/``None`` value) is dropped here, at storage
       time, on a copy -- the source documents (and the BM25 corpus) keep
       their metadata intact.

    :param metadata: Document metadata dict
    :returns: Copy of *metadata* without internal/provenance keys and
        without empty-list / ``None`` values
    """
    filtered = _apply_store_metadata_policy(metadata)
    return {
        key: value
        for key, value in filtered.items()
        if value is not None and value != []
    }


def _normalize_extracted_metadata(extracted: dict[str, Any]) -> dict[str, Any]:
    """Return *extracted* with typographic artifacts stripped from string fields.

    The bibliographic cascade (:func:`~klea_utils.biblio.extract.extract_metadata`)
    runs over raw converted text, so fields such as ``title``, ``authors``,
    and ``urls`` can carry soft hyphens / no-break spaces.  Normalising them
    keeps ``metadata-map.template.json``'s ``DEFAULT`` entry plain text.
    Non-string values (e.g. ``year``, ``_metadata_complete``) are untouched.

    :param extracted: Metadata dict from the extraction cascade
    :returns: Copy of *extracted* with string values normalised
    """
    normalized: dict[str, Any] = {}
    for key, value in extracted.items():
        if isinstance(value, str):
            normalized[key] = normalize_text(value)
        elif isinstance(value, list):
            normalized[key] = [
                normalize_text(item) if isinstance(item, str) else item
                for item in value
            ]
        else:
            normalized[key] = value
    return normalized


def _split_url_list(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return *metadata* with a ``urls`` list expanded into per-url keys.

    The retrieval display and the answer-LLM context assume one URL per
    ``url*`` metadata key (every ``url``/``url_1``/... key is shown as its
    own reference).  The bibliographic cascade produces a ``urls`` list,
    which would render as a single Python-list repr.  This expands
    ``urls: [u1, u2, ...]`` into ``url_1: u1, url_2: u2, ...`` and drops
    the ``urls`` key.  An empty ``urls`` list is dropped.

    Numbering starts at ``url_1`` so a singular ``url`` key already
    provided by another tier (e.g. the PDF Info dict) is left untouched;
    indices already present in *metadata* are skipped.

    :param metadata: Flat metadata dict from the extraction cascade
    :returns: Copy of *metadata* with the ``urls`` list split into
        ``url_1``/``url_2``/... keys
    """
    urls = metadata.get("urls")
    if not urls:
        return {k: v for k, v in metadata.items() if k != "urls"}

    split = dict(metadata)
    split.pop("urls", None)
    index = 1
    for url in urls:
        while f"url_{index}" in split:
            index += 1
        split[f"url_{index}"] = url
        index += 1
    return split


def _ensure_doi_url(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return *metadata* with a ``url_doi`` key derived from ``doi``.

    The answer LLM receives the bare ``doi`` identifier, but a resolvable
    URL form is more reliable than expecting it to construct one (and lets
    the references panel show ``doi: <url>`` via the ``url_<label>``
    convention).  Adds ``url_doi = https://doi.org/<doi>`` when a ``doi``
    is present and ``url_doi`` is not already set (so the researcher's own
    value wins).

    :param metadata: Flat metadata dict from the extraction cascade
    :returns: Copy of *metadata* with ``url_doi`` derived from ``doi``
    """
    doi = metadata.get("doi")
    if not doi or "url_doi" in metadata:
        return metadata

    result = dict(metadata)
    result["url_doi"] = f"https://doi.org/{doi}"
    return result
