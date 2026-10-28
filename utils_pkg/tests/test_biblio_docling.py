#!/usr/bin/env python3
"""
Test Docling-based bibliographic metadata extraction.

File: tests/test_biblio_docling.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path

from docling_core.types.doc.document import (
    BoundingBox,
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    ProvenanceItem,
    Size,
)
from klea_utils.biblio.docling import extract_docling_structured, extract_layout_region
from pydantic import AnyUrl

logger = logging.getLogger(__name__)


def _make_doc(
    title: str | None = None,
    mimetype: str = "application/pdf",
    uri: str | None = "https://example.com/paper",
    text_items: list[tuple[str, int, int]] | None = None,
) -> DoclingDocument:
    """Build a synthetic DoclingDocument for testing."""
    doc = DoclingDocument(name="paper")
    doc.add_page(page_no=1, size=Size(width=612, height=792))
    doc.origin = DocumentOrigin(
        mimetype=mimetype,
        binary_hash=0,
        filename="paper.pdf",
        uri=uri,
    )
    if title:
        doc.add_title(text=title)
    for text, page_no, top in text_items or []:
        doc.add_text(
            text=text,
            label=DocItemLabel.TEXT,
            prov=ProvenanceItem(
                page_no=page_no,
                bbox=BoundingBox(l=50, t=top, r=300, b=top + 20),
                charspan=(0, len(text)),
            ),
        )
    return doc


def test_structured_title_and_origin():
    """Title, source_type and source_url come straight from the document."""
    doc = _make_doc(title="A Synthetic Paper")
    result = extract_docling_structured(doc, "paper.pdf")
    logger.info(f"structured result: {result}")
    assert result["title"] == "A Synthetic Paper"
    assert result["source_type"] == "application/pdf"
    assert result["source_url"] == "https://example.com/paper"


def test_structured_title_falls_back_to_stem():
    """A document without a title item falls back to the filename stem."""
    doc = _make_doc()
    result = extract_docling_structured(doc, "some-paper.pdf")
    logger.info(f"stem-fallback result: {result}")
    assert result["title"] == "some-paper"


def test_structured_no_uri_omits_source_url():
    """A document without an origin URI has no source_url."""
    doc = _make_doc(title="T", uri=None)
    result = extract_docling_structured(doc, "paper.pdf")
    logger.info(f"no-uri result: {result}")
    assert "source_url" not in result


def test_structured_hyperlinks_deduped_and_filtered():
    """http(s) hyperlinks are deduped; non-http links are dropped."""
    doc = DoclingDocument(name="paper")
    doc.origin = DocumentOrigin(
        mimetype="text/markdown", binary_hash=0, filename="paper.md"
    )
    doc.add_text(
        text="a", label=DocItemLabel.TEXT, hyperlink=AnyUrl("https://example.com/a")
    )
    doc.add_text(
        text="a again",
        label=DocItemLabel.TEXT,
        hyperlink=AnyUrl("https://example.com/a"),
    )
    doc.add_text(
        text="file", label=DocItemLabel.TEXT, hyperlink=Path("/local/file.pdf")
    )

    result = extract_docling_structured(doc, "paper.md")
    logger.info(f"hyperlink result: {result}")
    assert result["urls"] == ["https://example.com/a"]


def test_layout_region_includes_top_items_only():
    """Only text items in the top fraction of the page are returned."""
    doc = _make_doc(
        title="A Synthetic Paper",
        text_items=[
            ("Header text", 1, 100),  # in the top region
            ("Body text", 1, 400),  # below the threshold
            ("Other page", 2, 100),  # different page
        ],
    )
    region = extract_layout_region(doc, page=1, frac=0.35)
    logger.info(f"layout region: {region!r}")
    assert region == "Header text"


def test_layout_region_none_when_page_missing():
    """A missing page yields None."""
    doc = _make_doc(title="T")
    assert extract_layout_region(doc, page=2) is None


def test_layout_region_none_when_empty():
    """A page with nothing in the region yields None."""
    doc = _make_doc(title="T", text_items=[("far below", 1, 700)])
    assert extract_layout_region(doc, page=1, frac=0.35) is None


if __name__ == "__main__":
    import pytest

    pytest.main()
