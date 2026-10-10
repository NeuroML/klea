#!/usr/bin/env python3
"""
DOI resolution via Crossref, OpenAlex and Semantic Scholar

See ``devdocs/adr/0027-doi-bibliographic-resolver.md`` and
``devdocs/system/store-create.md``.

File: klea_utils/biblio/doi.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import itertools
import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Self

import httpx
from pydantic import BaseModel

from .regex import DOI_RE

logger = logging.getLogger(__name__)

#: JATS/XML tags found in Crossref abstracts.
_JATS_TAG_RE = re.compile(r"<[^>]+>")

#: Persist the DOI cache at most once per this many newly-resolved DOIs.
#: :meth:`DoiResolver.close` always flushes whatever is left, so a crash
#: between saves loses at most this many fresh resolutions (they are
#: simply re-queried on the next run).
_SAVE_BATCH_SIZE = 25

#: Honest client identity sent to the DOI services (Crossref/OpenAlex/
#: Semantic Scholar).  It is not required to resolve DOIs, but identifies
#: Klea and avoids httpx's default anonymous ``python-httpx/<version>``
#: string; the polite pool is requested separately via the ``mailto``
#: query parameter.
_UA_PREFIX = "klea-ingest/"
_user_agent_cache: str | None = None


def _user_agent() -> str:
    """Return the honest ingest User-Agent, versioned with klea_utils.

    Reads the installed ``klea_utils`` version from package metadata; falls
    back to ``dev`` when metadata is unavailable (e.g. an editable checkout
    without installed distribution metadata).
    """
    global _user_agent_cache
    if _user_agent_cache is None:
        try:
            from importlib.metadata import PackageNotFoundError, version

            version_str = version("klea_utils")
        except PackageNotFoundError:
            version_str = ""
        _user_agent_cache = f"{_UA_PREFIX}{version_str or 'dev'}"
    return _user_agent_cache


class BiblioRecord(BaseModel):
    """Normalised bibliographic record shared across the DOI services."""

    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    journal: str | None = None
    abstract: str | None = None
    doi: str | None = None


def normalize_doi(doi: str) -> str:
    """Normalise a DOI by stripping common URL/prefix wrappers.

    ``https://doi.org/10.x/y``, ``http://dx.doi.org/10.x/y`` and
    ``doi: 10.x/y`` all become ``10.x/y``.  Trailing punctuation is
    removed, and a trailing URL path after the DOI suffix is stripped
    (a valid DOI suffix contains no ``/``, e.g. the path in
    ``10.1073/pnas.2201699120/-/DCSupplemental``).

    :param doi: DOI string, possibly wrapped in a URL or prefix
    :returns: Normalised DOI, or ``""`` when *doi* is empty
    """
    if not doi:
        return ""
    value = str(doi).strip()
    value = re.sub(
        r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", value, flags=re.IGNORECASE
    )
    value = value.rstrip(".,;:)]}")
    # A valid DOI is ``10.<registrant>/<suffix>``; anything after the
    # second slash is a URL path, not part of the DOI.
    parts = value.split("/", 2)
    if len(parts) == 3:
        value = f"{parts[0]}/{parts[1]}"
    return value


def _strip_tags(value: str | None) -> str | None:
    """Strip XML/JATS tags from a string and collapse whitespace."""
    if not value:
        return None
    text = _JATS_TAG_RE.sub(" ", value)
    text = re.sub(r"\s+", " ", text).strip()
    return text or None


def _reconstruct_inverted_index(inverted: dict | None) -> str | None:
    """Rebuild an abstract from OpenAlex's word->positions map."""
    if not inverted:
        return None
    positioned: dict[int, str] = {}
    for word, positions in inverted.items():
        for position in positions:
            positioned[position] = word
    if not positioned:
        return None
    return " ".join(positioned[pos] for pos in sorted(positioned))


def _normalize_crossref(data: dict) -> BiblioRecord:
    """Normalise a Crossref ``/works/{doi}`` response."""
    message = data.get("message") or {}
    titles = message.get("title") or []
    authors = []
    for author in message.get("author") or []:
        given = author.get("given") or ""
        family = author.get("family") or ""
        name = f"{given} {family}".strip() or (author.get("name") or "")
        if name:
            authors.append(name)
    year = None
    issued = message.get("issued") or {}
    date_parts = issued.get("date-parts") or []
    if date_parts and date_parts[0]:
        year = date_parts[0][0]
    container = message.get("container-title") or []
    return BiblioRecord(
        title=(titles[0] if titles else None),
        authors=authors,
        year=year,
        journal=(container[0] if container else None),
        abstract=_strip_tags(message.get("abstract")),
        doi=message.get("DOI") or None,
    )


