# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Validation errors raised by the test-case loader (ADR-0056).

The loader in :mod:`klea_eval.cases` raises :class:`CaseValidationError` for
 every load-time rejection of a JSONL test case.  The class carries the
source file, the JSONL line number and the case identifier of the offending
case so that the CLI can render a precise message and map the failure to exit
code 2 (ADR-0060).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

__all__ = ["CaseValidationError"]


class CaseValidationError(ValueError):
    """A test case failed load-time validation (ADR-0056).

    Derives from :class:`ValueError` so that ``json`` and ``pytest.raises``
    idioms keep working, but adds the location of the offending case: the
    source ``path``, the JSONL ``line_number`` and, once known, the
    ``case_id``.  The class carries no exit code of its own; the CLI maps it
    to exit code 2 (ADR-0060).
    """

    def __init__(
        self,
        message: str,
        *,
        source: Path | None = None,
        line_number: int | None = None,
        case_id: str | None = None,
    ) -> None:
        """Record the failure message and where in the suite it happened."""
        super().__init__(message)
        self.message = message
        self.source = source
        self.line_number = line_number
        self.case_id = case_id
        logger.debug(
            "case validation failed (source=%s, line=%s, case_id=%s): %s",
            source,
            line_number,
            case_id,
            message,
        )

    def __str__(self) -> str:
        """Render ``<source>:<line>: <message>``, degrading gracefully."""
        location = ""
        if self.source is not None:
            location = str(self.source)
        if self.line_number is not None:
            if location:
                location = f"{location}:{self.line_number}"
            else:
                location = f"line {self.line_number}"
        if location:
            return f"{location}: {self.message}"
        if self.case_id is not None:
            return f"case '{self.case_id}': {self.message}"
        return self.message
