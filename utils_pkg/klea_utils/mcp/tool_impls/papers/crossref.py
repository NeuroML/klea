#!/usr/bin/env python3
"""
Crossref paper search source.

File: klea_utils/mcp/tool_impls/papers/crossref.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os

from klea_utils.biblio.doi import _normalize_crossref
from klea_utils.mcp.tool_impls.session import SessionLike

from .http import PaperSourceError, get_json
from .record import PaperRecord, from_biblio, is_preprint

logger = logging.getLogger(__name__)

CROSSREF_WORKS_URL = "https://api.crossref.org/works"
#: Crossref search also returns preprints ("posted-content"), datasets etc.;
#: this keeps journal articles.
PEER_REVIEWED_FILTER = "type:journal-article"


async def search(
    session: SessionLike | None, query: str, limit: int
) -> list[PaperRecord]:
    """Search Crossref for peer reviewed journal articles.

    :raises PaperSourceError: when the request fails or is rate limited.
    """
    params = {"query": query, "rows": limit, "filter": PEER_REVIEWED_FILTER}
    mailto = os.environ.get("KLEA_INGEST_MAILTO")
    if mailto:
        params["mailto"] = mailto
    data = await get_json(session, CROSSREF_WORKS_URL, params=params)
    try:
        items = (data.get("message") or {}).get("items") or []
    except AttributeError as exc:
        raise PaperSourceError(f"Unexpected Crossref response: {exc}") from exc

    records = []
    for item in items:
        # The DOI resolver's normaliser reads a single DOI lookup response,
        # where the work sits under "message".
        record = _normalize_crossref({"message": item})
        if not record.title:
            continue
        # SSRN registers its preprints as journal articles.
        if is_preprint(record):
            logger.debug(f"Dropping Crossref preprint {record.doi}")
            continue
        records.append(
            from_biblio(
                record, url=item.get("URL"), peer_reviewed=True, source="crossref"
            )
        )
    logger.debug(f"Crossref returned {len(records)} records for {query!r}")
    return records
