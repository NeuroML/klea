#!/usr/bin/env python3
"""
Retriever store configuration models

File: klea_utils/stores/config.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Any, Literal

from pydantic import BaseModel


class StoreInfo(BaseModel):
    """Information about a single store used by a retriever manager.

    ``default_k``, ``k_max``, and ``k_inc`` configure retrieval depth per
    store.  When left ``None`` they fall back to the global values set on
    the retriever manager, so stores that do not need tuning inherit the
    graph-wide defaults.

    ``loaded_object`` holds the lazily-instantiated retriever object for
    the store (e.g. a LangChain VectorStore or BM25Retriever).
    """

    name: str
    path: str
    default_k: int | None = None
    k_max: int | None = None
    k_inc: int | None = None
    loaded_object: Any | None = None


class VectorStoreInfo(StoreInfo):
    """Information about a single vector store."""


class BM25StoreInfo(StoreInfo):
    """Information about a single BM25 store.

    ``path`` points to the pickled document corpus that the
    ``BM25RetrieverManager`` loads to build its keyword index.
    """


class FilterFieldInfo(BaseModel):
    """Configuration for a single retrievable metadata filter field.

    A deployment declares, per domain, the metadata fields the retrieval
    query generator may filter on.  Each entry describes one field: its
    name (the metadata key stored on the documents), its semantics for
    the LLM, and the operand type it accepts.

    ``value_type`` controls how a bare operand from the LLM is mapped to
    the filter DSL (see
    :func:`klea_utils.stores.filters.normalize_config_filters`):

    - ``"string"`` / ``"int"`` / ``"float"`` --- scalar fields.  A bare
      value becomes ``$eq``; a list of values becomes ``$in``.
    - ``"list"`` --- element-membership fields (e.g. ``tags``).  A bare
      value becomes ``$contains``; several values combine with ``$and``
      (every value must be present).
    """

    name: str
    description: str
    value_type: Literal["string", "int", "float", "list"] = "string"


class PerDomainConfig(BaseModel):
    """Configuration for a single domain."""

    #: Human-readable description of what the domain covers.
    description: str = ""
    vector_stores: list[VectorStoreInfo] = []
    bm25_stores: list[BM25StoreInfo] = []
    #: Retrieval filter fields the query generator may use for this domain.
    filter_fields: list[FilterFieldInfo] = []


class RetrieverConfig(BaseModel):
    """Top-level retriever configuration.

    Holds the per-domain store configuration for all retriever managers
    (vector stores and BM25 stores).
    """

    domains: dict[str, PerDomainConfig]
