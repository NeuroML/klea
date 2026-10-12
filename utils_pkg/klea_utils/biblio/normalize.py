#!/usr/bin/env python3
"""
Normalisers for bibliographic API responses and DOI strings.

The Crossref, OpenAlex and Semantic Scholar normalisers map a service
response to a :class:`~klea_utils.biblio.models.BiblioRecord`.  They are
shared by the DOI resolver (:mod:`klea_utils.biblio.doi`) and the academic
paper search tool (:mod:`klea_utils.mcp.tool_impls.papers`), so they live
here rather than in either consumer.

File: klea_utils/biblio/normalize.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import re

from .models import BiblioRecord

#: JATS/XML tags found in Crossref abstracts.
_JATS_TAG_RE = re.compile(r"<[^>]+>")


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


def strip_tags(value: str | None) -> str | None:
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


def normalize_crossref(data: dict) -> BiblioRecord:
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
        abstract=strip_tags(message.get("abstract")),
        doi=message.get("DOI") or None,
    )


def normalize_openalex(data: dict) -> BiblioRecord:
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


def normalize_semantic_scholar(data: dict) -> BiblioRecord:
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
