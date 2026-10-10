#!/usr/bin/env python3
"""
Europe PMC paper search source (life sciences).

Europe PMC indexes PubMed (``SRC:MED``) and preprint servers such as bioRxiv,
medRxiv and Research Square (``SRC:PPR``), so one free, keyless API covers
both peer reviewed papers and preprints.

File: klea_utils/mcp/tool_impls/papers/europepmc.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.biblio.doi import _strip_tags
from klea_utils.mcp.tool_impls.session import SessionLike

from .http import PaperSourceError, get_json
from .record import PaperRecord, clean_doi

logger = logging.getLogger(__name__)

EUROPEPMC_SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"


def _authors(result: dict) -> list[str]:
    authors = (result.get("authorList") or {}).get("author") or []
    names = [a.get("fullName") for a in authors if a.get("fullName")]
    if names:
        return names
    author_string = (result.get("authorString") or "").rstrip(".")
    return [name.strip() for name in author_string.split(",") if name.strip()]


def _year(result: dict) -> int | None:
    try:
        return int(result.get("pubYear"))
    except (TypeError, ValueError):
        return None


def _venue(result: dict, preprints: bool) -> str | None:
    if preprints:
        return (result.get("bookOrReportDetails") or {}).get("publisher")
    journal = (result.get("journalInfo") or {}).get("journal") or {}
    return journal.get("title")


def _normalize(result: dict, preprints: bool) -> PaperRecord:
    doi = clean_doi(result.get("doi"))
    url = (
        f"https://doi.org/{doi}"
        if doi
        else f"https://europepmc.org/article/{result.get('source')}/{result.get('id')}"
    )
    return PaperRecord(
        title=result.get("title"),
        authors=_authors(result),
        year=_year(result),
        journal=_venue(result, preprints),
        abstract=_strip_tags(result.get("abstractText")),
        doi=doi,
        url=url,
        peer_reviewed=not preprints,
        source="europepmc",
    )


async def search(
    session: SessionLike | None, query: str, limit: int, preprints: bool = False
) -> list[PaperRecord]:
    """Search Europe PMC for PubMed papers, or preprints when *preprints*.

    :raises PaperSourceError: when the request fails or is rate limited.
    """
    source = "PPR" if preprints else "MED"
    params = {
        "query": f"({query}) AND SRC:{source}",
        "format": "json",
        "pageSize": limit,
        # "core" includes abstracts and the full author list.
        "resultType": "core",
    }
    data = await get_json(session, EUROPEPMC_SEARCH_URL, params=params)
    try:
        results = (data.get("resultList") or {}).get("result") or []
    except AttributeError as exc:
        raise PaperSourceError(f"Unexpected Europe PMC response: {exc}") from exc

    records = [_normalize(r, preprints) for r in results if r.get("title")]
    logger.debug(f"Europe PMC ({source}) returned {len(records)} records for {query!r}")
    return records
