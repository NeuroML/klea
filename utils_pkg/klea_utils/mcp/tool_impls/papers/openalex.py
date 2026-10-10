#!/usr/bin/env python3
"""
OpenAlex paper search source.

File: klea_utils/mcp/tool_impls/papers/openalex.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os

from klea_utils.biblio.doi import _normalize_openalex
from klea_utils.mcp.tool_impls.session import SessionLike

from .http import PaperSourceError, get_json
from .record import PaperRecord, clean_doi, from_biblio

logger = logging.getLogger(__name__)

OPENALEX_WORKS_URL = "https://api.openalex.org/works"
#: OpenAlex search also returns preprints, datasets etc.; this keeps only
#: journal articles.
PEER_REVIEWED_FILTER = "type:article,primary_location.source.type:journal"


async def search(
    session: SessionLike | None, query: str, limit: int
) -> list[PaperRecord]:
    """Search OpenAlex for peer reviewed journal articles.

    :raises PaperSourceError: when the request fails or is rate limited.
    """
    params = {"search": query, "per-page": limit, "filter": PEER_REVIEWED_FILTER}
    mailto = os.environ.get("KLEA_INGEST_MAILTO")
    if mailto:
        params["mailto"] = mailto
    data = await get_json(session, OPENALEX_WORKS_URL, params=params)
    try:
        works = data.get("results") or []
    except AttributeError as exc:
        raise PaperSourceError(f"Unexpected OpenAlex response: {exc}") from exc

    records = []
    for work in works:
        record = _normalize_openalex(work)
        if not record.title:
            continue
        landing = (work.get("primary_location") or {}).get("landing_page_url")
        records.append(
            from_biblio(
                record,
                url=landing,
                peer_reviewed=True,
                source="openalex",
                doi=clean_doi(work.get("doi")),
            )
        )
    logger.debug(f"OpenAlex returned {len(records)} records for {query!r}")
    return records
