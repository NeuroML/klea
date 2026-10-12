#!/usr/bin/env python3
"""
Bibliographic metadata extraction cascade

File: klea_utils/biblio/extract.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import re
from pathlib import Path
from typing import Protocol

from .docling import extract_docling_structured, extract_layout_region
from .models import BiblioRecord
from .normalize import normalize_doi
from .pdf import extract_pdf_info
from .regex import _scan_dois, extract_regex_metadata

logger = logging.getLogger(__name__)


class Resolver(Protocol):
    """Protocol for objects that can resolve a DOI to a record.

    :class:`~klea_utils.biblio.doi.DoiResolver` implements this; tests
    and other callers may substitute any object with a compatible
    ``resolve`` method.
    """

    def resolve(self, doi: str) -> BiblioRecord | None:
        """Resolve *doi* to a record, or ``None`` on failure."""


def extract_metadata(
    dl_doc,
    file_path: str,
    pdf_path: str | None = None,
    resolver: Resolver | None = None,
) -> dict:
    """Extract bibliographic metadata for a converted document.

    The full-cascade entry point, used when a Docling document is
    available (fresh conversion).  Tiers are applied in **precedence
    order, most authoritative first**; each tier only fills fields the
    tiers above it have not already set (gap-fill), so a resolved record
    always wins:

    1. ``doi-service`` -- a DOI discovered by any tier below is resolved
       via Crossref/OpenAlex/Semantic Scholar (round-robin across calls,
       fallback on rate limits, disk-cached).  Its title, authors, year,
       journal and DOI override everything else.  Skipped when *resolver*
       is ``None``.
    2. ``pdf-info`` -- the PDF Info dict (title, authors, keywords, doi,
       url), read with pypdfium2.  Local and fast, but often empty: many
       publishers ship no bibliographic fields in it.
    3. ``docling`` -- the free structured signals from Docling's layout
       model: the title item, the origin mimetype/URI, and the
       hyperlinks on text items.
    4. ``layout-regex`` -- regex over the focused first-page header
       region (the top fraction of page one, selected via the layout
       bounding boxes), where authors, keywords and the DOI live.
    5. ``regex`` -- regex over the first ``DEFAULT_SCAN_LIMIT``
       characters of the whole document text.  A broader net than the
       layout region, so it still catches a DOI/URL sitting in a
       first-page footer.

    **Output** -- a flat dict of non-empty fields (``title``,
    ``authors``, ``keywords``, ``year``, ``journal``, ``doi``, ``url``,
    ``source_type``, ``source_url``, ``urls``) plus two internal keys:

    - ``_metadata_complete`` -- ``True`` only when a full DOI record
      (title + authors + year) or a full PDF Info dict (title + author +
      keywords) was obtained; otherwise ``False``, signalling the
      researcher to review the pre-populated map.
    - ``_sources`` -- the tiers that contributed at least one field, in
      precedence order (e.g. ``["doi-service", "regex"]``).

    The abstract of a resolved record is used only as completeness
    evidence and is deliberately NOT included in the output: the abstract
    is already part of the chunked document, so persisting it per-chunk
    would duplicate it.

    :param dl_doc: A Docling ``DoclingDocument``
    :param file_path: Path of the source file
    :param pdf_path: Path to a PDF file, when the source is a PDF
    :param resolver: Optional :class:`Resolver`; when ``None`` the
        DOI-service tier is skipped (no network)
    :returns: Flat metadata dict including the ``_metadata_complete``
        and ``_sources`` internal keys
    """
    logger.debug(f"extracting metadata for {file_path = }\n{pdf_path = }")

    # Join the document's text items for the regex tiers.
    full_text = _document_text(dl_doc)

    pdf_fields = _pdf_fields(pdf_path)
    logger.debug(f"pdf-info tier: {pdf_fields}")

    docling_info = extract_docling_structured(dl_doc, file_path)
    logger.debug(f"docling tier: {docling_info}")

    layout_text = extract_layout_region(dl_doc)
    layout_regex = extract_regex_metadata(layout_text) if layout_text else {}
    logger.debug(f"layout-regex tier: {layout_regex}")

    regex_fields = extract_regex_metadata(full_text) if full_text else {}
    logger.debug(f"regex tier: {regex_fields}")

    doi = _discover_doi(pdf_fields, layout_regex, regex_fields)
    logger.debug(f"discovered {doi = }")

    candidates = _doi_candidates(full_text, docling_info)
    record = _resolve_record(doi, resolver, candidates)

    # Most authoritative first.
    tiers: list[tuple[str, dict]] = []
    if record is not None:
        tiers.append(("doi-service", _record_fields(record)))
    tiers.append(("pdf-info", pdf_fields))
    tiers.append(("docling", docling_info))
    tiers.append(("layout-regex", layout_regex))
    tiers.append(("regex", regex_fields))

    return _merge_tiers(tiers, record, pdf_fields, file_path)


def extract_metadata_from_text(
    text: str,
    file_path: str,
    pdf_path: str | None = None,
    resolver: Resolver | None = None,
) -> dict:
    """Extract bibliographic metadata from document text.

    The text-only entry point of the cascade, used when no Docling
    document is available (e.g. cached chunks).  Tiers are applied in
    **precedence order, most authoritative first**; each tier only fills
    fields not already set:

    1. ``doi-service`` -- a DOI found by a tier below is resolved, and
       its record overrides everything else.  Skipped when *resolver*
       is ``None``.
    2. ``pdf-info`` -- the PDF Info dict, when *pdf_path* is a PDF.
    3. ``regex`` -- the first ``DEFAULT_SCAN_LIMIT`` characters of
       *text* (the first chunks: the title and front matter).

    :param text: Document text (e.g. the joined cached chunk text)
    :param file_path: Path of the source file
    :param pdf_path: Path to a PDF file, when the source is a PDF
    :param resolver: Optional :class:`Resolver`; when ``None`` the
        DOI-service tier is skipped (no network)
    :returns: Flat metadata dict including the ``_metadata_complete``
        and ``_sources`` internal keys
    """
    logger.debug(f"extracting metadata from text for {file_path = }\n{pdf_path = }")

    pdf_fields = _pdf_fields(pdf_path)
    logger.debug(f"pdf-info tier: {pdf_fields}")

    regex_fields = extract_regex_metadata(text) if text else {}
    logger.debug(f"regex tier: {regex_fields}")

    doi = _discover_doi(pdf_fields, regex_fields)
    logger.debug(f"discovered {doi = }")

    record = _resolve_record(doi, resolver)

    # Most authoritative first.
    tiers: list[tuple[str, dict]] = []
    if record is not None:
        tiers.append(("doi-service", _record_fields(record)))
    tiers.append(("pdf-info", pdf_fields))
    tiers.append(("regex", regex_fields))

    return _merge_tiers(tiers, record, pdf_fields, file_path)


def _resolve_record(
    doi: str | None,
    resolver: Resolver | None,
    candidates: list[str] | None = None,
) -> BiblioRecord | None:
    """Resolve *doi* via *resolver*, falling back to *candidates*.

    The primary *doi* is tried first.  If it resolves to a record *with
    authors* (a paper record), that is used.  Otherwise -- or when the
    primary is missing -- the remaining *candidates* are tried in order
    until one yields a record with authors.  If only author-less records
    are found (e.g. a journal-level DOI like ``10.1073/pnas``), the first
    resolved record is returned as a last resort, matching the old
    behaviour.

    Logs an informational message when a discovered DOI is not resolved
    because no resolver was provided, and a warning when resolution is
    attempted but fails.

    :param doi: Primary discovered DOI, or ``None``
    :param resolver: Resolver, or ``None``
    :param candidates: Additional DOI candidates to try when the primary
        does not resolve to a paper record
    :returns: Resolved record, or ``None``
    """
    if resolver is None:
        if doi:
            logger.info(
                f"DOI {doi} discovered but DOI resolution skipped (no resolver)"
            )
        return None

    ordered = []
    if doi:
        ordered.append(doi)
    for candidate in candidates or []:
        if candidate not in ordered:
            ordered.append(candidate)
    if not ordered:
        return None

    first_resolved: BiblioRecord | None = None
    for candidate in ordered:
        logger.debug(f"trying DOI candidate {candidate!r}")
        record = resolver.resolve(candidate)
        if record is None:
            logger.warning(f"Could not resolve DOI {candidate} (see DOI resolver logs)")
            continue
        if first_resolved is None:
            first_resolved = record
            logger.info(
                f"resolved DOI {candidate} via DOI services\n"
                f"{record.title = }\n"
                f"{record.authors = }\n"
                f"{record.year = }"
            )
        if record.authors:
            logger.info(
                f"using DOI {candidate} (record has authors)\n"
                f"{record.title = }\n"
                f"{record.year = }"
            )
            return record

    logger.warning(
        "No DOI candidate resolved to a paper record; "
        "falling back to the first resolved record"
    )
    return first_resolved


def _doi_candidates(full_text: str, docling_info: dict) -> list[str]:
    """Return deduplicated DOI candidates from text and docling URLs.

    The primary DOI (from the labeled tiers) may be broken or a
    journal-level stub; these additional candidates give the resolver
    something to fall back to.  They come from two sources:

    - every ``10.`` match in the document text (:func:`_scan_dois`), and
    - DOIs embedded in the docling ``urls`` hyperlinks.

    :param full_text: Joined document text
    :param docling_info: Docling tier output (may carry a ``urls`` list)
    :returns: Deduplicated list of sanitized DOI strings
    """
    candidates: list[str] = []
    seen: set[str] = set()
    text_candidates = _scan_dois(full_text)
    url_candidates = [
        candidate
        for url in (docling_info.get("urls") or [])
        for candidate in _scan_dois(url)
    ]
    for candidate in text_candidates + url_candidates:
        normalized = normalize_doi(candidate)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)
    return candidates


def _document_text(dl_doc) -> str:
    """Join the document's text items into a single string."""
    return "\n".join(item.text for item in dl_doc.texts if item.text.strip())


