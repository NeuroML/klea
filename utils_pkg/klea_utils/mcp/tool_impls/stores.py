#!/usr/bin/env python3
"""
Domain-aware document store search for Klea MCP tools.

The tool answers a query from the deployment's configured vector and BM25
stores: it resolves the stores configuration with
:func:`klea_utils.stores.utils.resolve_stores_config_path`, builds the
retrievers for the requested domain per call, fuses their results, re-ranks
them and returns a character-budgeted block of reference material.

Framework-agnostic implementation shared across Klea MCP servers.  Apps wrap
:func:`search_stores` in an MCP tool (see
``klea_utils.mcp.server.bundled_tools``).

File: klea_utils/mcp/tool_impls/stores.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from klea_utils.stores.utils import (
    DEFAULT_CROSS_ENCODER_MODEL,
    cross_encoder_rerank,
    load_cross_encoder,
    load_retriever_config,
    resolve_stores_config_path,
    rrf_merge,
    serialize_reference_material,
    truncate_reference_material,
)

if TYPE_CHECKING:
    from langchain_core.documents import Document

    from klea_utils.stores.config import PerDomainConfig, RetrieverConfig

logger = logging.getLogger(__name__)

#: Default number of documents fetched per store.
DEFAULT_TOP_K = 5

#: Largest ``top_k`` a caller may request.
MAX_TOP_K = 50

#: Default character budget for the returned reference material.
DEFAULT_MAX_CHARS = 20000

#: Largest character budget a caller may request.
MAX_MAX_CHARS = 200_000

#: Environment variable naming the cross-encoder used for re-ranking.
CROSS_ENCODER_MODEL_ENV_VAR = "KLEA_CROSS_ENCODER_MODEL"

#: Environment variable naming the embedding model used to query the vector
#: stores.  Query-time embeddings must match the vectors in the store, so the
#: deployment declares the model rather than this module guessing one.
EMBEDDING_MODEL_ENV_VAR = "KLEA_EMBEDDING_MODEL"

#: Mode returning the configured domains instead of searching them.
CATALOGUE_MODE = "catalogue"

#: Enriched search modes: embeddings, keywords, or every store the domain
#: configures (fused with RRF).
SEARCH_MODES = ("hybrid", "vector", "bm25")

#: Every accepted ``mode`` value.
MODES = (CATALOGUE_MODE, *SEARCH_MODES)

#: Mode used when the caller does not choose one.
DEFAULT_MODE = "hybrid"


class StoresSearchError(RuntimeError):
    """A controlled store-search failure.

    Raised for the documented failure cases (no configuration, unknown
    domain or mode, unusable filter, missing embedding model, ...) so
    :func:`search_stores` can turn each into exactly one synthetic error
    result instead of raising out of the tool call.
    """


def _result(
    *,
    query: str = "",
    domain: str = "",
    mode: str = "",
    top_k: int = 0,
    results: list[dict[str, Any]] | None = None,
    content: str = "",
    catalogue: list[dict[str, Any]] | None = None,
    truncated: bool = False,
    error: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Build the standard store-search result dict.

    :param query: The query that was searched for.
    :param domain: The domain that was searched (or asked for).
    :param mode: The mode that was used.
    :param top_k: The effective per-store fetch depth.
    :param results: One summary dict per returned document.
    :param content: Serialized, character-budgeted reference material.
    :param catalogue: The configured domains, when known.
    :param truncated: Whether the character budget dropped documents.
    :param error: Empty on success; a message otherwise.
    :param note: Human-readable notes (degraded re-ranking, truncation, ...).
    :returns: The result dict returned to the MCP wrapper.
    """
    results = results or []
    return {
        "query": query,
        "domain": domain,
        "mode": mode,
        "top_k": top_k,
        "results": results,
        "count": len(results),
        "content": content,
        "catalogue": catalogue or [],
        "truncated": truncated,
        "error": error,
        "note": note,
    }


