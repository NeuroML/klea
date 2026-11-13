#!/usr/bin/env python3
"""
Shared per-line character caps for Klea MCP tool output.

A single very long line (minified JSON, a generated blob, a one-line log) can
be far larger than the transport frame that carries a tool result.  The total
character budgets used by ``grep`` and ``run_command`` only bound the sum of
all lines, so one such line can still dominate the response.  These helpers
cap each individual line and append an explicit, model-visible marker so the
reader knows text was dropped and roughly how much.

File: klea_utils/mcp/tool_impls/text_limits.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

logger = logging.getLogger(__name__)

#: Default cap on the characters kept from a single line.
DEFAULT_MAX_LINE_CHARS = 2_000

#: Marker appended to a capped line.  ``{omitted}`` is replaced by the number
#: of characters dropped, so the truncation is visible to the model.
LINE_TRUNCATION_MARKER = " ... [line truncated: {omitted} chars omitted]"


def _split_terminator(line: str) -> tuple[str, str]:
    """Split a line into its text and its trailing line terminator.

    :param line: One line, possibly ending in ``\n``, ``\r\n`` or ``\r``.
    :returns: ``(text, terminator)``; the terminator is empty when absent.
    """
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    if line.endswith("\r"):
        return line[:-1], "\r"
    return line, ""


def _render_marker(marker: str, omitted: int) -> str:
    """Render *marker* for *omitted* dropped characters.

    A caller-supplied marker without the ``{omitted}`` placeholder (or with a
    malformed one) is used verbatim rather than raising.

    :param marker: Marker template.
    :param omitted: Number of characters dropped from the line.
    :returns: The marker text.
    """
    try:
        return marker.format(omitted=omitted)
    except (KeyError, IndexError, ValueError):
        logger.warning(f"Marker {marker!r} could not be formatted; using it as-is")
        return marker


def _check_max_chars(max_chars: int) -> int:
    """Validate a per-line character limit.

    :param max_chars: Requested characters to keep per line.
    :returns: The value, clamped to at least 1.
    """
    if max_chars < 1:
        logger.warning(f"Non-positive max_chars {max_chars}; using 1")
        return 1
    return max_chars


def cap_line(
    line: str,
    max_chars: int = DEFAULT_MAX_LINE_CHARS,
    *,
    marker: str = LINE_TRUNCATION_MARKER,
) -> tuple[str, bool]:
    """Cap one line at *max_chars* and append a truncation marker.

    The line terminator (``\n``, ``\r\n`` or ``\r``) is preserved and is not
    counted against *max_chars*.  The marker is added after the kept text, so
    the returned line can be slightly longer than *max_chars*; that is
    deliberate, because the marker must stay visible to the model.

    :param line: The line to cap, with or without a trailing terminator.
    :param max_chars: Maximum characters of line text to keep (>= 1).
    :param marker: Marker template appended when the line is capped.
    :returns: ``(line, was_capped)``.
    """
    max_chars = _check_max_chars(max_chars)
    text, terminator = _split_terminator(line)
    if len(text) <= max_chars:
        return line, False

    omitted = len(text) - max_chars
    capped = text[:max_chars] + _render_marker(marker, omitted) + terminator
    logger.debug(f"Capped line at {max_chars} characters: {omitted} characters omitted")
    return capped, True


def cap_lines(
    text: str,
    max_chars: int = DEFAULT_MAX_LINE_CHARS,
    *,
    marker: str = LINE_TRUNCATION_MARKER,
) -> tuple[str, int]:
    """Cap every line of *text* at *max_chars*, appending the marker.

    Line terminators are preserved and not counted against *max_chars*, so a
    block's line structure is unchanged apart from the capped lines.

    :param text: The block of text to process.
    :param max_chars: Maximum characters kept per line (>= 1).
    :param marker: Marker template appended to each capped line.
    :returns: ``(text, number_of_lines_capped)``.
    """
    if not text:
        return text, 0

    max_chars = _check_max_chars(max_chars)
    capped_lines = 0
    parts: list[str] = []
    for line in text.split("\n"):
        capped, was_capped = cap_line(line, max_chars, marker=marker)
        if was_capped:
            capped_lines += 1
        parts.append(capped)

    if capped_lines:
        logger.debug(
            f"Capped {capped_lines} line(s) at {max_chars} characters per line"
        )
    return "\n".join(parts), capped_lines
