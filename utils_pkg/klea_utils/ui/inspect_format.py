#!/usr/bin/env python3
"""
Human-readable formatting for streamed inspection details.

``NodeStreamData.details`` is structured JSON (dict/list/scalar) carried
over the ``inspect`` stream event.  ``json.dumps`` escapes embedded
newlines, so multi-line values such as prompts collapse into a single
unreadable line.  :func:`format_details` keeps the JSON shape but renders
string *values* faithfully: embedded newlines become real line breaks, and
a value that is itself a JSON object/array is inlined and pretty-printed.
The output is plain text, for any frontend (the NiceGUI inspector, the
TUI).

The structure, indentation and quote/backslash escaping are all delegated
to ``json.dumps``.  Only the two value transformations are custom (see
:func:`_prepare`), because ``json.dumps`` always escapes newlines.

File: klea_utils/ui/inspect_format.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

#: Placeholder for a real newline while ``json.dumps`` runs; ``json`` would
#: otherwise emit ``\n``.  A Private Use Area codepoint, which never appears
#: in model prompts or other detail text.
_NEWLINE_SENTINEL = "\ue000"


def _as_json_container(text: str) -> Any | None:
    """Return the parsed dict/list when *text* is a JSON object/array string.

    Only object/array strings are inlined; a bare number or quoted string
    (or any non-JSON text, such as a Pydantic ``repr``) is left alone.

    :param text: A string value from the details payload.
    :returns: The parsed container, or ``None`` when not a JSON object/array.
    """
    stripped = text.strip()
    if not stripped or stripped[0] not in "[{":
        return None
    try:
        parsed = json.loads(stripped)
    except (ValueError, TypeError):
        return None
    return parsed if isinstance(parsed, (dict, list)) else None


def _prepare(value: Any) -> Any:
    """Rewrite *value* so ``json.dumps`` renders its strings readably.

    * a JSON object/array string is replaced by its parsed structure, so it
      is inlined and pretty-printed;
    * remaining newlines are swapped for :data:`_NEWLINE_SENTINEL`, so they
      survive ``json.dumps`` and are restored afterwards as real breaks.

    :param value: The details payload (or a nested value).
    :returns: An equivalent structure with the string values prepared.
    """
    if isinstance(value, dict):
        return {str(key): _prepare(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_prepare(item) for item in value]
    if isinstance(value, str):
        parsed = _as_json_container(value)
        if parsed is not None:
            return _prepare(parsed)
        return value.replace("\n", _NEWLINE_SENTINEL)
    return value


def format_details(details: Any) -> str:
    """Render structured inspect ``details`` as readable indented JSON.

    The structure matches ``json.dumps(..., indent=2)``; only string values
    are treated differently:

    * embedded newlines are kept as real line breaks (at their original
      column, so the text is not altered), so multi-line prompts read as
      text;
    * a value that is itself a JSON object/array string is parsed and inlined
      as nested, pretty-printed JSON;
    * all other values (numbers, booleans, ``null``, single-line strings
      such as a Pydantic ``repr``) are rendered as normal JSON.

    :param details: The ``details`` payload of an ``inspect`` event.
    :returns: A human-readable, indented multi-line string.
    """
    logger.debug(f"{type(details) = }")
    return json.dumps(_prepare(details), indent=2, ensure_ascii=False).replace(
        _NEWLINE_SENTINEL, "\n"
    )
