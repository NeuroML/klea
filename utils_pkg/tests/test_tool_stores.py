#!/usr/bin/env python3
"""
Tests for the bundled ``search_stores`` MCP tool.

The payload contract --- a successful search, an unknown domain and an empty
retrieval --- is exercised against the framework-agnostic implementation in
``klea_utils.mcp.tool_impls.stores``.  The retriever managers are replaced
with inert fakes, so the cases need no embedding model, no downloaded
cross-encoder and no on-disk corpus; the retrieval modules are still
imported for real, so the backend-dependent cases self-skip when the
retrieval backend is not installed.

File: tests/test_tool_stores.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import json
from pathlib import Path

import klea_utils.mcp.tool_impls.stores as stores_impl
import pytest
from klea_utils.mcp.tool_impls.stores import DEFAULT_MODE, search_stores


def _write_config(path: Path, domain: str = "docs") -> Path:
    """Write a stores configuration with one BM25-only *domain*.

    The corpus path is never opened: the retriever managers are faked, so
    only the configuration shape matters here.
    """
    path.write_text(
        json.dumps(
            {
                "domains": {
                    domain: {
                        "description": f"documents about {domain}",
                        "bm25_stores": [
                            {"name": f"{domain}-bm25", "path": "/tmp/corpus.pkl"}
                        ],
                    }
                }
            }
        )
    )
    return path


def _documents() -> list[tuple[object, float]]:
    """Return one ``(document, score)`` pair as a retriever would."""
    document_class = pytest.importorskip("langchain_core.documents").Document
    return [
        (
            document_class(
                page_content="Sodium channels open on depolarisation.",
                metadata={"file_name": "paper.pdf", "headings": ["Results"]},
            ),
            0.87,
        )
    ]


def _patch_retrievers(monkeypatch, hits: list[tuple[object, float]]) -> None:
    """Replace the retriever managers with inert fakes returning *hits*.

    The retrieval modules are imported for real, so the test self-skips when
    the backend is absent, exactly like the other retrieval tests.

    :param monkeypatch: The active monkeypatch fixture
    :param hits: The ``(document, score)`` pairs the fake returns
    """
    bm25_module = pytest.importorskip("klea_utils.stores.retrieval.bm25")
    vs_module = pytest.importorskip("klea_utils.stores.retrieval.vs")

    class _FakeRetriever:
        """Minimal stand-in for a ``BaseKleaRetriever`` manager."""

        source_label = "vector store"

        def __init__(self, *args, **kwargs) -> None:
            self.init_args = args
            self.init_kwargs = kwargs
            self.domains: list[str] = []

        def setup(self) -> None:
            self.ready = True

        def load(self, domain_name: str) -> None:
            self.domains.append(domain_name)

        def retrieve(self, domain_name, query, metadata_filter=None, k_values=None):
            return list(hits)

    monkeypatch.setattr(bm25_module, "BM25RetrieverManager", _FakeRetriever)
    monkeypatch.setattr(vs_module, "VSRetriever", _FakeRetriever)


def test_success_returns_documented_payload(tmp_path, monkeypatch):
    """A successful search returns the documented result dict."""
    config = _write_config(tmp_path / "stores.json")
    monkeypatch.setattr(
        stores_impl, "resolve_stores_config_path", lambda configured=None: config
    )
    _patch_retrievers(monkeypatch, _documents())

    result = search_stores(
        query="sodium channel kinetics",
        domain="docs",
        mode="bm25",
        top_k=3,
        rerank=False,
    )

    assert result["error"] == ""
    assert result["query"] == "sodium channel kinetics"
    assert result["domain"] == "docs"
    assert result["mode"] == "bm25"
    assert result["top_k"] == 3
    assert result["count"] == 1
    assert len(result["results"]) == 1

    entry = result["results"][0]
    assert entry["rank"] == 1
    assert entry["file_name"] == "paper.pdf"
    assert isinstance(entry["score"], float)

    assert "Sodium channels open on depolarisation." in result["content"]
    assert "paper.pdf" in result["content"]
    assert result["truncated"] is False
    assert result["note"] == ""

    # The catalogue is echoed so the caller can see the domain it searched.
    assert [item["domain"] for item in result["catalogue"]] == ["docs"]
    assert result["catalogue"][0]["description"] == "documents about docs"


def test_unknown_domain_returns_documented_error(tmp_path, monkeypatch):
    """An unknown domain is reported with the configured domains listed."""
    config = _write_config(tmp_path / "stores.json")
    monkeypatch.setattr(
        stores_impl, "resolve_stores_config_path", lambda configured=None: config
    )

    result = search_stores(query="anything", domain="not-a-domain")

    assert result["error"]
    assert "Unknown domain" in result["error"]
    assert "docs" in result["error"]
    assert result["mode"] == DEFAULT_MODE
    assert result["results"] == []
    assert result["content"] == ""
    # The catalogue is echoed so the caller can retry with a valid domain.
    assert [item["domain"] for item in result["catalogue"]] == ["docs"]


def test_empty_retrieval_returns_documented_outcome(tmp_path, monkeypatch):
    """An empty retrieval is a success carrying a note, not an error."""
    config = _write_config(tmp_path / "stores.json")
    monkeypatch.setattr(
        stores_impl, "resolve_stores_config_path", lambda configured=None: config
    )
    _patch_retrievers(monkeypatch, [])

    result = search_stores(
        query="nothing matches this", domain="docs", mode="bm25", rerank=False
    )

    assert result["error"] == ""
    assert result["results"] == []
    assert result["count"] == 0
    assert result["content"] == ""
    assert result["truncated"] is False
    assert "No matching documents" in result["note"]


def test_no_configuration_returns_documented_error():
    """Without a configured stores file the tool fails closed with a message."""
    result = search_stores(query="anything", domain="docs")

    assert "No stores configuration" in result["error"]
    assert result["catalogue"] == []


def test_wrapper_is_admitted_under_read_only():
    """The bundled wrapper is annotated read-only and passes the gate."""
    pytest.importorskip("fastmcp")
    from klea_utils.mcp.access import tool_permits
    from klea_utils.mcp.server.bundled_tools import BUNDLED_TAG, STORES_TAG
    from klea_utils.mcp.server.bundled_tools import search_stores as wrapper

    info = wrapper._tool_meta

    assert info.read_only is True
    assert not info.destructive
    assert tool_permits(info.read_only, info.destructive, "read_only") is True
    assert tool_permits(info.read_only, info.destructive, "full") is True
    assert BUNDLED_TAG in (info.tags or set())
    assert STORES_TAG in (info.tags or set())
