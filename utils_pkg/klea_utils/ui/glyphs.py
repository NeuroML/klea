"""Shared glyph tables with Unicode/ASCII fallback detection.

Both frontends draw their status markers from this one module: the TUI (for
terminals, using the fallback logic below) and the web UI (which always uses
:data:`UNICODE_GLYPHS` directly, because a browser is always Unicode-capable).
A single source of truth keeps the role markers identical in both frontends.

The TUI can run on terminals that cannot render box-drawing or symbol
characters (non-UTF-8 locales, ``TERM=dumb``, redirected output, or CI
log capture).  This module keeps a single source of truth for the small
set of glyphs both frontends draw, plus the logic that decides which table
the TUI should use at runtime.

Selection order for :func:`get_glyphs` (terminal use only):

1. ``KLEA_TUI_ASCII=1`` (or ``true``/``yes``/``on``) forces the ASCII set.
2. Redirected output (``isatty()`` is false, including test doubles without a
   usable ``isatty``) forces the ASCII set.
3. On a terminal, a stdout encoding that is positively not UTF-8/UTF-16 forces
   the ASCII set; an unknown/missing encoding is trusted and stays Unicode.
4. A ``TERM`` value of ``dumb``/``unknown`` forces the ASCII set.
5. Otherwise the Unicode set is used.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from typing import TextIO


@dataclass(frozen=True)
class GlyphSet:
    """A complete set of glyphs used by the frontends.

    Keeping every glyph on one dataclass means callers never mix Unicode
    and ASCII characters in a single frame.

    The ``marker_*`` fields are status role markers resolved by
    :func:`role_marker`.
    """

    success: str  # completed step / passing check
    failure: str  # failed step / error marker
    pending: str  # in-flight or not-yet-run step
    agent: str  # bullet preceding an agent turn
    prompt: str  # input prompt caret
    separator: str  # vertical rule between panes / rows
    overflow: str  # truncation marker for clipped text
    marker_ok: str  # completed / passing item
    marker_error: str  # failed item
    marker_current: str  # item being worked on
    marker_pending: str  # item not yet started
    marker_plain: str  # item with no recognised role


#: Unicode glyph table used for capable terminals and the web (the default).
UNICODE_GLYPHS = GlyphSet(
    marker_ok="\u2714",  # U+2714 heavy check mark
    marker_error="\u2718",  # U+2718 heavy ballot X
    marker_current="\u25b6",  # U+25B6 black right-pointing triangle
    marker_pending="\u25cb",  # U+25CB white circle
    marker_plain="\u00b7",  # U+00B7 middle dot
    success="\u2714",  # U+2714 heavy check mark
    failure="\u2718",  # U+2718 heavy ballot X
    pending="\u22ef",  # U+22EF midline horizontal ellipsis
    agent="\u25c6",  # U+25C6 black diamond
    prompt="\u276f",  # U+276F heavy right-pointing angle quotation mark ornament
    separator="\u2502",  # U+2502 box drawings light vertical
    overflow="\u2026",  # U+2026 horizontal ellipsis
)


#: Pure-ASCII fallback table for non-Unicode terminals.
ASCII_GLYPHS = GlyphSet(
    marker_ok="[x]",
    marker_error="[!]",
    marker_current="[*]",
    marker_pending="[ ]",
    marker_plain="[-]",
    success="[ok]",
    failure="[x]",
    pending="...",
    agent="*",
    prompt=">",
    separator="|",
    overflow="~",
)


_TRUTHY_VALUES = frozenset({"1", "true", "yes", "on"})
_ASCII_HINT_TERMS = frozenset({"dumb", "unknown"})
_UTF_ENCODING_PREFIXES = ("utf-8", "utf8", "utf-16", "utf16")


def ascii_forced() -> bool:
    """Return ``True`` when ``KLEA_TUI_ASCII`` requests the ASCII set."""

    return os.environ.get("KLEA_TUI_ASCII", "").strip().lower() in _TRUTHY_VALUES


def _active_stream(stream: TextIO | None) -> TextIO | None:
    """Return ``stream`` when given, else the current :data:`sys.stdout`."""

    return stream if stream is not None else sys.stdout


def _stream_encoding(stream: TextIO | None) -> str:
    """Return the normalized encoding name of ``stream`` (may be empty)."""

    active = _active_stream(stream)
    encoding = getattr(active, "encoding", None) or ""
    return encoding.replace("_", "-").strip().lower()


def is_redirected(stream: TextIO | None = None) -> bool:
    """Report whether ``stream`` is not attached to an interactive terminal.

    ``stream`` defaults to :data:`sys.stdout`.  Streams that do not expose
    ``isatty`` (test doubles, bare file-like wrappers) are treated as
    redirected, and unusable streams never raise.
    """

    active = _active_stream(stream)
    isatty = getattr(active, "isatty", None)
    if not callable(isatty):
        return True
    try:
        return not bool(isatty())
    except (ValueError, OSError):  # closed or otherwise unusable stream
        return True


def detect_unicode_support(stream: TextIO | None = None) -> bool:
    """Report whether the active output stream can render Unicode glyphs.

    ``stream`` defaults to :data:`sys.stdout`.  The check is intentionally
    conservative: anything ambiguous falls back to ASCII so the TUI stays
    readable over pipes and dumb terminals.

    ``isatty()`` is consulted first: redirected output (or a stream without a
    working ``isatty``) always falls back to ASCII.  For a stream attached to
    a terminal, only a positively non-UTF encoding is a reason to fall back;
    a missing/unknown encoding is treated as Unicode-capable.
    ``KLEA_TUI_ASCII`` and ``TERM=dumb`` / ``TERM=unknown`` always select the
    ASCII set.
    """

    if ascii_forced():
        return False

    if is_redirected(stream):
        return False

    encoding = _stream_encoding(stream)
    if encoding and not encoding.startswith(_UTF_ENCODING_PREFIXES):
        return False

    term = os.environ.get("TERM", "").strip().lower()
    return term not in _ASCII_HINT_TERMS


def get_glyphs(stream: TextIO | None = None) -> GlyphSet:
    """Return the glyph set to use for the active output stream.

    Unicode is the default; ASCII is selected when ``KLEA_TUI_ASCII`` is
    truthy or when :func:`detect_unicode_support` says the stream cannot
    render Unicode.
    """

    return UNICODE_GLYPHS if detect_unicode_support(stream) else ASCII_GLYPHS


#: Status role -> :class:`GlyphSet` field holding that role's marker.
_ROLE_MARKER_FIELDS: dict[str, str] = {
    "ok": "marker_ok",
    "error": "marker_error",
    "current": "marker_current",
    "pending": "marker_pending",
}


def role_marker(glyphs: GlyphSet, role: object) -> str:
    """Return the status marker glyph for *role*.

    The marker is chosen from a fixed role table; unknown roles and roles
    that are not strings fall back to ``glyphs.marker_plain``.  The lookup
    never raises and never inspects the item's text, so emitters may send
    new roles without breaking rendering.

    :param glyphs: The glyph set selected for the active frontend.
    :param role: The block/item role (``ok``/``error``/``current``/``pending``).
    :returns: The matching marker (the plain fallback when unknown).
    """

    if isinstance(role, str):
        field = _ROLE_MARKER_FIELDS.get(role)
        if field is not None:
            return getattr(glyphs, field)
    return glyphs.marker_plain