def _pdf_fields(pdf_path: str | None) -> dict:
    """Extract and normalise the pdf-info tier, or ``{}``."""
    if not pdf_path:
        return {}
    return _normalize_pdf_info(extract_pdf_info(pdf_path))


def _normalize_pdf_info(pdf_info: dict) -> dict:
    """Normalise PDF Info fields to the canonical metadata key set."""
    result: dict = {}
    if pdf_info.get("title"):
        result["title"] = pdf_info["title"]
    if pdf_info.get("author"):
        result["authors"] = _split_terms(pdf_info["author"])
    if pdf_info.get("keywords"):
        result["keywords"] = _split_terms(pdf_info["keywords"])
    if pdf_info.get("doi"):
        result["doi"] = pdf_info["doi"]
    if pdf_info.get("url"):
        result["url"] = pdf_info["url"]
    return result


def _discover_doi(*sources: dict) -> str | None:
    """Return the first DOI found across *sources*."""
    for source in sources:
        doi = source.get("doi")
        if doi:
            return doi
    return None


def _record_fields(record: BiblioRecord) -> dict:
    """Convert a resolved record to canonical metadata fields.

    The abstract is deliberately excluded (it is already part of the
    chunked document).
    """
    fields = {
        "title": record.title,
        "authors": record.authors,
        "year": record.year,
        "journal": record.journal,
        "doi": record.doi,
    }
    return {key: value for key, value in fields.items() if value not in (None, [], "")}


