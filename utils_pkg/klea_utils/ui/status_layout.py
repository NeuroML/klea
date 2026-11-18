#!/usr/bin/env python3
# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""
Height budget for status-pane sections.

Both frontends render the same streamed ``state_sections``: the Textual
status panel and the NiceGUI status drawer.  A single long section (the plan,
a busy tool round) would otherwise grow without limit and swallow the pane, so
each section's body is capped to a content-derived height and scrolls inside
itself when it overflows.

This module is frontend-neutral: it counts the lines a renderer *would*
produce and turns that count into a height budget in whole lines.  It imports
neither ``textual`` nor ``nicegui``, so the two frontends cannot drift apart.

The outer pane's scroll stays authoritative: the budget only bounds one
section's body.  The pane keeps its own scroll and its tail-follow rule, and
an expanded section that overflows scrolls internally with overscroll
contained, so its scroll never chains into the pane.

File: klea_utils/ui/status_layout.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger(__name__)

#: Smallest budget: even an almost empty section gets a little room.
MIN_LINES = 5

#: Largest budget: a very long section still scrolls inside the pane.
MAX_LINES = 30

#: Budgets are quantised to this many lines, so streaming item growth does
#: not resize the pane on every tick.
BAND_LINES = 5

#: Aliases naming the same values as a budget, for call sites that read the
#: constants in "budget" terms rather than "raw line count" terms.
MIN_BUDGET_LINES = MIN_LINES
MAX_BUDGET_LINES = MAX_LINES


def _block_lines(block: Any) -> int:
    """Count the display lines one content block renders to.

    Mirrors the renderers' block handling (``items``, ``fields`` and the
    ``text``/unknown kind) purely in counts, so it never raises on a malformed
    block: anything unrecognised contributes at most one line.

    :param block: A content block (a mapping with a ``type``/``kind`` key).
    :returns: The number of logical lines the block renders to.
    """
    if not isinstance(block, Mapping):
        return 1 if block else 0
    kind = block.get("type") or block.get("kind") or "text"
    if kind == "items":
        count = 0
        for item in block.get("items") or ():
            if isinstance(item, Mapping):
                text = str(item.get("text") or item.get("label") or "")
            else:
                text = str(item) if item else ""
            if text:
                count += 1
        # Consecutive visible items are separated by one blank line unless the
        # block asks for the compact layout, matching both renderers.
        if count and block.get("layout", "spaced") != "compact":
            count += count - 1
        return count
    if kind == "fields":
        fields = block.get("fields") or {}
        return len(fields) if isinstance(fields, Mapping) else 0
    text = block.get("text")
    if text is None:
        text = block.get("content") or ""
    return 1 if text else 0


def estimate_section_lines(section: Mapping[str, Any] | None) -> int:
    """Estimate how many lines a status section's body renders to.

    The count mirrors the frontends' renderers: the structured ``content``
    blocks (``items`` with their spacing, ``fields`` and plain ``text``) are
    counted block by block, and the plain ``display`` (honouring
    ``preformatted``; logical lines only, since both renderers wrap) or the
    ``summary`` is used as the fallback.

    ``details`` is deliberately not counted: it renders inside its own nested
    disclosure, so the budget floor already covers it and this helper needs no
    JSON-formatting import.

    :param section: The stored section (content/display/summary/details/...).
    :returns: The estimated number of rendered lines (never negative).
    """
    if not isinstance(section, Mapping):
        return 0
    content = section.get("content")
    if content:
        blocks = content if isinstance(content, (list, tuple)) else [content]
        return sum(_block_lines(block) for block in blocks)
    display = section.get("display", "")
    if display:
        return len(str(display).splitlines())
    summary = section.get("summary", "")
    if summary:
        return len(str(summary).splitlines())
    return 0


def section_budget_lines(section: Mapping[str, Any] | None) -> int:
    """Return the height budget, in lines, for a status section's body.

    The estimate is padded by one line (so a section that just fits does not
    immediately overflow), clamped to :data:`MIN_LINES`..:data:`MAX_LINES`, and
    quantised up to the next :data:`BAND_LINES` band.  Two counts in one band
    share a budget, so a section that grows by a line or two while streaming
    keeps its height and the pane does not jitter on every tick.

    :param section: The stored section (content/display/summary/details/...).
    :returns: The clamped, banded height budget in lines.
    """
    lines = estimate_section_lines(section) + 1
    clamped = max(MIN_LINES, min(MAX_LINES, lines))
    banded = math.ceil(clamped / BAND_LINES) * BAND_LINES
    return max(MIN_LINES, min(MAX_LINES, banded))
