#!/usr/bin/env python3
"""
Semantic Scholar paper search source.

The search endpoint answers HTTP 429 to nearly every request without an API
key, so this source is only used when ``SEMANTIC_SCHOLAR_API_KEY`` is set.

File: klea_utils/mcp/tool_impls/papers/semantic_scholar.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os

from klea_utils.biblio.doi import _normalize_semantic_scholar
from klea_utils.mcp.tool_impls.session import SessionLike

from .http import PaperSourceError, Throttle, get_json
from .record import PaperRecord, from_biblio, is_preprint

logger = logging.getLogger(__name__)

SEMANTIC_SCHOLAR_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
API_KEY_ENV_VAR = "SEMANTIC_SCHOLAR_API_KEY"
FIELDS = "title,authors,abstract,year,venue,externalIds,url"

#: A free key is meant to allow one request per second, but with a 1 second
#: gap every other request still got a 429.  A 2 second gap helps, though the
#: odd 429 still happens; the search then moves on to the next source.
_THROTTLE = Throttle(min_interval=2.0)


def api_key() -> str | None:
    """Return the Semantic Scholar API key, or ``None`` when unset."""
    return os.environ.get(API_KEY_ENV_VAR) or None


async def search(
    session: SessionLike | None, query: str, limit: int
) -> list[PaperRecord]:
    """Search Semantic Scholar for peer reviewed journal articles.

    :raises PaperSourceError: when no key is set, or the request fails or is
        rate limited.
    """
    key = api_key()
    if key is None:
        raise PaperSourceError(f"{API_KEY_ENV_VAR} is not set")
    params = {
        "query": query,
        # Ask for extra so dropping preprints still leaves enough results.
        "limit": min(limit * 2, 100),
        "fields": FIELDS,
        "publicationTypes": "JournalArticle",
    }
    async with _THROTTLE:
        data = await get_json(
            session,
            SEMANTIC_SCHOLAR_SEARCH_URL,
            params=params,
            headers={"x-api-key": key},
        )
    try:
        papers = data.get("data") or []
    except AttributeError as exc:
        raise PaperSourceError(f"Unexpected Semantic Scholar response: {exc}") from exc

    records = []
    for paper in papers:
        record = _normalize_semantic_scholar(paper)
        if not record.title:
            continue
        paper_record = from_biblio(
            record,
            url=paper.get("url"),
            peer_reviewed=True,
            source="semantic_scholar",
        )
        # publicationTypes=JournalArticle still lets some preprints through,
        # with a preprint server as the venue.
        if is_preprint(paper_record):
            logger.debug(f"Dropping Semantic Scholar preprint {paper_record.doi}")
            continue
        records.append(paper_record)
    logger.debug(f"Semantic Scholar returned {len(records)} records for {query!r}")
    return records[:limit]
