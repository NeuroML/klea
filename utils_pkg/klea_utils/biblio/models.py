#!/usr/bin/env python3
"""
Normalised bibliographic record shared across the DOI services and the
academic paper search.

File: klea_utils/biblio/models.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from pydantic import BaseModel


class BiblioRecord(BaseModel):
    """Normalised bibliographic record shared across the DOI services."""

    title: str | None = None
    authors: list[str] = []
    year: int | None = None
    journal: str | None = None
    abstract: str | None = None
    doi: str | None = None
