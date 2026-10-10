#!/usr/bin/env python3
"""
Test DOI resolution via Crossref, OpenAlex and Semantic Scholar.

File: tests/test_biblio_doi.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging

import httpx
from klea_utils.biblio.doi import (
    DoiResolver,
    _normalize_crossref,
    _normalize_openalex,
    _normalize_semantic_scholar,
    _user_agent,
    normalize_doi,
)

logger = logging.getLogger(__name__)

CROSSREF_JSON = {
    "message": {
        "DOI": "10.1234/abc.5678",
        "title": ["A Crossref Sample Paper"],
        "author": [
            {"given": "Jane", "family": "Doe"},
            {"given": "John", "family": "Smith"},
        ],
        "issued": {"date-parts": [[2024, 5, 1]]},
        "container-title": ["Journal of Samples"],
        "abstract": "<jats:p>This is the <jats:italic>abstract</jats:italic> text.</jats:p>",
    }
}

OPENALEX_JSON = {
    "title": "An OpenAlex Sample Paper",
    "authorships": [
        {"author": {"display_name": "Jane Doe"}},
        {"author": {"display_name": "John Smith"}},
    ],
    "publication_year": 2023,
    "primary_location": {"source": {"display_name": "Journal of Open Samples"}},
    "abstract_inverted_index": {"This": [0], "is": [1], "an": [2], "abstract": [3]},
    "doi": "https://doi.org/10.2345/def.6789",
}

S2_JSON = {
    "title": "A Semantic Scholar Sample Paper",
    "authors": [{"name": "Jane Doe"}, {"name": "John Smith"}],
    "year": 2022,
    "venue": "Journal of Semantic Samples",
    "abstract": "Plain abstract text.",
    "externalIds": {"DOI": "10.3456/ghi.7890"},
}


def _handler_for(route):
    """Build a MockTransport handler that routes by service URL."""

    def handler(request):
        url = str(request.url)
        if "api.crossref.org" in url:
            return route("crossref", request)
        if "api.openalex.org" in url:
            return route("openalex", request)
        if "api.semanticscholar.org" in url:
            return route("semantic_scholar", request)
        return httpx.Response(404)

    return handler


def _make_resolver(cache_dir, handler):
    return DoiResolver(cache_dir=str(cache_dir), transport=httpx.MockTransport(handler))


def test_normalize_doi_variants():
    """URL/prefix wrappers around a DOI are stripped."""
    for wrapped, expected in [
        ("10.1234/abc.5678", "10.1234/abc.5678"),
        ("https://doi.org/10.1234/abc.5678", "10.1234/abc.5678"),
        ("http://dx.doi.org/10.1234/abc.5678", "10.1234/abc.5678"),
        ("DOI: 10.1234/abc.5678", "10.1234/abc.5678"),
        ("doi:10.1234/abc.5678.", "10.1234/abc.5678"),
    ]:
        assert normalize_doi(wrapped) == expected, wrapped


def test_normalize_doi_strips_trailing_path():
    """A trailing URL path after the DOI suffix is dropped."""
    assert normalize_doi("10.1073/pnas.2201699120/-/DCSupplemental") == (
        "10.1073/pnas.2201699120"
    )
    assert normalize_doi("10.1098/rstb.2017.0379/254766/rstb.2017.0379.pdf") == (
        "10.1098/rstb.2017.0379"
    )


def test_normalize_crossref():
    """Crossref responses normalise, stripping JATS tags from the abstract."""
    record = _normalize_crossref(CROSSREF_JSON)
    logger.info(f"crossref record: {record}")
    assert record.title == "A Crossref Sample Paper"
    assert record.authors == ["Jane Doe", "John Smith"]
    assert record.year == 2024
    assert record.journal == "Journal of Samples"
    assert record.abstract == "This is the abstract text."
    assert record.doi == "10.1234/abc.5678"


def test_normalize_openalex():
    """OpenAlex responses normalise, reconstructing the abstract index."""
    record = _normalize_openalex(OPENALEX_JSON)
    logger.info(f"openalex record: {record}")
    assert record.title == "An OpenAlex Sample Paper"
    assert record.authors == ["Jane Doe", "John Smith"]
    assert record.year == 2023
    assert record.journal == "Journal of Open Samples"
    assert record.abstract == "This is an abstract"
    assert record.doi == "10.2345/def.6789"


def test_normalize_semantic_scholar():
    """Semantic Scholar responses normalise."""
    record = _normalize_semantic_scholar(S2_JSON)
    logger.info(f"s2 record: {record}")
    assert record.title == "A Semantic Scholar Sample Paper"
    assert record.authors == ["Jane Doe", "John Smith"]
    assert record.year == 2022
    assert record.journal == "Journal of Semantic Samples"
    assert record.abstract == "Plain abstract text."
    assert record.doi == "10.3456/ghi.7890"


def test_resolve_round_robin_rotates_primary(tmp_path):
    """The primary service rotates across calls, spreading API load."""

    def route(service, request):
        if service == "crossref":
            return httpx.Response(200, json=CROSSREF_JSON)
        if service == "openalex":
            return httpx.Response(200, json=OPENALEX_JSON)
        return httpx.Response(200, json=S2_JSON)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        first = resolver.resolve("10.1234/abc.5678")
        second = resolver.resolve("10.2345/def.6789")
        third = resolver.resolve("10.3456/ghi.7890")
    finally:
        resolver.close()

    assert first.title == "A Crossref Sample Paper"
    assert second.title == "An OpenAlex Sample Paper"
    assert third.title == "A Semantic Scholar Sample Paper"


def test_resolve_falls_back_on_429(tmp_path):
    """A rate-limited primary falls back to the next service."""

    def route(service, request):
        if service == "crossref":
            return httpx.Response(429, headers={"Retry-After": "5"})
        return httpx.Response(200, json=OPENALEX_JSON)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        record = resolver.resolve("10.2345/def.6789")
    finally:
        resolver.close()

    assert record is not None
    assert record.title == "An OpenAlex Sample Paper"


def test_resolve_all_services_fail(tmp_path):
    """When every service is rate-limited, resolution returns None."""

    def route(service, request):
        return httpx.Response(429)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        assert resolver.resolve("10.2345/def.6789") is None
    finally:
        resolver.close()


def test_resolver_sends_honest_user_agent(tmp_path):
    """Requests identify Klea with a versioned honest User-Agent."""
    seen = {}

    def route(service, request):
        seen["user_agent"] = request.headers.get("user-agent")
        return httpx.Response(200, json=CROSSREF_JSON)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        resolver.resolve("10.1234/abc.5678")
    finally:
        resolver.close()

    assert seen["user_agent"] == _user_agent()
    assert seen["user_agent"].startswith("klea-ingest/")


def test_resolve_cache_hit_skips_network(tmp_path):
    """A cached DOI is served without any network request."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    cached = {
        "10.1000/xyz.123": {
            "title": "Cached Paper",
            "authors": ["Jane Doe"],
            "year": 2020,
            "journal": "Cached Venue",
            "abstract": None,
            "doi": "10.1000/xyz.123",
        }
    }
    (cache_dir / "doi-cache.json").write_text(json.dumps(cached))

    def route(service, request):
        raise AssertionError(f"network should not be hit for {service}")

    resolver = _make_resolver(cache_dir, _handler_for(route))
    try:
        record = resolver.resolve("10.1000/xyz.123")
    finally:
        resolver.close()

    assert record.title == "Cached Paper"
    assert record.authors == ["Jane Doe"]
    assert record.year == 2020