def search_stores(
    query: str = "",
    domain: str = "",
    mode: str = DEFAULT_MODE,
    top_k: int = DEFAULT_TOP_K,
    rerank: bool = True,
    metadata_filter: dict[str, Any] | None = None,
    max_chars: int = DEFAULT_MAX_CHARS,
    configured: str | None = None,
) -> dict[str, Any]:
    """Search the deployment's document stores for one domain.

    Use when:
    - Answering a question from the deployment's document collections.
    - Looking up material held in a vector store or BM25 corpus.

    Do not use for:
    - Reading a local file (use the read file tool instead).
    - General web pages (use the web search tool instead).

    Example: search_stores(query="sodium channel kinetics", domain="neuro")

    The store catalogue is returned when ``mode`` is ``"catalogue"`` or
    ``domain`` is empty, so a caller can discover the valid domain names
    before searching.  Retrievers are built per call from the stores
    configuration resolved by
    :func:`klea_utils.stores.utils.resolve_stores_config_path`, so an edited
    file is picked up without restarting the server (the embedding model is
    therefore also loaded per call; its weights stay in the Hugging Face
    cache).

    Failure cases, each mapped to exactly one synthetic error result (empty
    ``results``/``content``, populated ``error``, nothing raised):

    - an unknown ``mode``;
    - ``top_k`` or ``max_chars`` outside its bounds;
    - a ``metadata_filter`` that is not a mapping;
    - a stores configuration that is not configured, missing or invalid;
    - an unknown ``domain`` (the catalogue is echoed so the caller retries);
    - an empty ``query`` in a search mode;
    - a domain configuring no store, or none of the requested kind;
    - a filter on a domain that declares no filter fields;
    - vector retrieval without an embedding model configured;
    - an unexpected backend fault while retrieving.

    :param query: Search string; required unless ``mode`` is ``catalogue``
    :param domain: Domain name to search, or empty to list the catalogue
    :param mode: One of ``hybrid``, ``vector``, ``bm25`` or ``catalogue``
    :param top_k: Documents fetched per store (1 to :data:`MAX_TOP_K`)
    :param rerank: Re-rank the fused results with the cross-encoder
    :param metadata_filter: Optional metadata filter in the store filter DSL
        (see :func:`klea_utils.stores.filters.validate_metadata_filter`)
    :param max_chars: Character budget for the returned reference material
    :param configured: Stores-configuration JSON path supplied by the client
        for this request.  ``None`` (the default) keeps the host default
        resolved by :func:`klea_utils.stores.utils.resolve_stores_config_path`
    :returns: Dictionary with query, domain, mode, top_k, results, count,
        content, catalogue, truncated, error and note.
    """
    requested_mode = str(mode or "").strip().lower() or DEFAULT_MODE
    requested_query = (query or "").strip()
    requested_domain = (domain or "").strip()

    try:
        return _search(
            query=requested_query,
            domain=requested_domain,
            mode=requested_mode,
            top_k=top_k,
            rerank=rerank,
            metadata_filter=metadata_filter,
            max_chars=max_chars,
            configured=configured,
        )
    except StoresSearchError as exc:
        logger.debug(f"Store search refused: {exc}")
        return _result(
            query=requested_query,
            domain=requested_domain,
            mode=requested_mode,
            top_k=_safe_int(top_k),
            catalogue=_catalogue_safe(configured),
            error=str(exc),
        )
    except Exception as exc:
        # Backend faults (unreachable server, broken store, ...) are mapped to
        # a single non-halting error result so the model can adapt.
        logger.exception("Store search failed")
        return _result(
            query=requested_query,
            domain=requested_domain,
            mode=requested_mode,
            top_k=_safe_int(top_k),
            catalogue=_catalogue_safe(configured),
            error=f"Store search failed: {exc}",
        )


def render_catalogue(config: RetrieverConfig) -> list[dict[str, Any]]:
    """Render the configured domains as a compact catalogue.

    The catalogue tells a caller which domain names exist, what each covers
    and which filter fields it accepts, so a mismatched request can be
    corrected from the tool result alone.

    :param config: Parsed stores configuration
    :returns: One dict per domain
    """
    catalogue: list[dict[str, Any]] = []
    for name, domain in config.domains.items():
        catalogue.append(
            {
                "domain": name,
                "description": domain.description,
                "vector_stores": [store.name for store in domain.vector_stores],
                "bm25_stores": [store.name for store in domain.bm25_stores],
                "filter_fields": [
                    {"name": field.name, "value_type": field.value_type}
                    for field in domain.filter_fields
                ],
            }
        )
    return catalogue


