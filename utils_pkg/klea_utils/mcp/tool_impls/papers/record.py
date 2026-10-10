#!/usr/bin/env python3
"""
Normalised paper record returned by every paper search source.

File: klea_utils/mcp/tool_impls/papers/record.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import re

from klea_utils.biblio.doi import BiblioRecord

#: Abstracts longer than this are cut, so a page of results stays small
#: enough for the model's context.
MAX_ABSTRACT_CHARS = 2000

_DOI_PREFIX_RE = re.compile(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", re.IGNORECASE)

#: Venue names of preprint servers: arXiv, bioRxiv/medRxiv, Research Square,
#: Preprints.org and SSRN.
_PREPRINT_VENUE_RE = re.compile(
    r"rxiv|research square|preprints\.org|ssrn", re.IGNORECASE
)


class PaperRecord(BiblioRecord):
    """A search hit: the DOI resolver's record plus search-specific fields."""

    url: str | None = None
    #: False for preprints, so the model can tell the user the paper has not
    #: been peer reviewed.
    peer_reviewed: bool = True
    #: Name of the service that returned the record.
    source: str = ""


def clean_doi(raw: str | None) -> str | None:
    """Strip a ``https://doi.org/`` or ``doi:`` prefix from an API-provided DOI.

    Unlike :func:`klea_utils.biblio.doi.normalize_doi` this keeps everything
    after the prefix: DOI suffixes may contain ``/`` (for example
    ``10.1088/0957-4484/24/38/383001``).
    """
    if not raw:
        return None
    return _DOI_PREFIX_RE.sub("", str(raw).strip()) or None


def is_preprint(record: BiblioRecord) -> bool:
    """Whether *record* comes from a preprint server, by its venue.

    Some sources tag preprints as journal articles (Semantic Scholar, and
    SSRN in Crossref), so their peer reviewed filters alone are not enough.
    The DOI is not checked: Semantic Scholar merges a preprint with its
    published version and can keep the preprint DOI, while the venue is then
    the journal.
    """
    return bool(record.journal and _PREPRINT_VENUE_RE.search(record.journal))


def from_biblio(
    record: BiblioRecord,
    *,
    url: str | None,
    peer_reviewed: bool,
    source: str,
    doi: str | None = None,
) -> PaperRecord:
    """Build a :class:`PaperRecord` from a :class:`BiblioRecord`.

    :param record: Record produced by one of the DOI resolver normalisers.
    :param url: Link to the paper's landing page.
    :param peer_reviewed: Whether the paper is peer reviewed.
    :param source: Name of the service that returned the record.
    :param doi: DOI to use instead of ``record.doi``.
    """
    fields = record.model_dump()
    if doi:
        fields["doi"] = doi
    return PaperRecord(
        **fields,
        url=url or (f"https://doi.org/{fields['doi']}" if fields["doi"] else None),
        peer_reviewed=peer_reviewed,
        source=source,
    )


def truncate_abstract(abstract: str | None) -> str | None:
    """Cut *abstract* to :data:`MAX_ABSTRACT_CHARS`, marking the cut."""
    if abstract and len(abstract) > MAX_ABSTRACT_CHARS:
        return abstract[:MAX_ABSTRACT_CHARS].rstrip() + "..."
    return abstract
