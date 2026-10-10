#!/usr/bin/env python3
"""
Academic paper search across several scholarly APIs.

Peer reviewed sources are preferred.  The general sources (OpenAlex,
Crossref, and Semantic Scholar when ``SEMANTIC_SCHOLAR_API_KEY`` is set) are
queried in round-robin order across calls, like
:class:`klea_utils.biblio.doi.DoiResolver`, so no single API takes every
request; when the primary is rate limited, fails or finds nothing, the next
one is tried.  Life sciences searches try Europe PMC, then PubMed, before the
general sources.  Preprints (arXiv, Europe PMC preprints) are only searched
when asked for, and are listed after the peer reviewed results.

File: klea_utils/mcp/tool_impls/papers/search.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import functools
import itertools
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal, get_args

from klea_utils.mcp.tool_impls.session import SessionLike

from . import arxiv, crossref, europepmc, openalex, pubmed, semantic_scholar
from .http import PaperSourceError
from .record import PaperRecord, truncate_abstract

logger = logging.getLogger(__name__)

Domain = Literal["general", "life-sciences"]
DOMAINS: tuple[str, ...] = get_args(Domain)
DEFAULT_MAX_RESULTS = 10
MAX_RESULTS_LIMIT = 25

SearchFn = Callable[[SessionLike | None, str, int], Awaitable[list[PaperRecord]]]

#: Picks which general source goes first, moving on by one each search so
#: no single API takes every request.
_ROTATION = itertools.count()


def _general_sources() -> list[tuple[str, SearchFn]]:
    """Peer reviewed general sources, primary rotated per call."""
    sources: list[tuple[str, SearchFn]] = [
        ("openalex", openalex.search),
        ("crossref", crossref.search),
    ]
    if semantic_scholar.api_key():
        sources.append(("semantic_scholar", semantic_scholar.search))
    start = next(_ROTATION) % len(sources)
    return sources[start:] + sources[:start]


def _peer_reviewed_sources(domain: str) -> list[tuple[str, SearchFn]]:
    general = _general_sources()
    if domain == "life-sciences":
        return [
            ("europepmc", europepmc.search),
            ("pubmed", pubmed.search),
            *general,
        ]
    return general


def _preprint_sources(domain: str) -> list[tuple[str, SearchFn]]:
    europepmc_preprints = functools.partial(europepmc.search, preprints=True)
    if domain == "life-sciences":
        return [
            ("europepmc_preprints", europepmc_preprints),
            ("arxiv", arxiv.search),
        ]
    return [("arxiv", arxiv.search)]


async def _first_hit(
    session: SessionLike | None,
    query: str,
    limit: int,
    sources: list[tuple[str, SearchFn]],
    failures: list[str],
    answered: list[str],
) -> list[PaperRecord]:
    """Return the results of the first source that finds papers with abstracts.

    Results without any abstract (common for Crossref) are kept only as a
    last resort, in case no later source finds anything better.  Failures
    are appended to *failures* as ``"<source>: <reason>"``; sources that
    responded (even with no results) are appended to *answered*.
    """
    without_abstracts: list[PaperRecord] = []
    for name, search_fn in sources:
        logger.debug(f"trying paper source '{name}' for {query!r}")
        try:
            records = await search_fn(session, query, limit)
        except (PaperSourceError, ValueError, TypeError, KeyError) as exc:
            logger.warning(f"Paper source {name} failed for {query!r}: {exc}")
            failures.append(f"{name}: {exc}")
            continue
        answered.append(name)
        if not records:
            continue
        if any(record.abstract for record in records):
            logger.info(f"Found {len(records)} papers via {name}")
            return records[:limit]
        logger.debug(f"{name} results have no abstracts, trying the next source")
        without_abstracts = without_abstracts or records
    return without_abstracts[:limit]


def _dedupe(records: list[PaperRecord]) -> list[PaperRecord]:
    """Drop repeats (same DOI, or same title when there is no DOI)."""
    seen: set[str] = set()
    unique = []
    for record in records:
        key = (record.doi or record.title or "").lower()
        if key and key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


async def search_papers(
    session: SessionLike | None,
    query: str,
    domain: str = "general",
    preprints: bool = False,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> dict[str, Any]:
    """Search academic papers by keywords.

    :param session: HTTP session to use for the requests.
    :param query: Keywords to search for.
    :param domain: ``"general"`` or ``"life-sciences"``.
    :param preprints: Also search preprints, listed after peer reviewed results.
    :param max_results: Maximum results from the peer reviewed sources, and
        separately from the preprint sources.
    :returns: Dictionary with query, domain, preprints, results, error and
        note.  ``note`` lists sources that failed, if any.
    """
    result: dict[str, Any] = {
        "query": query,
        "domain": domain,
        "preprints": preprints,
        "results": [],
        "error": "",
        "note": "",
    }
    query = query.strip()
    if not query:
        result["error"] = "Query is empty."
        return result
    if domain not in DOMAINS:
        result["error"] = f"Unknown domain {domain!r}; use one of {list(DOMAINS)}."
        return result
    limit = max(1, min(max_results, MAX_RESULTS_LIMIT))

    failures: list[str] = []
    answered: list[str] = []
    records = await _first_hit(
        session, query, limit, _peer_reviewed_sources(domain), failures, answered
    )
    if preprints:
        records += await _first_hit(
            session, query, limit, _preprint_sources(domain), failures, answered
        )
    records = _dedupe(records)

    for record in records:
        record.abstract = truncate_abstract(record.abstract)
    result["results"] = [record.model_dump() for record in records]

    if failures:
        result["note"] = "Some sources failed: " + "; ".join(failures)
    if not records:
        if not answered:
            result["error"] = "No paper source could be reached. " + result["note"]
        else:
            result["note"] = " ".join(
                part
                for part in ("No papers found for this query.", result["note"])
                if part
            )
    return result
