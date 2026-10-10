#!/usr/bin/env python3
"""
PubMed paper search source (life sciences), via NCBI E-utilities.

Used as the fallback for Europe PMC: ``esearch`` finds the PubMed IDs, then
``efetch`` returns the records (with abstracts) as XML.

File: klea_utils/mcp/tool_impls/papers/pubmed.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import os
import re
import xml.etree.ElementTree as ET

from klea_utils.mcp.tool_impls.session import SessionLike

from .http import PaperSourceError, Throttle, get_json, get_text
from .record import PaperRecord, clean_doi

logger = logging.getLogger(__name__)

PUBMED_ESEARCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi"
PUBMED_EFETCH_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi"
#: PubMed also indexes some preprints; this keeps only peer reviewed records.
PEER_REVIEWED_FILTER = "NOT preprint[pt]"

#: NCBI allows at most 3 requests per second without an API key.
_THROTTLE = Throttle(min_interval=0.34)


def _text(element: ET.Element | None) -> str | None:
    """Text of *element* including inline markup such as ``<i>``."""
    if element is None:
        return None
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip() or None


def _authors(article: ET.Element) -> list[str]:
    names = []
    for author in article.findall("AuthorList/Author"):
        collective = _text(author.find("CollectiveName"))
        parts = [
            _text(author.find("ForeName")),
            _text(author.find("LastName")),
        ]
        name = " ".join(part for part in parts if part) or collective
        if name:
            names.append(name)
    return names


def _year(article: ET.Element) -> int | None:
    pub_date = article.find("Journal/JournalIssue/PubDate")
    if pub_date is None:
        return None
    # Some records only have a free text MedlineDate, e.g. "1998 Dec-1999 Jan".
    raw = _text(pub_date.find("Year")) or _text(pub_date.find("MedlineDate")) or ""
    match = re.match(r"\d{4}", raw)
    return int(match.group()) if match else None


def _abstract(article: ET.Element) -> str | None:
    parts = []
    for section in article.findall("Abstract/AbstractText"):
        text = _text(section)
        if not text:
            continue
        label = section.get("Label")
        parts.append(f"{label}: {text}" if label else text)
    return " ".join(parts) or None


def _doi(entry: ET.Element, article: ET.Element) -> str | None:
    for article_id in entry.findall("PubmedData/ArticleIdList/ArticleId"):
        if article_id.get("IdType") == "doi":
            return clean_doi(_text(article_id))
    for location in article.findall("ELocationID"):
        if location.get("EIdType") == "doi":
            return clean_doi(_text(location))
    return None


def _normalize(entry: ET.Element) -> PaperRecord | None:
    citation = entry.find("MedlineCitation")
    article = citation.find("Article") if citation is not None else None
    if article is None:
        return None
    pmid = _text(citation.find("PMID"))
    doi = _doi(entry, article)
    url = f"https://doi.org/{doi}" if doi else None
    if not url and pmid:
        url = f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/"
    return PaperRecord(
        title=_text(article.find("ArticleTitle")),
        authors=_authors(article),
        year=_year(article),
        journal=_text(article.find("Journal/Title")),
        abstract=_abstract(article),
        doi=doi,
        url=url,
        peer_reviewed=True,
        source="pubmed",
    )


def _common_params() -> dict[str, str]:
    # NCBI asks clients to identify themselves with a tool name and an email.
    params = {"db": "pubmed", "tool": "klea"}
    email = os.environ.get("KLEA_INGEST_MAILTO")
    if email:
        params["email"] = email
    return params


async def search(
    session: SessionLike | None, query: str, limit: int
) -> list[PaperRecord]:
    """Search PubMed for peer reviewed papers.

    :raises PaperSourceError: when a request fails, is rate limited, or
        returns an unreadable body.
    """
    search_params = {
        **_common_params(),
        "term": f"({query}) {PEER_REVIEWED_FILTER}",
        "retmax": limit,
        "retmode": "json",
        "sort": "relevance",
    }
    async with _THROTTLE:
        data = await get_json(session, PUBMED_ESEARCH_URL, params=search_params)
    try:
        result = data["esearchresult"]
        ids = result.get("idlist") or []
    except (KeyError, TypeError, AttributeError) as exc:
        raise PaperSourceError(f"Unexpected PubMed search response: {exc}") from exc
    if result.get("ERROR"):
        raise PaperSourceError(f"PubMed search error: {result['ERROR']}")
    if not ids:
        logger.debug(f"PubMed returned no records for {query!r}")
        return []

    fetch_params = {**_common_params(), "id": ",".join(ids), "retmode": "xml"}
    async with _THROTTLE:
        body = await get_text(session, PUBMED_EFETCH_URL, params=fetch_params)
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise PaperSourceError(f"Invalid XML from PubMed: {exc}") from exc

    by_pmid = {}
    for entry in root.findall("PubmedArticle"):
        record = _normalize(entry)
        pmid = _text(entry.find("MedlineCitation/PMID"))
        if record and record.title and pmid:
            by_pmid[pmid] = record
    # efetch does not promise to keep the relevance order of esearch.
    records = [by_pmid[pmid] for pmid in ids if pmid in by_pmid]
    logger.debug(f"PubMed returned {len(records)} records for {query!r}")
    return records