def _search(
    *,
    query: str,
    domain: str,
    mode: str,
    top_k: int,
    rerank: bool,
    metadata_filter: dict[str, Any] | None,
    max_chars: int,
    configured: str | None = None,
) -> dict[str, Any]:
    """Carry out one store search.

    :returns: The result dict.
    :raises StoresSearchError: For the documented failure cases
    """
    if mode not in MODES:
        raise StoresSearchError(
            f"Unknown mode {mode!r}: expected one of {', '.join(MODES)}"
        )
    depth = _validated_top_k(top_k)
    budget = _validated_max_chars(max_chars)
    if metadata_filter is not None and not isinstance(metadata_filter, dict):
        raise StoresSearchError(
            "metadata_filter must be a mapping of field name to operand"
        )

    config = _load_config(configured)
    catalogue = render_catalogue(config)

    if mode == CATALOGUE_MODE or not domain:
        if mode != CATALOGUE_MODE and not catalogue:
            raise StoresSearchError("No domains are configured in the stores config")
        note = ""
        if mode != CATALOGUE_MODE:
            note = (
                "No domain given: listing the configured domains. Call again "
                "with one of the catalogue 'domain' values."
            )
        return _result(
            query=query,
            domain=domain,
            mode=mode,
            top_k=depth,
            catalogue=catalogue,
            note=note,
        )

    domain_config = config.domains.get(domain)
    if domain_config is None:
        known = ", ".join(sorted(config.domains)) or "none"
        raise StoresSearchError(
            f"Unknown domain {domain!r}: configured domains are {known}"
        )
    if not query:
        raise StoresSearchError("query must not be empty in a search mode")

    _check_domain_mode(domain, domain_config, mode)
    _check_filter_fields(domain, domain_config, metadata_filter)

    wants_vector = mode in ("vector", "hybrid") and bool(domain_config.vector_stores)
    embedding_model = ""
    if wants_vector:
        embedding_model = _embedding_model()
        if not embedding_model:
            raise StoresSearchError(
                "Vector retrieval needs an embedding model: set "
                f"{EMBEDDING_MODEL_ENV_VAR} to the model the stores were built "
                "with"
            )

    # Lazy: the retrieval layer pulls LangChain (and, on setup, the embedding
    # model), so it is imported only when a search actually runs.
    from klea_utils.stores.retrieval.bm25 import BM25RetrieverManager
    from klea_utils.stores.retrieval.vs import VSRetriever

    managers: list[Any] = []
    if wants_vector:
        managers.append(
            VSRetriever(
                config,
                logger,
                embedding_model,
                default_k=depth,
                k_max=depth,
            )
        )
    if mode in ("bm25", "hybrid") and domain_config.bm25_stores:
        managers.append(
            BM25RetrieverManager(config, logger, default_k=depth, k_max=depth)
        )

    result_sets: list[tuple[str, list[tuple[Document, float]]]] = []
    for manager in managers:
        manager.setup()
        manager.load(domain)
        hits = manager.retrieve(domain, query, metadata_filter=metadata_filter)
        if hits:
            result_sets.append((manager.source_label, hits))

    if not result_sets:
        return _result(
            query=query,
            domain=domain,
            mode=mode,
            top_k=depth,
            catalogue=catalogue,
            note="No matching documents were found in the configured stores.",
        )

    if len(result_sets) == 1:
        merged = list(result_sets[0][1])
    else:
        merged = rrf_merge(result_sets)

    notes: list[str] = []
    if rerank and merged:
        try:
            model = load_cross_encoder(_cross_encoder_model())
            merged = cross_encoder_rerank(query, merged, model=model)
        # Re-ranking is best-effort: any failure from the cross-encoder or its
        # model backend must degrade to the fused order (plus a note) instead of
        # failing the whole search, so the broad catch is deliberate.
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"Cross-encoder re-ranking unavailable: {exc}")
            notes.append(
                "Cross-encoder re-ranking was unavailable; the results are in "
                "fused order."
            )

    budgeted = truncate_reference_material({query: merged}, budget)
    selected = budgeted.get(query, [])
    truncated = len(selected) < len(merged)
    if truncated:
        notes.append(
            f"Reference material truncated to {budget} characters: showing "
            f"{len(selected)} of {len(merged)} documents."
        )

    results = [
        {
            "rank": rank,
            "score": float(score),
            "file_name": str(doc.metadata.get("file_name", "")),
            "headings": _headings(doc),
        }
        for rank, (doc, score) in enumerate(selected, 1)
    ]

    return _result(
        query=query,
        domain=domain,
        mode=mode,
        top_k=depth,
        results=results,
        content=serialize_reference_material({query: selected}),
        catalogue=catalogue,
        truncated=truncated,
        note=" ".join(notes),
    )


def _load_config(configured: str | None = None) -> RetrieverConfig:
    """Resolve and load the stores configuration.

    :param configured: Stores-configuration path supplied for this request, or
        ``None`` to use the host default
    :returns: The parsed configuration
    :raises StoresSearchError: When no path is configured, the file is
        missing, or its contents are invalid
    """
    path = resolve_stores_config_path(configured)
    if path is None:
        raise StoresSearchError(
            "No stores configuration is available: no stores JSON file is configured"
        )
    try:
        return load_retriever_config(path)
    except FileNotFoundError as exc:
        raise StoresSearchError(f"Stores configuration not found: {path}") from exc
    except (OSError, ValueError) as exc:
        raise StoresSearchError(f"Invalid stores configuration {path}: {exc}") from exc


def _catalogue_safe(configured: str | None = None) -> list[dict[str, Any]]:
    """Return the catalogue for the current configuration, or ``[]``.

    Used on the error path so a model that named an unknown domain or mode
    can retry with a valid one.  The load is served from the store package's
    mtime cache, so it costs a stat when the configuration is unchanged.

    :param configured: Stores-configuration path supplied for this request, or
        ``None`` to use the host default
    :returns: The rendered catalogue, or an empty list when unavailable
    """
    try:
        return render_catalogue(_load_config(configured))
    except StoresSearchError:
        return []