def _merge_tiers(
    tiers: list[tuple[str, dict]],
    record: BiblioRecord | None,
    pdf_fields: dict,
    file_path: str,
) -> dict:
    """Merge the tier contributions into the final metadata dict.

    *tiers* is an ordered ``(label, fields)`` list in **precedence
    order, highest authority first** (``doi-service`` > ``pdf-info`` >
    ``docling``/``layout-regex`` > ``regex``).  Each tier only fills
    fields not already set (gap-fill), so the most authoritative tier
    that has a value for a field wins.  Also applies the filename-stem
    title fallback and computes the ``_metadata_complete`` /
    ``_sources`` internal keys.

    :param tiers: Ordered ``(label, fields)`` tier list, precedence order
    :param record: Resolved DOI record, or ``None``
    :param pdf_fields: Normalised pdf-info fields (used for the
        completeness check)
    :param file_path: Source file path (for the stem title fallback)
    :returns: Flat metadata dict with the internal keys
    """
    metadata: dict = {}
    sources: list[str] = []

    for label, fields in tiers:
        _gap_fill(metadata, fields, label, sources)

    if "title" not in metadata:
        metadata["title"] = Path(file_path).stem

    if record is not None:
        complete = bool(record.title and record.authors and record.year)
    else:
        complete = bool(
            pdf_fields.get("title")
            and pdf_fields.get("authors")
            and pdf_fields.get("keywords")
        )
    metadata["_metadata_complete"] = complete
    metadata["_sources"] = sources

    if not complete:
        logger.warning(
            f"metadata extraction incomplete for {file_path}; "
            f"review the pre-populated template"
        )
    logger.info(
        f"extracted metadata for {file_path}: "
        f"doi={metadata.get('doi')!r} complete={complete} sources={sources}"
    )
    logger.debug(
        f"metadata extraction done for {file_path = }\n"
        f"{metadata = }\n"
        f"{sources = }\n"
        f"{complete = }"
    )

    return metadata


def _gap_fill(metadata: dict, source: dict, label: str, sources: list[str]) -> None:
    """Fill unset *metadata* fields from *source*, recording *label*.

    Fields already present in *metadata* are never overwritten, which is
    what gives the cascade its most-authoritative-first precedence.
    """
    added = False
    for key, value in source.items():
        if key in metadata or not value:
            continue
        metadata[key] = value
        added = True
    if added and label not in sources:
        sources.append(label)


def _split_terms(value: str) -> list[str]:
    """Split a comma/semicolon-separated term list."""
    return [term.strip() for term in re.split(r"[,;]", value) if term.strip()]
