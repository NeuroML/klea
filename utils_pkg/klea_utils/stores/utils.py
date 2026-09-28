#!/usr/bin/env python3
"""
Vector store utilities

File: klea_utils/stores/utils.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
import re
import unicodedata
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.documents import Document

from klea_utils.stores.metadata import (
    PERSON_NAME_FILTER_FIELDS,
    SHARED_DOC_METADATA_KEYS,
)

#: Name of the chunk-cache directory created inside a source directory by
#: the ingestion pipeline (holds the per-file pickled chunks, the
#: generated metadata-map template, the DOI cache, and store manifests).
#: Excluded from ingestion by :func:`find_source_files`.
CACHE_DIR_NAME = ".klea-cache"

#: Name of the metadata-map template ``chunk`` writes into the cache
#: directory, organised per source file.
TEMPLATE_FILE_NAME = "metadata-map.template.json"


def find_source_files(
    source_dir: Path,
    *,
    metadata_map_path: Path | None = None,
    store_dir: Path | None = None,
    logger: logging.Logger | None = None,
) -> list[Path]:
    """Walk ``source_dir`` and return files whose extensions are in
    docling's :class:`~docling.datamodel.base_models.FormatToExtensions`.

    This is the canonical "what will the store ingest" enumeration: the
    ingestion pipeline and ``map-lint`` both use it, so a metadata map
    that lints clean against its output is guaranteed to resolve at
    store time.  Files with unsupported extensions are logged as a
    warning (when a *logger* is given) and skipped.

    Generated artifacts are excluded: the cache directory
    (:data:`CACHE_DIR_NAME`, e.g. ``.klea-cache``), the metadata map
    passed via *metadata_map_path* (when it lives inside *source_dir*),
    and the vector store directory -- either the configured *store_dir*
    when it lies under *source_dir*, or any directory inside
    *source_dir* that contains a ``chroma.sqlite3`` (so a store created
    without setting *store_dir* is still not ingested).

    :param source_dir: Directory to walk recursively
    :param metadata_map_path: The metadata-map file to exclude from
        ingestion (mirrors how the ingestion pipeline remembers the
        loaded map)
    :param store_dir: Configured vector store directory that may live
        inside the source directory; ``None`` for remote backends with
        no local folder
    :param logger: Optional logger for the unsupported-extension warning
    :returns: Sorted list of files with supported extensions
    """
    from docling.datamodel.base_models import FormatToExtensions

    all_exts: set[str] = set()
    for exts in FormatToExtensions.values():
        all_exts.update(exts)

    source_resolved = source_dir.resolve()

    # Directories that must never be ingested: the configured store
    # (when it lives under the source dir) and any Chroma store folder
    # (a dir containing chroma.sqlite3) inside the source dir.
    skip_dirs: set[Path] = set()
    if store_dir is not None:
        resolved_store = store_dir.resolve()
        if resolved_store != source_resolved and resolved_store.is_relative_to(
            source_resolved
        ):
            skip_dirs.add(resolved_store)
    for chroma_db in source_dir.rglob("chroma.sqlite3"):
        if chroma_db.is_file():
            parent = chroma_db.parent.resolve()
            # Don't add source_dir itself  ---  when store==source, we only want to
            # exclude the DB file, not all docs in the source
            if parent != source_resolved:
                skip_dirs.add(parent)

    supported: list[Path] = []
    for f in sorted(source_dir.rglob("*")):
        if not f.is_file():
            continue
        if CACHE_DIR_NAME in f.parts:
            continue
        if metadata_map_path is not None and f.resolve() == metadata_map_path.resolve():
            continue
        if any(f.resolve().is_relative_to(skip_dir) for skip_dir in skip_dirs):
            continue
        suffix = f.suffix.lstrip(".").lower()
        if suffix in all_exts:
            supported.append(f)
        elif logger is not None:
            logger.warning(f"Skipping unsupported file: {f.name}")

    return supported


#: Metadata key holding each document's original per-source scores (e.g.
#: ``{"vector store": 0.87, "BM25": 3.21}``), set by :func:`rrf_merge`.
SOURCE_SCORES_KEY = "_source_scores"

#: HNSW distance space used for Chroma collections created by Klea.  Cosine
#: makes the vector-store relevance scores true cosine similarities
#: (``1 - cosine_distance``), so a ``score_threshold`` reads as a minimum
#: cosine similarity.
CHROMA_HNSW_SPACE = "cosine"

#: Rank offset for Reciprocal Rank Fusion.  A document at rank *r* within a
#: source's result list contributes ``1 / (RRF_K + r)`` to its fused score.
RRF_K = 60

#: Per-document character overhead attributed by :func:`truncate_reference_material`
#: for the serialized markup that wraps each reference in the LLM context
#: (``### Source document N/M: [file]`` plus the optional metadata line).
#: Approximate; the page content dominates in practice.
REF_DOC_OVERHEAD = 200

_INTERNAL_META_KEYS = {
    "file_name",
    "file_hash",
    "headings",
    SOURCE_SCORES_KEY,
    # Bibliographic extraction provenance (see klea_utils/biblio).  These
    # guide the researcher reviewing metadata-map.template.json but carry
    # no meaning for the answer LLM, so they are never serialized to it.
    "_metadata_complete",
    "_sources",
}

#: No-break space variants mapped to a regular space by :func:`normalize_text`
#: (NFKC also folds these, but the explicit mapping keeps the intent visible).
_NO_BREAK_SPACES = "\u00a0\u2007\u202f"

#: Zero-width / invisible characters stripped by :func:`normalize_text`.
#: ``\ufeff`` is the byte-order mark emitted at the start of extracted text;
#: ``\u200e``/``\u200f`` are bidi marks; ``\ufe00``-``\ufe0f`` are variation
#: selectors.
_ZERO_WIDTH_CHARS = "\ufeff\u200b\u200c\u200d\u200e\u200f\u2060\ufe00\ufe01\ufe02\ufe03\ufe04\ufe05\ufe06\ufe07\ufe08\ufe09\ufe0a\ufe0b\ufe0c\ufe0d\ufe0e\ufe0f"


def normalize_text(text: str) -> str:
    """Normalise free text for consistent indexing and retrieval.

    Document conversion (e.g. Docling's PDF extraction) embeds
    typographic artifacts that hurt search: soft hyphens (``\\u00ad``)
    split words mid-token, no-break / zero-width characters distort
    embeddings and BM25 keyword matching, and ligatures / full-width
    forms / superscripts / typographic spaces tokenise differently from
    their plain equivalents.  This strips or maps them so that indexed
    chunks and retrieval queries share the same plain-text form.

    The final pass uses NFKC compatibility composition, which (unlike NFC)
    also folds ligatures (``\\ufb01`` -> "fi"), full-width forms
    (``\\uff21`` -> "A"), superscripts (``\\u00b2`` -> "2"), typographic
    spaces (en/em/thin/ideographic), and the non-breaking hyphen
    (``\\u2011`` -> ``\\u2010``).  Typographic em/en dashes are kept
    unchanged.

    :param text: Raw text, possibly containing typographic artifacts
    :returns: Normalised plain text
    """
    # Soft hyphen: an invisible line-break hint; dropping it rejoins the
    # split word (e.g. "multi-\\u00adscale" -> "multi-scale").
    text = text.replace("\u00ad", "")
    # No-break space variants -> regular space.
    for ch in _NO_BREAK_SPACES:
        text = text.replace(ch, " ")
    # Byte-order mark and zero-width characters carry no meaning.
    for ch in _ZERO_WIDTH_CHARS:
        text = text.replace(ch, "")
    # NFKC: canonical + compatibility composition (ligatures, full-width
    # forms, superscripts, typographic spaces; see docstring).
    text = unicodedata.normalize("NFKC", text)
    # Collapse repeated horizontal whitespace and trim.
    return re.sub(r"[ \t]{2,}", " ", text).strip()


def expand_person_names(names: list[str]) -> list[str]:
    """Return *names* with per-word variants appended, order-preserving.

    Humans are referred to by parts of their full name ("find papers by
    Sinha" for an author stored as "Ankur Sinha"), so person-name list
    fields (:data:`klea_utils.stores.metadata.PERSON_NAME_FILTER_FIELDS`)
    are expanded at store time: each full name is kept, every whitespace
    token plus its lowercase form is added, and the lowercased full name
    is added too.  This makes an exact-membership retrieval filter
    (``$contains``) match the partial name in any form and case,
    uniformly on every store backend.

    Example::

        expand_person_names(["Ankur Sinha"])
        -> ["Ankur Sinha", "ankur sinha", "Ankur", "Sinha",
            "ankur", "sinha"]

    The expansion is idempotent (expanding an already-expanded list is a
    no-op), so re-applying the store metadata policy is safe.

    :param names: List of author display names
    :returns: Names plus per-word and lowercase variants, deduplicated in
        order of first appearance.  Non-string elements are skipped.
    """
    expanded: list[str] = []
    seen: set[str] = set()

    def add(variant: str) -> None:
        if variant not in seen:
            seen.add(variant)
            expanded.append(variant)

    for name in names:
        if not isinstance(name, str):
            continue
        add(name)
        add(name.lower())
        for token in name.split():
            add(token)
        for token in name.split():
            add(token.lower())

    return expanded


def display_person_names(names: list[str]) -> list[str]:
    """Return the display-safe subset of an expanded person-name list.

    :func:`expand_person_names` appends per-word and lowercase variants to
    a person-name field for filtering, but those variants must not appear
    in the reference material shown to the answer LLM (citations would
    otherwise read ``Ankur Sinha; Sinha; ankur; sinha``).  This keeps only
    the real names: whole-lowercase entries and single-word entries that
    make up a longer entry are dropped, so a genuine single-name author
    (not a token of another entry) still displays.

    Example::

        display_person_names(
            ["Ankur Sinha", "Padraig Gleeson", "Sinha", "gleeson"]
        )
        -> ["Ankur Sinha", "Padraig Gleeson"]

    :param names: Person-name list, possibly expanded
    :returns: The original full names, order-preserving
    """
    multi_word: list[str] = [
        n for n in names if isinstance(n, str) and len(n.split()) > 1
    ]
    tokens_of_others: set[str] = set()
    for n in multi_word:
        tokens_of_others.update(n.split())

    shown: list[str] = []
    for n in names:
        if not isinstance(n, str):
            continue
        if n.islower():
            continue
        if len(n.split()) == 1 and n in tokens_of_others:
            continue
        shown.append(n)
    return shown


def rrf_merge(
    result_sets: list[tuple[str, list[tuple[Document, float]]]],
    num_refs_max: int | None = None,
) -> list[tuple[Document, float]]:
    """Fuse per-source retrieval results with Reciprocal Rank Fusion.

    Scores from different retrievers (e.g. cosine similarity vs BM25) are not
    comparable, so each document is scored purely by its rank within each
    source's result list.  The original per-source scores are preserved in
    each document's :data:`SOURCE_SCORES_KEY` metadata for debugging and
    introspection (they are not shown to the answer LLM).

    :param result_sets: List of ``(source_label, results)`` pairs, where each
        *results* is a list of ``(document, score)`` tuples already ranked by
        its source
    :param num_refs_max: Maximum number of documents to return, or ``None``
        to return every fused document.  Callers that want to bound the
        context fed to an LLM should cap by characters via
        :func:`truncate_reference_material` instead of by document count.
    :returns: Documents ordered by RRF score, deduplicated by file+content,
        capped at *num_refs_max* when set
    """
    # Key by (file_name, page_content) so identical boilerplate in different
    # files is not collapsed to a single provenance
    rrf_scores: dict[tuple[str, str], float] = {}
    doc_by_key: dict[tuple[str, str], Document] = {}

    for source_label, results in result_sets:
        for rank, (doc, score) in enumerate(results, start=1):
            fname = (
                doc.metadata.get("file_name", "")
                if isinstance(doc.metadata.get("file_name"), str)
                else ""
            )
            key = (fname, doc.page_content)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
            if key not in doc_by_key:
                # Copy metadata so we don't mutate the retriever's original doc
                # (which would pollute cached results and checkpoint history)
                try:
                    # langchain Document is a Pydantic model  ---  model_copy is cheapest
                    copied = doc.model_copy(deep=True)  # type: ignore[attr-defined]
                except (AttributeError, TypeError):
                    # Fallback for plain objects / older versions
                    from langchain_core.documents import (
                        Document as _Doc,  # type: ignore[import]
                    )

                    copied = _Doc(
                        page_content=doc.page_content, metadata=dict(doc.metadata)
                    )
                copied.metadata[SOURCE_SCORES_KEY] = {}
                doc_by_key[key] = copied
            # Cast to Python floats: BM25 and vector-store scores are numpy
            # scalars (np.float64/np.float32).  The scores are persisted in
            # doc metadata, which ends up inside the graph state; LangGraph's
            # msgpack checkpoint serializer cannot encode numpy scalars, so
            # plain floats keep the state checkpoint-serializable.
            doc_by_key[key].metadata[SOURCE_SCORES_KEY][source_label] = float(score)

    merged = sorted(
        ((doc_by_key[key], rrf) for key, rrf in rrf_scores.items()),
        key=lambda tup: tup[1],
        reverse=True,
    )
    if num_refs_max is None:
        return merged
    return merged[:num_refs_max]


#: Default cross-encoder model for :func:`cross_encoder_rerank` when callers
#: enable reranking.  Small MS MARCO model; runs locally via
#: ``sentence-transformers``.
DEFAULT_CROSS_ENCODER_MODEL = "cross-encoder/ms-marco-MiniLM-L-6-v2"

_cross_encoder_cache: dict[str, Any] = {}


def _load_cross_encoder(model_name: str) -> Any:
    """Return a cached :class:`~sentence_transformers.CrossEncoder` instance."""
    cached = _cross_encoder_cache.get(model_name)
    if cached is not None:
        return cached

    # Lazily create a per-function lock to avoid races when multiple callers
    # try to initialize the same model concurrently.
    import threading

    lock = _load_cross_encoder.__dict__.setdefault("_lock", threading.Lock())
    with lock:
        cached = _cross_encoder_cache.get(model_name)
        if cached is not None:
            return cached
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            raise ImportError(
                "Cross-encoder reranking requires sentence-transformers. "
                "Install: pip install klea_utils[rerank]"
            ) from None
        _cross_encoder_cache[model_name] = CrossEncoder(model_name)
        return _cross_encoder_cache[model_name]


def cross_encoder_rerank(
    query: str,
    docs: list[tuple[Document, float]],
    *,
    model_name: str | None = None,
    top_k: int | None = None,
) -> list[tuple[Document, float]]:
    """Re-rank fused retrieval results with a cross-encoder.

    Intended to run after :func:`rrf_merge` and before downstream steps such
    as :func:`rerank_by_recency` and :func:`truncate_reference_material`.
    When *model_name* is ``None`` (the default), *docs* are returned
    unchanged so existing deployments keep their current behaviour.

    Each document is scored by a query--passage cross-encoder (local/offline
    via ``sentence-transformers``).  Original per-source scores in
    :data:`SOURCE_SCORES_KEY` metadata are preserved; only the tuple's
    relevance score is replaced by the cross-encoder score.

    :param query: Normalized retrieval query (same form as passed to
        retrievers)
    :param docs: ``(document, score)`` tuples, typically from
        :func:`rrf_merge`
    :param model_name: Hugging Face cross-encoder model id, or ``None`` to
        skip reranking.  See :data:`DEFAULT_CROSS_ENCODER_MODEL` for a
        sensible default when enabling reranking.
    :param top_k: When set, keep only the top *top_k* documents after
        reranking; ``None`` keeps the full reranked list
    :returns: Documents ordered by cross-encoder score (descending), or
        *docs* unchanged when reranking is disabled
    """
    if model_name is None or not docs:
        return docs

    model = _load_cross_encoder(model_name)
    pairs = [(query, doc.page_content) for doc, _ in docs]
    raw_scores = model.predict(pairs)

    ranked = sorted(
        ((doc, float(score)) for (doc, _), score in zip(docs, raw_scores, strict=True)),
        key=lambda item: item[1],
        reverse=True,
    )
    if top_k is None:
        return ranked
    return ranked[:top_k]


#: Weight given to the normalized relevance (RRF) component of the final
#: blended score in :func:`rerank_by_recency`.
RECENCY_WEIGHT_RELEVANCE = 0.9

#: Weight given to the recency (time) component of the final blended score.
#: Newer documents are boosted because academic work builds on -- and often
#: corrects -- earlier results, so recent information is more authoritative.
RECENCY_WEIGHT_TIME = 0.1

#: Recency score assigned to documents without a usable ``year`` metadata
#: value.  A fixed midpoint (not a derived statistic) so it is immune to
#: distribution skew: it ranks such documents below known-recent papers but
#: above the oldest retrieved document.
RECENCY_MISSING_YEAR_SCORE = 0.5


def rerank_by_recency(
    merged: list[tuple[Document, float]],
    relevance_weight: float = RECENCY_WEIGHT_RELEVANCE,
    time_weight: float = RECENCY_WEIGHT_TIME,
) -> list[tuple[Document, float]]:
    """Re-rank RRF results blending in document recency.

    Keeps :func:`rrf_merge` pure (relevance only) and applies recency as a
    separate post-fusion re-rank.  Each document's pure RRF score is
    min-max normalized to ``[0, 1]`` across the result set, a time score is
    computed from its ``year`` metadata, and the final score is a weighted
    combination:

    ``final = relevance_weight * norm_rrf + time_weight * time_score``

    The time score is ``(year - year_min) / (year_max - year_min)`` where
    ``year_min``/``year_max`` are the min and max ``year`` across the
    retrieved set (relative normalization).  Documents without a usable
    ``year`` (missing or non-int) get :data:`RECENCY_MISSING_YEAR_SCORE`.

    Division-by-zero cases are guarded: a single distinct RRF value maps to
    ``1.0`` and a single distinct year maps to ``1.0``.

    :param merged: ``(doc, rrf_score)`` tuples from :func:`rrf_merge`
    :param relevance_weight: Weight for the normalized relevance component
    :param time_weight: Weight for the recency component
    :returns: The same documents, re-sorted descending by the blended score,
        with the blended score replacing the pure RRF score in each tuple
    """
    if not merged:
        return []

    rrf_scores = [score for _, score in merged]
    rrf_min, rrf_max = min(rrf_scores), max(rrf_scores)

    years: list[int] = [
        doc.metadata["year"]
        for doc, _ in merged
        if isinstance(doc.metadata.get("year"), int)
    ]
    year_min = min(years) if years else None
    year_max = max(years) if years else None

    blended: list[tuple[Document, float]] = []
    for doc, score in merged:
        if rrf_max > rrf_min:
            norm_rrf = (score - rrf_min) / (rrf_max - rrf_min)
        else:
            norm_rrf = 1.0

        year = doc.metadata.get("year")
        if isinstance(year, int) and year_max is not None and year_min is not None:
            if year_max > year_min:
                time_score = (year - year_min) / (year_max - year_min)
            else:
                time_score = 1.0
        else:
            time_score = RECENCY_MISSING_YEAR_SCORE

        final = relevance_weight * norm_rrf + time_weight * time_score
        blended.append((doc, final))

    return sorted(blended, key=lambda tup: tup[1], reverse=True)


def truncate_reference_material(
    reference_material: dict[str, list[tuple[Document, float]]],
    max_chars: int,
) -> dict[str, list[tuple[Document, float]]]:
    """Truncate reference material to a global character budget.

    The RRF merge orders documents by fused rank but does not bound how
    much context the answer LLM receives; that is what this function does.
    Documents are consumed in RRF order per domain (domains in their dict
    order), counting ``len(page_content)`` plus the per-document
    serialization overhead (:data:`REF_DOC_OVERHEAD`), until the budget is
    exhausted.  The first document that crosses the budget is still
    included, so a single large chunk never silently yields empty context.

    :param reference_material: ``{domain: [(doc, score), ...]}`` in RRF order
    :param max_chars: Total character budget across all domains
    :returns: New mapping with the same domain keys, lists truncated to the
        budget
    """
    budgeted: dict[str, list[tuple[Document, float]]] = {
        d: [] for d in reference_material
    }
    total = 0
    crossed = False
    # Round-robin across domains to avoid starvation: take one doc at a time
    # from each domain in dict order, rather than filling first domain fully
    # before later domains get any docs.
    max_len = max((len(v) for v in reference_material.values()), default=0)
    for idx in range(max_len):
        for domain, docs in reference_material.items():
            if idx >= len(docs):
                continue
            doc, score = docs[idx]
            size = len(doc.page_content) + REF_DOC_OVERHEAD
            if total + size > max_chars:
                if crossed:
                    continue
                crossed = True
            budgeted[domain].append((doc, score))
            total += size
    return budgeted


def serialize_reference_material(
    reference_material: dict[str, list[tuple[Document, float]]],
) -> str:
    """Serialize reference material into text for use in prompt context.

    Documents are grouped by their source file (``file_name`` metadata)
    and each source file's document-level metadata is emitted once, with
    the file's chunks listed underneath.  The shared bibliographic
    fields (authors, year, journal, ...) are identical on every chunk of
    a file, so they are listed once on the source header; a ``url*`` key
    is also hoisted to the header when the whole file shares the same
    value.  Per-chunk metadata that differs (e.g. a heading-specific
    ``url``) is emitted inline so no chunk is misattributed.  Files are
    ordered by their best chunk's score; chunks within a file by score.
    Relevance scores are not included in the prompt -- the ranked order is
    what matters to the answer LLM.

    Uses Docling ``HybridChunker`` metadata format:

    - ``headings``: list of heading hierarchy (most specific last)
    - ``file_name``: source filename
    - ``_source_scores``: optional per-retriever scores (from the RRF merge)
    - Optional custom keys from the ``--metadata-map`` (e.g., ``url``)

    :param reference_material: Dict mapping query/domain to list of (doc, score) tuples
    :returns: Formatted string representation of references
    """
    serialized = ""
    for q, sorted_refs in reference_material.items():
        serialized += f"## {q}\n"

        # Group chunks by source file, keeping per-file best score for ordering.
        files: dict[str, list[tuple[Document, float]]] = {}
        for doc, score in sorted_refs:
            file_name = doc.metadata.get("file_name", "")
            files.setdefault(file_name, []).append((doc, score))

        ordered_files = sorted(
            files.items(), key=lambda item: max(s for _, s in item[1]), reverse=True
        )
        for ctr, (file_name, file_docs) in enumerate(ordered_files, 1):
            file_docs.sort(key=lambda item: item[1], reverse=True)
            first_doc = file_docs[0][0]

            # Document-level metadata: the shared bibliographic fields
            # (authors, year, journal, ...) come from the file's top chunk
            # -- every chunk of the file carries the same values, so they
            # are emitted once.  A url* key is promoted to the file level
            # too when every chunk carries the identical value (the common
            # case); otherwise it stays per-chunk below.
            file_meta: dict[str, Any] = {}
            for k, v in first_doc.metadata.items():
                if k not in SHARED_DOC_METADATA_KEYS:
                    continue
                if k in PERSON_NAME_FILTER_FIELDS and isinstance(v, list):
                    # Person-name fields carry expanded per-word variants
                    # for filtering; show only the real names.
                    v = display_person_names(v)
                file_meta[k] = v
            url_keys = {
                k for doc, _ in file_docs for k in doc.metadata if k.startswith("url")
            }
            for url_key in url_keys:
                values = {doc.metadata.get(url_key) for doc, _ in file_docs}
                if len(values) == 1 and None not in values:
                    file_meta[url_key] = first_doc.metadata.get(url_key)

            heading_str = f"[{file_name}]" if file_name else "(no file)"
            serialized += (
                f"\n### Source document {ctr}/{len(ordered_files)}: {heading_str}\n"
            )
            if file_meta:
                meta_str = " | ".join(f"{k}={v}" for k, v in file_meta.items())
                serialized += f"Metadata: {meta_str}\n"

            for chunk_ctr, (doc, _score) in enumerate(file_docs, 1):
                headings = doc.metadata.get("headings", [])
                heading_str = " > ".join(headings) if headings else "(no heading)"
                serialized += f"\n  Chunk {chunk_ctr}: {heading_str}\n"

                # Chunk-level metadata: non-internal keys not already on the
                # file level (shared bibliographic fields, and url keys the
                # whole file shares).  A differing url (e.g. heading-specific)
                # is emitted inline so no chunk is misattributed.
                chunk_meta = {
                    k: v
                    for k, v in doc.metadata.items()
                    if k not in _INTERNAL_META_KEYS
                    and k not in SHARED_DOC_METADATA_KEYS
                    and file_meta.get(k, object()) != v
                }
                if chunk_meta:
                    meta_str = " | ".join(f"{k}={v}" for k, v in chunk_meta.items())
                    serialized += f"  Chunk metadata: {meta_str}\n"
                serialized += doc.page_content

    return serialized


def instantiate_vector_store(
    path: str,
    name: str,
    embeddings,
    logger: logging.Logger,
    create: bool = False,
):
    """Instantiate a vector store based on the URI scheme in path.

    Expected format: ``"scheme:location"``.

    If ``create`` is ``True``, the store is created if it does not exist
    (relevant for ChromaDB which requires a local directory).  For
    Qdrant and PGVector the flag is a no-op --- collections are created
    on first write.

    For ChromaDB, ``location`` must point at the store folder.  Chroma
    always stores its database as ``<folder>/chroma.sqlite3`` and the
    filename is not configurable, so a path pointing at an existing file
    (even the ``chroma.sqlite3`` itself) is rejected.  The collection
    ``name`` selects which collection within the store file is
    addressed: a single ChromaDB store file can hold multiple
    collections, so reusing an existing folder with a new collection
    name creates a new collection in it.

    New Chroma collections are created with the :data:`CHROMA_HNSW_SPACE`
    HNSW distance space (cosine).  The configuration is only applied at
    collection creation; loading an existing collection keeps its own
    distance space.

    :param path: URI-style string with scheme prefix
        (e.g. ``"chroma:/path/to/dir"``,
        ``"qdrant:http://localhost:6333"``,
        ``"pgvector:postgresql://localhost/db"``)
    :param name: Collection name for the vector store
    :param embeddings: Embedding function to use
    :param logger: Logger instance
    :param create: If ``True``, allow creating a new store
    :returns: Instantiated LangChain VectorStore
    :raises ValueError: If the scheme is missing or unknown
    :raises FileNotFoundError: If ``create`` is ``False`` and a local
        ChromaDB store does not exist
    """
    scheme, sep, location = path.partition(":")
    if not sep:
        raise ValueError(
            f"Invalid vector store path '{path}': "
            f"expected format 'scheme:location' (e.g. 'chroma:/path/to/store')"
        )

    match scheme.lower():
        case "chroma":
            try:
                import chromadb
                from langchain_chroma import Chroma
            except ImportError:
                raise ImportError(
                    "ChromaDB backend not installed. "
                    "Install: pip install klea_utils[chroma]"
                ) from None

            store_dir = Path(location)
            if not store_dir.is_absolute():
                store_dir = Path.cwd() / store_dir
                logger.debug(f"Store path made absolute relative to cwd: {store_dir}")

            # Chroma always stores its database in <folder>/chroma.sqlite3
            # (the filename is not configurable), so a store is addressed
            # by its folder, never by the database file.
            if store_dir.is_file():
                raise FileNotFoundError(
                    f"'{store_dir}' is a file, not a folder. Chroma stores "
                    f"its database in a folder as 'chroma.sqlite3'; pass the "
                    f"folder path instead (e.g. 'chroma:{store_dir.parent}')"
                )

            if create:
                store_dir.mkdir(parents=True, exist_ok=True)
            else:
                if not store_dir.is_dir():
                    logger.error(f"Could not find folder: {store_dir}")
                    raise FileNotFoundError(f"Could not find folder: {store_dir}")

                store_db = store_dir / "chroma.sqlite3"
                if not store_db.is_file():
                    raise FileNotFoundError(f"ChromaDB not found at {store_db}")

            logger.debug(
                f"Loading Chroma vector store '{name}' from {store_dir.absolute()}"
            )

            settings = chromadb.config.Settings(
                is_persistent=True,
                persist_directory=str(store_dir.absolute()),
                anonymized_telemetry=False,
            )
            return Chroma(
                collection_name=name,
                embedding_function=embeddings,
                client_settings=settings,
                collection_configuration={"hnsw": {"space": CHROMA_HNSW_SPACE}},
            )

        case "qdrant":
            try:
                from langchain_qdrant import QdrantVectorStore
                from qdrant_client import QdrantClient
            except ImportError:
                raise ImportError(
                    "Qdrant backend not installed. "
                    "Install: pip install klea_utils[qdrant]"
                ) from None

            client = QdrantClient(url=location)
            logger.debug(f"Loading Qdrant vector store '{name}' at {location}")
            return QdrantVectorStore(
                client=client,
                collection_name=name,
                embedding=embeddings,
            )

        case "pgvector":
            try:
                from langchain_postgres import PGVector
            except ImportError:
                raise ImportError(
                    "PGVector backend not installed. "
                    "Install: pip install klea_utils[pgvector]"
                ) from None

            logger.debug(
                f"Loading PGVector vector store '{name}' with connection {location}"
            )
            return PGVector(
                collection_name=name,
                embeddings=embeddings,
                connection=location,
            )

        case _:
            raise ValueError(
                f"Unknown vector store scheme '{scheme}'. "
                f"Supported: chroma, qdrant, pgvector"
            )


def drop_collection(store, store_path: str, collection_name: str) -> None:
    """Drop a vector store collection (portable across backends).

    Used by ``store --force`` to replace a collection wholesale, since
    documents within a collection cannot be updated in place portably.
    Chroma and PGVector expose ``delete_collection`` on their LangChain
    wrapper; Qdrant's wrapper does not, so its raw client is used
    instead.

    :param store: Instantiated LangChain vector store
    :param store_path: Store URI (``scheme:location``)
    :param collection_name: Collection name to drop
    :raises ValueError: When the scheme is missing or unknown
    """
    scheme, sep, _ = store_path.partition(":")
    if not sep:
        raise ValueError(
            f"Invalid vector store path '{store_path}': expected format "
            f"'scheme:location' (e.g. 'chroma:/path/to/store')"
        )
    match scheme.lower():
        case "chroma" | "pgvector":
            store.delete_collection()
        case "qdrant":
            store._client.delete_collection(collection_name)
        case _:
            raise ValueError(
                f"Unsupported vector store scheme '{scheme}' for collection "
                f"drop. Supported: chroma, qdrant, pgvector"
            )