def _check_domain_mode(domain: str, domain_config: PerDomainConfig, mode: str) -> None:
    """Refuse a mode the domain cannot serve.

    :param domain: Domain name, for the error message
    :param domain_config: The domain's store configuration
    :param mode: The requested search mode
    :raises StoresSearchError: When the domain configures no usable store
    """
    has_vector = bool(domain_config.vector_stores)
    has_bm25 = bool(domain_config.bm25_stores)
    if not has_vector and not has_bm25:
        raise StoresSearchError(f"Domain {domain!r} configures no stores")
    if mode == "vector" and not has_vector:
        raise StoresSearchError(
            f"Domain {domain!r} has no vector stores: use mode 'bm25' or 'hybrid'"
        )
    if mode == "bm25" and not has_bm25:
        raise StoresSearchError(
            f"Domain {domain!r} has no BM25 stores: use mode 'vector' or 'hybrid'"
        )


def _check_filter_fields(
    domain: str,
    domain_config: PerDomainConfig,
    metadata_filter: dict[str, Any] | None,
) -> None:
    """Refuse a filter on a domain that declares no filter fields.

    :param domain: Domain name, for the error message
    :param domain_config: The domain's store configuration
    :param metadata_filter: The requested filter, if any
    :raises StoresSearchError: When a filter is requested but the domain
        declares no filterable metadata fields
    """
    if not metadata_filter:
        return
    if not domain_config.filter_fields:
        raise StoresSearchError(
            f"Domain {domain!r} declares no filter fields: retry without "
            "metadata_filter"
        )


def _validated_top_k(top_k: int) -> int:
    """Validate (and coerce) the requested fetch depth.

    :param top_k: Requested documents per store
    :returns: The validated depth
    :raises StoresSearchError: When it is not an integer in range
    """
    try:
        value = int(top_k)
    except (TypeError, ValueError) as exc:
        raise StoresSearchError(f"top_k must be an integer, got {top_k!r}") from exc
    if value < 1:
        raise StoresSearchError("top_k must be at least 1")
    if value > MAX_TOP_K:
        raise StoresSearchError(f"top_k must be at most {MAX_TOP_K}")
    return value


def _validated_max_chars(max_chars: int) -> int:
    """Validate (and coerce) the requested character budget.

    :param max_chars: Requested character budget
    :returns: The validated budget
    :raises StoresSearchError: When it is not an integer in range
    """
    try:
        value = int(max_chars)
    except (TypeError, ValueError) as exc:
        raise StoresSearchError(
            f"max_chars must be an integer, got {max_chars!r}"
        ) from exc
    if value < 1:
        raise StoresSearchError("max_chars must be at least 1")
    if value > MAX_MAX_CHARS:
        raise StoresSearchError(f"max_chars must be at most {MAX_MAX_CHARS}")
    return value


def _embedding_model() -> str:
    """Resolve the embedding model used to query the vector stores.

    ``KLEA_EMBEDDING_MODEL`` wins.  When it is unset the store package's
    default is used if it exposes one, otherwise ``""`` is returned so the
    caller reports a documented error rather than querying a store with
    mismatched embeddings.

    :returns: The model name, or ``""`` when none is available
    """
    raw = os.environ.get(EMBEDDING_MODEL_ENV_VAR, "").strip()
    if raw:
        return raw
    try:
        import importlib

        module = importlib.import_module("klea_utils.stores.embeddings")
        default_model = module.DEFAULT_EMBEDDING_MODEL
    except (ImportError, AttributeError):
        logger.debug("No package embedding-model default is available")
        return ""
    return str(default_model).strip()


def _cross_encoder_model() -> str:
    """Resolve the cross-encoder model used for re-ranking.

    :returns: ``KLEA_CROSS_ENCODER_MODEL`` when set, else the package default
    """
    raw = os.environ.get(CROSS_ENCODER_MODEL_ENV_VAR, "").strip()
    return raw or DEFAULT_CROSS_ENCODER_MODEL


def _headings(doc: Document) -> list[str]:
    """Return a document's heading path as a list of strings.

    Stores record ``headings`` as a list; a single heading (or a missing
    value) is normalised so the payload shape stays stable.

    :param doc: The retrieved document
    :returns: The heading path, empty when the document has none
    """
    value = doc.metadata.get("headings") or []
    if isinstance(value, str):
        return [value]
    return [str(heading) for heading in value]


def _safe_int(value: Any) -> int:
    """Coerce *value* to an int for an error payload, else ``0``.

    :param value: The raw value
    :returns: The integer, or ``0`` when it cannot be coerced
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
