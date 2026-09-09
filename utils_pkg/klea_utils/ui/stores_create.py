#!/usr/bin/env python3
"""
CLI for creating stores from documents

File: klea_utils/ui/stores_create.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path

import typer

from ..plogging import resolve_log_level, setup_root_logger

app = typer.Typer(help="Create stores from documents")

#: Shared ``--debug`` option attached to every command.  When given, the
#: console shows full DEBUG logging; otherwise the console stays at INFO
#: (progress on stdout, warnings/errors on stderr).  See
#: :func:`klea_utils.plogging.resolve_log_level` for the flag/env precedence.
debug_option = typer.Option(False, "--debug", help="Enable debug logging")


def _store_dir(store_path: str) -> Path | None:
    """Return the local directory of a store URI, or ``None``.

    Backends with a local store folder (e.g. ``chroma:/path/to/store``,
    or any future filesystem-backed scheme) have a directory that may
    live inside the source directory and must be excluded from
    ingestion.  Remote backends whose location is a URL or database
    connection string (``qdrant:http://...``, ``pgvector:postgresql://...``)
    have no local folder.

    :param store_path: Vector store URI (``scheme:location``)
    :returns: Resolved local directory, or ``None`` for remote schemes
    """
    _scheme, sep, location = store_path.partition(":")
    if not sep:
        return None
    if location.startswith(("http://", "https://", "postgresql://", "postgres://")):
        return None
    return Path(location).resolve()


@app.command()
def pre_check(
    source_dir: str = typer.Argument(
        help="Directory containing source documents (PDFs)"
    ),
    organise: bool = typer.Option(
        False,
        "--organise",
        help="Copy classified files into 'ocr/' (scanned/image PDFs) and "
        "'no-ocr/' (text PDFs plus all non-PDF files) subdirectories. "
        "Copies, never moves -- your original bibliography directory is "
        "left untouched. Recommended workflow: relocate the copies to a "
        "scratch directory, then chunk and store each subdirectory into "
        "the same collection",
    ),
    debug: bool = debug_option,
):
    """Decide which PDFs need OCR before chunking.

    Reads each PDF's embedded text layer with pypdfium2 and reports
    whether it is image-based (scanned, needs OCR) or text-based (no OCR
    needed).  This lets you avoid the cost of OCR on born-digital PDFs
    while keeping it for older scanned papers -- without guessing by
    publication year.

    Without ``--organise`` the command only reports.  With ``--organise``
    it copies files into ``ocr/`` and ``no-ocr/`` subdirectories (the
    originals are never modified), then prints the recommended chunk and
    store commands to build both into the same collection.
    """
    setup_root_logger("klea-stores-create", stderr_level=resolve_log_level(debug))
    logger = logging.getLogger("klea-stores-create")

    source_path = Path(source_dir).resolve()
    if not source_path.is_dir():
        logger.error(f"Source directory not found: {source_path}")
        raise typer.Exit(1)

    try:
        # Lazy: importing the pre-check module pulls in pypdfium2 (native
        # pdfium bindings) and ingestion (docling etc.).  Deferring keeps
        # --help fast -- Python only needs the function signature.
        from klea_utils.stores.precheck import (
            classify_directory,
            format_precheck_report,
            organise_directory,
        )

        classifications = classify_directory(source_path)
        print(format_precheck_report(classifications))

        if not classifications:
            logger.info("No PDFs found to classify")
            raise typer.Exit(0)

        if organise:
            ocr_dir, no_ocr_dir = organise_directory(source_path, classifications)
            print()
            print(f"Organised copies into {ocr_dir.name}/ and {no_ocr_dir.name}/")
            print("(originals untouched; output dirs are skipped on re-runs)")
            print()
            print("Recommended workflow -- relocate the copies to a scratch dir:")
            print(f"  mv {ocr_dir.name}/ {no_ocr_dir.name}/ /tmp/biblio-build/")
            print("  cd /tmp/biblio-build")
            print(f"  klea-stores-create chunk {no_ocr_dir.name}/ --no-ocr")
            print(f"  klea-stores-create chunk {ocr_dir.name}/")
            print(
                f"  klea-stores-create store {no_ocr_dir.name}/ "
                "--collection <name> --store chroma:/path"
            )
            print(
                f"  klea-stores-create store {ocr_dir.name}/ "
                "--collection <name> --store chroma:/path"
            )
            print()
            print("(both store runs target the SAME collection, so they merge)")
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed: {e}")
        raise typer.Exit(1) from None


@app.command()
def chunk(
    source_dir: str = typer.Argument(help="Directory containing source documents"),
    max_tokens: int = typer.Option(
        450, "--max-tokens", help="Maximum tokens per chunk"
    ),
    ocr: bool = typer.Option(
        True,
        "--ocr/--no-ocr",
        help="Whether to perform optical character recognition (OCR) "
        "during PDF conversion (default: on). Keep for scanned/image "
        "PDFs; disable for text-based PDFs to speed up conversion "
        "significantly",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-process all files even if unchanged"
    ),
    worker_mem_limit: float = typer.Option(
        4.0,
        "--worker-mem-limit",
        help="Maximum memory (in GiB) a conversion worker process may "
        "use before it is restarted.  This includes the ~1-1.5 GiB "
        "Docling's models occupy at worker startup, so the headroom for "
        "Docling's per-conversion memory growth is roughly the limit "
        "minus that.  Uncached files are converted in short-lived "
        "subprocesses so that growth is released when a worker exits; "
        "raise this on machines with more RAM to restart workers less "
        "often",
    ),
    worker_batch_size: int = typer.Option(
        200,
        "--worker-batch-size",
        help="Maximum files handed to any single conversion worker "
        "before it is restarted.  A worker stops at whichever comes "
        "first: its memory cap or this batch size",
    ),
    debug: bool = debug_option,
):
    """Chunk and cache documents without writing to a vector store.

    Converts all files in SOURCE_DIR with Docling, chunks them, and
    caches the result in ``<source_dir>/.klea-cache/``.  Also writes a
    ``metadata-map.template.json`` file organised by source file, with
    empty ``{}`` placeholders for each heading chain.  Fill in the
    metadata values and pass the file to
    ``klea-stores-create store --metadata-map``.

    Uncached files are converted in short-lived worker subprocesses
    (bounded by ``--worker-mem-limit``), so a run over a very large
    corpus stays memory-bounded instead of being killed by the kernel
    when Docling's per-conversion memory growth is never released.
    Already-cached files are handled in-process and never spawn workers.
    """
    setup_root_logger("klea-stores-create", stderr_level=resolve_log_level(debug))
    logger = logging.getLogger("klea-stores-create")

    # Guard: chunking needs the [ingest] extra (docling etc.).
    # Lazy: require_extra uses only find_spec (stdlib) so --help stays fast.
    try:
        from klea_utils.imports import require_extra

        require_extra("docling", "ingest")
    except ImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    logger.info(
        f"Chunking documents in {source_dir}"
        f"\n  Max tokens: {max_tokens}"
        f"\n  Worker mem limit: {worker_mem_limit} GiB"
        f"\n  Worker batch size: {worker_batch_size}"
    )

    try:
        # Lazy: importing StoresBuilder pulls in ingestion.py -> llm.py ->
        # langchain_huggingface/langchain_ollama, stores/utils.py ->
        # chromadb/qdrant etc.  Deferring to function body keeps
        # --help fast (Python only needs the function signature).
        from pathlib import Path

        from klea_utils.stores.ingestion import (
            CACHE_DIR_NAME,
            TEMPLATE_FILE_NAME,
            StoresBuilder,
        )

        builder = StoresBuilder(
            embedding_model="",  # not needed for chunking only
            logger=logger,
            max_tokens=max_tokens,
            do_ocr=ocr,
        )
        source_path = Path(source_dir).resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source directory not found: {source_path}")

        # Cache-only: the conversion runs in subprocess workers
        # (worker_mem_limit) and only the per-file heading chains come
        # back, keeping this run's memory bounded on very large corpora.
        file_headings = builder.chunk_all(
            source_path,
            force=force,
            worker_mem_limit=int(worker_mem_limit * 1024**3),
            worker_batch_size=worker_batch_size,
        )
        builder.write_heading_template(file_headings, source_path)
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed: {e}")
        raise typer.Exit(1) from None

    if not file_headings:
        logger.error(
            f"No files were successfully chunked from {source_path} -- see errors above"
        )
        raise typer.Exit(1)
    logger.info("Chunking complete -- cache is ready")
    from klea_utils.stores.ingestion import CACHE_DIR_NAME

    logger.info(
        f"Review/update the metadata map before storing: "
        f"{source_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME} -- copy it out, "
        f"fill in per-heading entries, then run 'klea-stores-create store' "
        f"with --metadata-map"
    )
    logger.info("Metadata map summary:")
    try:
        import json

        from klea_utils.stores.map_lint import (
            format_metadata_lint_report,
            lint_metadata_map,
        )

        map_path = source_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        with open(map_path) as f:
            data = json.load(f)
        print(format_metadata_lint_report(lint_metadata_map(data)))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Could not lint metadata map: {e}")


@app.command()
def map_lint(
    source_dir: str = typer.Argument(
        help="Directory containing the metadata map (uses "
        "metadata-map.template.json in the .klea-cache folder unless "
        "--metadata-map is given)"
    ),
    metadata_map_path: str = typer.Option(
        None,
        "--metadata-map",
        "-M",
        help="Explicit metadata-map JSON file to lint, instead of the "
        "template in SOURCE_DIR",
    ),
    debug: bool = debug_option,
):
    """Report issues in a metadata map so it can be reviewed efficiently.

    Runs only deterministic checks (missing fields, suspicious titles or
    DOIs, year/filename mismatches, stale 'venue' keys, excess url* keys,
    placeholder counts, and whether the top-level keys are the actual
    source filenames) -- no LLM is needed.  Useful after editing the
    map by hand, and printed automatically at the end of 'chunk'.

    A source file with no map entry is fatal (the store step raises), so
    the full report is printed first and the command then exits non-zero
    when any such file is found.
    """
    setup_root_logger("klea-stores-create", stderr_level=resolve_log_level(debug))
    logger = logging.getLogger("klea-stores-create")

    try:
        import json
        from pathlib import Path

        from klea_utils.stores.map_lint import (
            format_metadata_lint_report,
            lint_metadata_map,
        )
        from klea_utils.stores.utils import (
            CACHE_DIR_NAME,
            TEMPLATE_FILE_NAME,
            find_source_files,
        )

        source_path = Path(source_dir).resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source directory not found: {source_path}")

        if metadata_map_path:
            map_path = Path(metadata_map_path)
        else:
            map_path = source_path / CACHE_DIR_NAME / TEMPLATE_FILE_NAME
        if not map_path.is_file():
            raise FileNotFoundError(f"Metadata map not found: {map_path}")

        with open(map_path) as f:
            data = json.load(f)
        # The expected keys are exactly what the store step would ingest:
        # the files find_source_files returns, minus the map file itself
        # (which _load_metadata_map excludes at store time).  This mirrors
        # the strict parity so a map that lints clean is guaranteed to
        # resolve at store time.
        source_files = {
            f.name
            for f in find_source_files(
                source_path,
                metadata_map_path=map_path.resolve(),
                logger=logger,
            )
        }
        report = lint_metadata_map(data, source_files=source_files)
        print(format_metadata_lint_report(report))
        if report["missing_keys"]:
            raise typer.Exit(1)
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed: {e}")
        raise typer.Exit(1) from None


@app.command()
def store(
    source_dir: str = typer.Argument(help="Directory containing source documents"),
    collection_name: str = typer.Option(
        ...,
        "--collection",
        "-n",
        help="Collection name for the vector store. Must match the "
        "'name' of the corresponding vector_stores/bm25_stores entry in "
        "the RAG config file (e.g. klea.json); a different name on an "
        "existing store file creates a new collection",
    ),
    store_path: str = typer.Option(
        ...,
        "--store",
        "-s",
        help="Vector store URI (e.g. chroma:/path/to/store). For local "
        "Chroma stores, point at the store folder: the database file "
        "inside it is always named chroma.sqlite3",
    ),
    embedding_model: str = typer.Option(
        "ollama:bge-m3:latest",
        "--model",
        "-m",
        help="Embedding model identifier",
    ),
    metadata_map_path: str = typer.Option(
        None,
        "--metadata-map",
        "-M",
        help="JSON file keyed by source filename; each file entry maps "
        "heading chains to metadata dicts (with per-file DEFAULT fallback). "
        "Defaults to metadata-map.template.json in the source "
        "directory's cache folder; required when no template exists",
    ),
    bm25_store: str = typer.Option(
        None,
        "--bm25-store",
        help="Write the combined document corpus to this path for BM25 "
        "retrieval (a pickle of all chunked documents). Defaults to "
        "<collection>.pkl in the current directory; the file can be "
        "moved after creation",
        show_default="<collection>.pkl",
    ),
    embed_batch_size: int = typer.Option(
        256,
        "--embed-batch-size",
        help="Number of chunks embedded per store write call. Smaller "
        "values report progress more frequently; larger values reduce "
        "per-request overhead on very large corpora",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Drop the collection and re-store all files from scratch "
        "(the portable way to update a collection; documents within a "
        "collection cannot be updated in place).  Without --force, "
        "`store` is incremental: unchanged files are skipped and changed "
        "files are updated in place.  Does not reconvert: files must "
        "already be cached by 'chunk'",
    ),
    debug: bool = debug_option,
):
    """Write cached document chunks to a vector store.

    Cache-only: every source file must already have a cache entry (run
    ``klea-stores-create chunk`` first).  Reads the cached chunks from
    ``<source_dir>/.klea-cache/``, applies the metadata map, and writes
    them to the vector store.  Conversion settings (OCR, max tokens)
    belong to ``chunk``; ``store`` never converts on the fly.

    Incremental by default: a store manifest in
    ``<source_dir>/.klea-cache/`` records which files are in the
    collection, so unchanged files are skipped and changed files are
    updated in place.  Pass ``--force`` to drop the whole collection
    and rebuild it (documents within a collection cannot be updated in
    place across all backends).

    The ``--bm25-store`` option (default ``<collection>.pkl`` in the
    current directory) writes the combined chunked documents to a single
    pickle file that can be used as a BM25 store.

    Run ``klea-stores-create chunk`` first to populate the cache and
    generate a ``metadata-map.template.json``.
    """
    setup_root_logger("klea-stores-create", stderr_level=resolve_log_level(debug))
    logger = logging.getLogger("klea-stores-create")

    # Guard: storing reuses the ingest cache (docling) and vector-store
    # backends. The ingest check is cheap and keeps --help fast.
    try:
        from klea_utils.imports import require_extra

        require_extra("docling", "ingest")
    except ImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    logger.info(
        f"Storing cached chunks to '{collection_name}' at {store_path}"
        f"\n  Source: {source_dir}"
        f"\n  Model: {embedding_model}"
        f"\n  Metadata map: {metadata_map_path or '(none)'}"
    )

    # Typer cannot compute a default that depends on another argument, so
    # the dynamic --bm25-store default is resolved here.
    if bm25_store is None:
        bm25_store = f"{collection_name}.pkl"
    logger.info(f"BM25 store: {bm25_store}")

    try:
        from pathlib import Path

        from klea_utils.stores.ingestion import TEMPLATE_FILE_NAME, StoresBuilder

        builder = StoresBuilder(
            embedding_model=embedding_model,
            logger=logger,
            embed_batch_size=embed_batch_size,
            store_dir=_store_dir(store_path),
        )
        source_path = Path(source_dir).resolve()
        if not source_path.is_dir():
            raise FileNotFoundError(f"Source directory not found: {source_path}")

        metadata_map = builder._resolve_metadata_map(source_path, metadata_map_path)
        if metadata_map is None:
            # store is the review-driven path: it consumes the template a
            # prior chunk run wrote (or an explicit --metadata-map), so a
            # missing map here is a misconfiguration, not a silent fallback.
            raise ValueError(
                f"No metadata map found for {source_path}. Run "
                f"'klea-stores-create chunk' to generate "
                f"{TEMPLATE_FILE_NAME} in the source directory's cache "
                f"folder, or pass --metadata-map."
            )

        results = builder._load_and_fold_results(source_path, metadata_map)
        # results is a lazy generator (store_all consumes it per file), so
        # emptiness is checked on the source directory rather than len().
        if not builder._find_files(source_path):
            logger.error(
                f"No files were successfully chunked from "
                f"{source_path} -- see errors above"
            )
            raise typer.Exit(1)
        builder.store_all(
            results,
            store_path,
            collection_name,
            source_path,
            force=force,
            bm25_path=bm25_store,
        )
        # Auto-print a store-lint report of the just-written BM25 corpus,
        # so the researcher can review chunking/metadata quality without
        # unpickling the file (mirrors map-lint being auto-printed after
        # chunk).  Only fires when a BM25 corpus was actually written.
        bm25_file = Path(bm25_store)
        if bm25_file.is_file():
            try:
                import pickle

                from klea_utils.stores.postcheck import (
                    format_store_lint_report,
                    lint_store,
                    select_sample_windows,
                )

                with open(bm25_file, "rb") as f:
                    docs: list = []
                    # Batched corpora hold one pickled list per batch;
                    # legacy corpora a single flat list.  Read until EOF.
                    while True:
                        try:
                            docs.extend(pickle.load(f))
                        except EOFError:
                            break
                if docs:
                    print()
                    print(
                        format_store_lint_report(
                            lint_store(docs),
                            select_sample_windows(docs, anchors=3),
                        )
                    )
            except Exception as e:  # noqa: BLE001
                self_logger = logging.getLogger("klea-stores-create")
                self_logger.warning(f"Could not auto-run store-lint: {e}")
        logger.info(f"Done -- collection '{collection_name}' is ready")
        from klea_utils.stores.ingestion import CACHE_DIR_NAME

        logger.info(
            f"The chunk cache and store manifest in "
            f"{source_path / CACHE_DIR_NAME} are reused for incremental "
            f"updates -- keep them to avoid re-converting and re-storing "
            f"everything."
        )
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed: {e}")
        raise typer.Exit(1) from None


@app.command()
def store_lint(
    corpus_path: str = typer.Argument(
        help="Path to the pickled BM25/vector corpus (e.g. <collection>.pkl)"
    ),
    samples: int = typer.Option(
        3,
        "--samples",
        help="Number of evenly-spaced sampling locations across the corpus; "
        "each shows 3 contiguous chunks (truncated text + metadata) for "
        "human review.  Pass 0 to suppress sampling",
        show_default=True,
    ),
    debug: bool = debug_option,
):
    """Report issues in a stored corpus so it can be reviewed efficiently.

    Loads the pickled BM25 corpus (a list of chunked documents) and runs
    only deterministic checks (no LLM): a corpus summary, suspicious
    chunks (near-empty text from a conversion/OCR miss, or missing
    bibliographic metadata), and structural problems (chunks without a
    ``file_name``, invalid ``page_content`` / ``year`` types).  Also
    prints ``--samples`` evenly-spaced windows of contiguous chunks so you
    can eyeball that chunking and metadata look right across the corpus --
    no need to unpickle the file yourself.  Useful after ``store``, and
    printed automatically at the end of ``store`` when a BM25 corpus was
    written.
    """
    setup_root_logger("klea-stores-create", stderr_level=resolve_log_level(debug))
    logger = logging.getLogger("klea-stores-create")

    corpus = Path(corpus_path)
    if not corpus.is_file():
        logger.error(f"Corpus file not found: {corpus}")
        raise typer.Exit(1)

    try:
        # Lazy: importing the post-check module pulls in langchain-core's
        # Document and pickle handling.  Deferring keeps --help fast --
        # Python only needs the function signature.
        import pickle

        from klea_utils.stores.postcheck import (
            format_store_lint_report,
            lint_store,
            select_sample_windows,
        )

        docs: list = []
        with open(corpus, "rb") as f:
            # Batched corpora hold one pickled list per batch; legacy
            # corpora a single flat list.  Read until EOF handles both.
            while True:
                try:
                    loaded = pickle.load(f)
                except EOFError:
                    break
                if not isinstance(loaded, list):
                    logger.error(
                        f"Corpus {corpus} is not a pickled list of documents; "
                        f"got {type(loaded).__name__}"
                    )
                    raise typer.Exit(1)
                docs.extend(loaded)

        report = lint_store(docs)
        sample_windows = select_sample_windows(docs, anchors=max(samples, 0))
        print(format_store_lint_report(report, sample_windows))
    except typer.Exit:
        raise
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed: {e}")
        raise typer.Exit(1) from None


@app.command()
def build(
    source_dir: str = typer.Argument(help="Directory containing source documents"),
    collection_name: str = typer.Option(
        ...,
        "--collection",
        "-n",
        help="Collection name for the vector store. Must match the "
        "'name' of the corresponding vector_stores/bm25_stores entry in "
        "the RAG config file (e.g. klea.json); a different name on an "
        "existing store file creates a new collection",
    ),
    store_path: str = typer.Option(
        ...,
        "--store",
        "-s",
        help="Vector store URI (e.g. chroma:/path/to/store). For local "
        "Chroma stores, point at the store folder: the database file "
        "inside it is always named chroma.sqlite3",
    ),
    embedding_model: str = typer.Option(
        "ollama:bge-m3:latest",
        "--model",
        "-m",
        help="Embedding model identifier",
    ),
    max_tokens: int = typer.Option(
        450, "--max-tokens", help="Maximum tokens per chunk"
    ),
    ocr: bool = typer.Option(
        True,
        "--ocr/--no-ocr",
        help="Whether to perform optical character recognition (OCR) "
        "during PDF conversion (default: on). Keep for scanned/image "
        "PDFs; disable for text-based PDFs to speed up conversion "
        "significantly",
    ),
    metadata_map_path: str = typer.Option(
        None,
        "--metadata-map",
        "-M",
        help="JSON file keyed by source filename; each file entry maps "
        "heading chains to metadata dicts (with per-file DEFAULT fallback). "
        "If not given, build generates metadata-map.template.json from the "
        "extraction and uses it without a review step",
    ),
    bm25_store: str = typer.Option(
        None,
        "--bm25-store",
        help="Write the combined document corpus to this path for BM25 "
        "retrieval (a pickle of all chunked documents). Defaults to "
        "<collection>.pkl in the current directory; the file can be "
        "moved after creation",
        show_default="<collection>.pkl",
    ),
    embed_batch_size: int = typer.Option(
        256,
        "--embed-batch-size",
        help="Number of chunks embedded per store write call. Smaller "
        "values report progress more frequently; larger values reduce "
        "per-request overhead on very large corpora",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Re-process all files even if unchanged"
    ),
    worker_mem_limit: float = typer.Option(
        4.0,
        "--worker-mem-limit",
        help="Maximum memory (in GiB) a conversion worker process may "
        "use before it is restarted.  This includes the ~1-1.5 GiB "
        "Docling's models occupy at worker startup, so the headroom for "
        "Docling's per-conversion memory growth is roughly the limit "
        "minus that",
    ),
    worker_batch_size: int = typer.Option(
        200,
        "--worker-batch-size",
        help="Maximum files handed to any single conversion worker "
        "before it is restarted.  A worker stops at whichever comes "
        "first: its memory cap or this batch size",
    ),
    debug: bool = debug_option,
):
    """Full pipeline: chunk, embed, and write to a vector store.

    Processes all files in SOURCE_DIR: converts them with Docling,
    chunks them, embeds them, and writes to the vector store.  This is
    memory-bounded end-to-end: conversion runs in short-lived worker
    subprocesses (bounded by ``--worker-mem-limit``) and cached chunks
    are streamed into the store one file at a time.
    Processed chunks are cached in ``<source_dir>/.klea-cache/`` so
    subsequent runs (e.g. with ``--metadata-map``) skip conversion.

    The ``--bm25-store`` option (default ``<collection>.pkl`` in the
    current directory) writes the combined chunked documents to a single
    pickle file that can be used as a BM25 store.

    The optional ``--metadata-map`` / ``-M`` flag accepts a JSON file
    organised by source file.  Within each file entry, the most specific
    heading chain match wins; a ``DEFAULT`` entry provides fallback for
    any heading not listed.

    Example metadata-map.json::

        {
            "PrimerOnCElegans.md": {
                "DEFAULT": {},
                "C. elegans tissue morphology": {
                    "url": "https://example.com/worm"
                }
            },
            "c302-paper.pdf": {
                "DEFAULT": {
                    "url": "https://example.com/c302"
                }
            }
        }
    """
    setup_root_logger("klea-stores-create", stderr_level=resolve_log_level(debug))
    logger = logging.getLogger("klea-stores-create")

    # Guard: full pipeline needs [ingest] (docling). Lazy so --help stays fast.
    try:
        from klea_utils.imports import require_extra

        require_extra("docling", "ingest")
    except ImportError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from None

    logger.info(
        f"Building vector store '{collection_name}' at {store_path}"
        f"\n  Source: {source_dir}"
        f"\n  Model: {embedding_model}"
        f"\n  Max tokens: {max_tokens}"
        f"\n  Metadata map: {metadata_map_path or '(none)'}"
        f"\n  Worker mem limit: {worker_mem_limit} GiB"
        f"\n  Worker batch size: {worker_batch_size}"
    )

    # Typer cannot compute a default that depends on another argument, so
    # the dynamic --bm25-store default is resolved here.
    if bm25_store is None:
        bm25_store = f"{collection_name}.pkl"
    logger.info(f"BM25 store: {bm25_store}")

    try:
        # Lazy: importing StoresBuilder pulls in ingestion.py -> llm.py ->
        # langchain_huggingface/langchain_ollama, stores/utils.py ->
        # chromadb/qdrant etc.  Deferring to function body keeps
        # --help fast (Python only needs the function signature).
        from klea_utils.stores.ingestion import StoresBuilder

        builder = StoresBuilder(
            embedding_model=embedding_model,
            logger=logger,
            max_tokens=max_tokens,
            do_ocr=ocr,
            embed_batch_size=embed_batch_size,
            store_dir=_store_dir(store_path),
        )
        builder.build(
            source_dir=source_dir,
            store_uri=store_path,
            collection_name=collection_name,
            force=force,
            metadata_map_path=metadata_map_path,
            bm25_path=bm25_store,
            worker_mem_limit=int(worker_mem_limit * 1024**3),
            worker_batch_size=worker_batch_size,
        )
        logger.info(f"Done -- collection '{collection_name}' is ready")
    except Exception as e:  # noqa: BLE001
        logger.error(f"Failed: {e}")
        raise typer.Exit(1) from None


if __name__ == "__main__":
    app()