def test_resolve_writes_cache(tmp_path):
    """A successful resolution is persisted to the cache file."""

    def route(service, request):
        return httpx.Response(200, json=CROSSREF_JSON)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        resolver.resolve("10.1234/abc.5678")
    finally:
        resolver.close()

    cache_file = tmp_path / "doi-cache.json"
    assert cache_file.is_file()
    cache = json.loads(cache_file.read_text())
    assert cache["10.1234/abc.5678"]["title"] == "A Crossref Sample Paper"


def test_cache_written_as_literal_utf8(tmp_path):
    """Accented author names are written literally, not \\u-escaped."""
    crossref_json = {
        "message": {
            "DOI": "10.7554/elife.95135",
            "title": ["A Sample Paper"],
            "author": [{"given": "B\u00f3ris", "family": "Marin"}],
            "issued": {"date-parts": [[2025, 1, 1]]},
            "container-title": ["Journal of Samples"],
        }
    }

    def route(service, request):
        return httpx.Response(200, json=crossref_json)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        resolver.resolve("10.7554/elife.95135")
    finally:
        resolver.close()

    text = (tmp_path / "doi-cache.json").read_text()
    assert "B\u00f3ris Marin" in text
    assert "\\u00f3" not in text
    cache = json.loads(text)
    assert cache["10.7554/elife.95135"]["authors"] == ["B\u00f3ris Marin"]


def test_cache_save_is_atomic_no_tmp_left(tmp_path):
    """A successful resolution leaves a valid cache and no temp files."""

    def route(service, request):
        return httpx.Response(200, json=CROSSREF_JSON)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        resolver.resolve("10.1234/abc.5678")
    finally:
        resolver.close()

    cache_file = tmp_path / "doi-cache.json"
    assert cache_file.is_file()
    assert list(tmp_path.glob("*.json.tmp")) == []
    cache = json.loads(cache_file.read_text())
    assert cache["10.1234/abc.5678"]["title"] == "A Crossref Sample Paper"


def test_cache_save_batches_and_flushes_on_close(tmp_path, monkeypatch):
    """Writes happen every _SAVE_BATCH_SIZE resolutions and on close()."""
    monkeypatch.setattr("klea_utils.biblio.doi._SAVE_BATCH_SIZE", 2)

    def route(service, request):
        return httpx.Response(200, json=CROSSREF_JSON)

    resolver = _make_resolver(tmp_path, _handler_for(route))
    cache_file = tmp_path / "doi-cache.json"

    try:
        # First resolution is buffered (threshold of 2 not reached).
        resolver.resolve("10.1234/abc.5678")
        assert not cache_file.exists()
        # Second resolution reaches the threshold: mid-run write.
        resolver.resolve("10.2345/def.6789")
        assert cache_file.is_file()
        cache = json.loads(cache_file.read_text())
        assert set(cache) == {"10.1234/abc.5678", "10.2345/def.6789"}
    finally:
        resolver.close()


def test_resolve_invalid_doi_skips_network(tmp_path):
    """Invalid DOI strings never trigger a network request."""

    def route(service, request):
        raise AssertionError("network should not be hit")

    resolver = _make_resolver(tmp_path, _handler_for(route))
    try:
        assert resolver.resolve("not a doi") is None
        assert resolver.resolve("") is None
        # registrant prefix without a suffix is not a resolvable DOI
        assert resolver.resolve("https://doi.org/10.1234") is None
    finally:
        resolver.close()


if __name__ == "__main__":
    import pytest

    pytest.main()
