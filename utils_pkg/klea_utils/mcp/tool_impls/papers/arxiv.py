#!/usr/bin/env python3
"""
arXiv paper search source (general preprints).

File: klea_utils/mcp/tool_impls/papers/arxiv.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import re
import xml.etree.ElementTree as ET

from klea_utils.mcp.tool_impls.session import SessionLike

from .http import PaperSourceError, Throttle, get_text
from .record import PaperRecord, clean_doi

logger = logging.getLogger(__name__)

ARXIV_QUERY_URL = "https://export.arxiv.org/api/query"
_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV = "{http://arxiv.org/schemas/atom}"

#: arXiv asks for at most one request every 3 seconds and blocks clients
#: that go faster for several minutes.
_THROTTLE = Throttle(min_interval=3.0)


def _text(element: ET.Element, tag: str) -> str | None:
    child = element.find(tag)
    if child is None or not child.text:
        return None
    return re.sub(r"\s+", " ", child.text).strip() or None


def _normalize(entry: ET.Element) -> PaperRecord:
    published = _text(entry, f"{_ATOM}published") or ""
    year = int(published[:4]) if published[:4].isdigit() else None
    authors = [
        name
        for author in entry.findall(f"{_ATOM}author")
        if (name := _text(author, f"{_ATOM}name"))
    ]
    page = next(
        (
            link.get("href")
            for link in entry.findall(f"{_ATOM}link")
            if link.get("rel") == "alternate" and link.get("href")
        ),
        None,
    )
    return PaperRecord(
        title=_text(entry, f"{_ATOM}title"),
        authors=authors,
        year=year,
        journal="arXiv",
        abstract=_text(entry, f"{_ATOM}summary"),
        doi=clean_doi(_text(entry, f"{_ARXIV}doi")),
        url=page or _text(entry, f"{_ATOM}id"),
        peer_reviewed=False,
        source="arxiv",
    )


async def search(
    session: SessionLike | None, query: str, limit: int
) -> list[PaperRecord]:
    """Search arXiv preprints.

    :raises PaperSourceError: when the request fails, is rate limited, or
        the body is not valid Atom XML.
    """
    terms = re.findall(r"[\w-]+", query)
    if not terms:
        raise PaperSourceError(f"No searchable words in query {query!r}")
    params = {
        "search_query": " AND ".join(f"all:{term}" for term in terms),
        "max_results": limit,
        "sortBy": "relevance",
    }
    async with _THROTTLE:
        body = await get_text(session, ARXIV_QUERY_URL, params=params)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PaperSourceError(f"Invalid XML from arXiv: {exc}") from exc

    records = [
        record
        for entry in root.findall(f"{_ATOM}entry")
        if (record := _normalize(entry)).title
    ]
    logger.debug(f"arXiv returned {len(records)} records for {query!r}")
    return records
