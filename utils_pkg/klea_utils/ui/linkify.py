#!/usr/bin/env python3
"""
Bare-URL linkification for markdown text

File: klea_utils/ui/linkify.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import re

from linkify_it import LinkifyIt

logger = logging.getLogger(__name__)

#: Existing ``[text](url)`` markdown links are left untouched; only bare
#: URLs between them are linkified.  ``[^)]*`` is greedy up to the first
#: ``)``, which also tolerates balanced parentheses inside the URL.
_MARKDOWN_LINK_RE = re.compile(r"\[[^\]]*\]\([^)]*\)")

#: A fenced-code delimiter line (`` ` `` or ``~``), optionally indented up to
#: three spaces per CommonMark.  Content between an opening and matching
#: closing fence is code and must not be linkified.
_FENCE_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})", re.MULTILINE)

#: URL detection engine.  Defaults enable "fuzzy" matching of bare domains
#: and emails in addition to explicit http(s):// URLs.
_LINKIFY = LinkifyIt()


def linkify_md(text: str) -> str:
    """Convert bare URLs in markdown *text* to ``[url](url)`` links.

    Existing ``[text](url)`` links are returned verbatim so they are not
    re-wrapped, and fenced code blocks (```` ``` ```` / ``~~~``) are left
    untouched: a URL in code would otherwise be wrapped and the literal
    ``[url](url)`` shown inside the code span (fenced blocks are not parsed
    as markdown links).  Callers pass the result to a markdown renderer,
    which turns the wrapped URLs into clickable anchors.

    :param text: Markdown source text
    :returns: Text with bare URLs wrapped as ``[url](url)``
    """
    parts: list[str] = []
    last = 0
    fence: str | None = None
    for match in _FENCE_RE.finditer(text):
        # Text before this delimiter is code when a fence is open, otherwise
        # prose that may be linkified.
        segment = text[last : match.start()]
        parts.append(segment if fence is not None else _linkify_segment(segment))
        # The delimiter line itself is always verbatim.
        line_end = text.find("\n", match.end())
        line_end = len(text) if line_end == -1 else line_end + 1
        parts.append(text[match.start() : line_end])
        marker = match.group(2)
        if fence is None:
            fence = marker[0]  # ``` or ~~~
        elif marker[0] == fence:
            fence = None
        last = line_end
    tail = text[last:]
    parts.append(tail if fence is not None else _linkify_segment(tail))
    return "".join(parts)


def _linkify_segment(text: str) -> str:
    """Wrap bare URLs in a code-FREE markdown segment."""
    if not text:
        return text
    result: list[str] = []
    last = 0
    for match in _MARKDOWN_LINK_RE.finditer(text):
        result.append(_linkify_plain(text[last : match.start()]))
        result.append(match.group(0))
        last = match.end()
    result.append(_linkify_plain(text[last:]))
    return "".join(result)


def _linkify_plain(text: str) -> str:
    """Wrap bare URLs in *text* (which contains no markdown links)."""
    if not text:
        return text
    matches = _LINKIFY.match(text)
    if not matches:
        return text
    result: list[str] = []
    last = 0
    for match in matches:
        result.append(text[last : match.index])
        result.append(f"[{match.text}]({match.url})")
        last = match.last_index
    result.append(text[last:])
    return "".join(result)