def _normalize_openalex(data: dict) -> BiblioRecord:
    """Normalise an OpenAlex ``/works/doi:{doi}`` response."""
    authors = []
    for authorship in data.get("authorships") or []:
        author = authorship.get("author") or {}
        name = author.get("display_name")
        if name:
            authors.append(name)
    source = (data.get("primary_location") or {}).get("source") or {}
    raw_doi = data.get("doi")
    return BiblioRecord(
        title=data.get("title"),
        authors=authors,
        year=data.get("publication_year"),
        journal=source.get("display_name"),
        abstract=_reconstruct_inverted_index(data.get("abstract_inverted_index")),
        doi=normalize_doi(str(raw_doi)) if raw_doi else None,
    )


def _normalize_semantic_scholar(data: dict) -> BiblioRecord:
    """Normalise a Semantic Scholar ``/paper/DOI:{doi}`` response."""
    authors = [a.get("name") for a in (data.get("authors") or []) if a.get("name")]
    external = data.get("externalIds") or {}
    return BiblioRecord(
        title=data.get("title"),
        authors=authors,
        year=data.get("year"),
        journal=data.get("venue"),
        abstract=data.get("abstract"),
        doi=external.get("DOI") or None,
    )


class DoiResolver:
    """Resolve DOIs to bibliographic records via three web services.

    Services are queried in round-robin order across calls, so a bulk
    ingestion does not hammer a single API; when the primary is
    rate-limited (HTTP 429) or fails, the other services are tried as a
    fallback.  Successful records are cached to a JSON file on disk, so
    re-ingests never re-query the APIs.

    Polite-pool attribution is sent when ``KLEA_INGEST_MAILTO`` (or the
    ``mailto`` argument) is set -- Crossref and OpenAlex both honour a
    ``mailto`` parameter to raise their rate limits.  Requests also carry an
    honest ``User-Agent: klea-ingest/<version>``.
    """

    #: Services, in round-robin order.
    SERVICE_ORDER = ("crossref", "openalex", "semantic_scholar")

    def __init__(
        self,
        cache_dir: str | Path,
        mailto: str | None = None,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        logger_obj: logging.Logger | None = None,
    ):
        """Initialise the resolver.

        :param cache_dir: Directory to hold the ``doi-cache.json`` file
        :param mailto: Email address for the APIs' polite pool; falls
            back to the ``KLEA_INGEST_MAILTO`` environment variable
        :param timeout: HTTP timeout in seconds
        :param transport: Optional httpx transport (used by tests to
            avoid real network calls)
        :param logger_obj: Logger instance; a module logger is used when
            not given
        """
        self.logger = logger_obj or logger
        self.cache_dir = Path(cache_dir)
        self.mailto = (
            mailto if mailto is not None else os.environ.get("KLEA_INGEST_MAILTO")
        )
        self.timeout = timeout
        self._cycle = itertools.cycle(self.SERVICE_ORDER)
        self._cache_path = self.cache_dir / "doi-cache.json"
        self._cache = self._load_cache()
        # Count of newly-resolved DOIs not yet written to disk; the cache
        # is persisted every ``_SAVE_BATCH_SIZE`` of them and on close(),
        # so a long run does not rewrite the whole file per resolution.
        self._pending_saves = 0
        self._client = httpx.Client(
            timeout=self.timeout,
            transport=transport,
            headers={"User-Agent": _user_agent()},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        """Flush any pending DOI resolutions, then close the HTTP client."""
        if self._pending_saves:
            self._save_cache()
        self._client.close()

    def resolve(self, doi: str) -> BiblioRecord | None:
        """Resolve *doi* to a normalised bibliographic record.

        Returns the cached record immediately when present.  Otherwise
        queries the services in round-robin order, falling back to the
        remaining services when one is rate-limited or fails.  A success
        is cached to disk.  Returns ``None`` when the DOI is invalid or
        no service could resolve it.

        :param doi: DOI string (URL/prefix wrappers are stripped)
        :returns: Normalised record, or ``None``
        """
        normalized = normalize_doi(doi)
        if not normalized or not DOI_RE.fullmatch(normalized):
            self.logger.warning(f"Ignoring invalid DOI: {doi!r}")
            return None
        self.logger.debug(f"normalised {doi = } -> {normalized = }")

        if normalized in self._cache:
            self.logger.debug(f"DOI cache hit: {normalized}")
            return BiblioRecord.model_validate(self._cache[normalized])

        for service in self._service_order():
            self.logger.debug(f"trying DOI service '{service}' for {normalized}")
            record = self._query(service, normalized)
            if record:
                self._cache[normalized] = record.model_dump()
                self._pending_saves += 1
                if self._pending_saves >= _SAVE_BATCH_SIZE:
                    self._save_cache()
                self.logger.info(f"Resolved DOI {normalized} via {service}")
                return record

        self.logger.warning(f"Could not resolve DOI {normalized} from any service")
        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _service_order(self) -> tuple[str, ...]:
        """Return the services with a rotating primary first."""
        primary = next(self._cycle)
        return (primary,) + tuple(s for s in self.SERVICE_ORDER if s != primary)

    def _query(self, service: str, doi: str) -> BiblioRecord | None:
        """Query a single service, returning a record or ``None``."""
        method = getattr(self, f"_query_{service}")
        try:
            return method(doi)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
            self.logger.warning(f"DOI service {service} failed for {doi}: {e}")
            return None

    def _get_json(self, url: str, params: dict | None = None) -> dict | None:
        """GET *url* and return the JSON body, or ``None`` on failure."""
        try:
            response = self._client.get(url, params=params)
        except httpx.HTTPError as e:
            self.logger.warning(f"Request to {url} failed: {e}")
            return None
        self.logger.debug(f"GET {url} -> {response.status_code}")
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            self.logger.warning(
                f"Rate limited by {url} (HTTP 429)"
                f"{f', Retry-After={retry_after}' if retry_after else ''}"
            )
            return None
        if response.status_code != 200:
            self.logger.warning(f"Unexpected status {response.status_code} from {url}")
            return None
        try:
            return response.json()
        except ValueError as e:
            self.logger.warning(f"Invalid JSON from {url}: {e}")
            return None

    def _query_crossref(self, doi: str) -> BiblioRecord | None:
        params = {}
        if self.mailto:
            params["mailto"] = self.mailto
        data = self._get_json(f"https://api.crossref.org/works/{doi}", params=params)
        return self._record_or_none(_normalize_crossref, data)

    def _query_openalex(self, doi: str) -> BiblioRecord | None:
        params = {}
        if self.mailto:
            params["mailto"] = self.mailto
        data = self._get_json(
            f"https://api.openalex.org/works/doi:{doi}", params=params
        )
        return self._record_or_none(_normalize_openalex, data)

    def _query_semantic_scholar(self, doi: str) -> BiblioRecord | None:
        params = {"fields": "title,authors,abstract,year,venue,externalIds"}
        data = self._get_json(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params=params,
        )
        return self._record_or_none(_normalize_semantic_scholar, data)

    @staticmethod
    def _record_or_none(normalizer, data: dict | None) -> BiblioRecord | None:
        """Normalise *data*; require a title to count as a hit."""
        if data is None:
            return None
        record = normalizer(data)
        if record and record.title:
            return record
        return None

    def _load_cache(self) -> dict:
        """Load the on-disk DOI cache, tolerating a missing/corrupt file."""
        if not self._cache_path.is_file():
            return {}
        try:
            with open(self._cache_path) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            self.logger.warning(f"Could not read DOI cache {self._cache_path}: {e}")
            return {}

    def _save_cache(self) -> None:
        """Write the DOI cache to disk, tolerating failures.

        The write is atomic: the cache is dumped to a temp file in the
        cache directory and then ``os.replace``d onto the final
        ``doi-cache.json`` path, so a crash or kill mid-write can never
        leave a torn cache that would wipe every previously-resolved DOI
        (the reader degrades to an empty cache on a corrupt file, forcing
        re-queries).  Called every :data:`_SAVE_BATCH_SIZE` new
        resolutions and once more from :meth:`close` to flush the tail;
        the pending counter is only reset on a successful write.
        """
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            tmp_path: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="w",
                    dir=self.cache_dir,
                    prefix="doi-cache.",
                    suffix=".json.tmp",
                    delete=False,
                    encoding="utf-8",
                ) as f:
                    tmp_path = Path(f.name)
                    # ensure_ascii=False keeps accented author/title characters
                    # as literal UTF-8 (the cache may hold names like "B\u00f3ris").
                    json.dump(self._cache, f, indent=2, ensure_ascii=False)
                    f.write("\n")
                os.replace(tmp_path, self._cache_path)
                self.logger.debug(
                    f"Wrote DOI cache with {len(self._cache)} entries to "
                    f"{self._cache_path}"
                )
            finally:
                # A failed dump/replace must not leave a stray temp file;
                # after a successful os.replace it no longer exists.
                if tmp_path is not None and tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
        except OSError as e:
            self.logger.warning(f"Could not write DOI cache {self._cache_path}: {e}")
            return
        self._pending_saves = 0
