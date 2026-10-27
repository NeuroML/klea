"""Unit tests for the TUI glyph tables and Unicode/ASCII selection.

File: utils_pkg/tests/ui/tui/test_glyphs.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from dataclasses import fields
from typing import TextIO, cast

import pytest
from klea_utils.ui.glyphs import (
    ASCII_GLYPHS,
    UNICODE_GLYPHS,
    GlyphSet,
    ascii_forced,
    detect_unicode_support,
    get_glyphs,
)


class _FakeStream:
    def isatty(self) -> bool:
        """Report the fake stream as an interactive terminal."""
        return True

    """Minimal stand-in for a text stream exposing only ``encoding``."""

    def __init__(self, encoding: str | None) -> None:
        self.encoding = encoding


@pytest.fixture(autouse=True)
def _capable_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run every test on a UTF-8-capable terminal unless it overrides this."""

    monkeypatch.delenv("KLEA_TUI_ASCII", raising=False)
    monkeypatch.setenv("TERM", "xterm-256color")


# --- glyph set contents ----------------------------------------------------


@pytest.mark.parametrize("glyph_set", [UNICODE_GLYPHS, ASCII_GLYPHS])
def test_all_glyph_fields_are_non_empty_strings(glyph_set: GlyphSet) -> None:
    """Every field of a glyph table holds a non-empty string."""

    for field in fields(glyph_set):
        value = getattr(glyph_set, field.name)
        assert isinstance(value, str), f"{field.name} is not a string: {value!r}"
        assert value.strip(), f"{field.name} is empty"


def test_ascii_glyphs_are_pure_ascii() -> None:
    """The fallback table never emits non-ASCII characters."""

    for field in fields(ASCII_GLYPHS):
        getattr(ASCII_GLYPHS, field.name).encode("ascii")


def test_unicode_glyphs_differ_from_ascii() -> None:
    """The two tables are distinct so a mismatch is detectable."""

    assert UNICODE_GLYPHS != ASCII_GLYPHS


# --- ascii_forced ----------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " on "])
def test_ascii_forced_truthy_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """``KLEA_TUI_ASCII`` accepts the documented truthy spellings."""

    monkeypatch.setenv("KLEA_TUI_ASCII", value)
    assert ascii_forced() is True


@pytest.mark.parametrize("value", ["", "0", "false", "no", "off", "dumb"])
def test_ascii_forced_falsey_values(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """Anything else leaves the ASCII set unforced."""

    monkeypatch.setenv("KLEA_TUI_ASCII", value)
    assert ascii_forced() is False


def test_ascii_forced_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unset variable does not force ASCII."""

    monkeypatch.delenv("KLEA_TUI_ASCII", raising=False)
    assert ascii_forced() is False


# --- detection -------------------------------------------------------------


@pytest.mark.parametrize("encoding", ["utf-8", "UTF-8", "utf8", "utf-16", "utf16"])
def test_detect_unicode_support_utf_encodings(encoding: str) -> None:
    """UTF-8/UTF-16 streams are treated as Unicode-capable."""

    assert detect_unicode_support(cast("TextIO", _FakeStream(encoding))) is True


@pytest.mark.parametrize("encoding", ["cp1252", "CP1252", "latin-1", "ascii"])
def test_detect_unicode_support_non_utf_encodings(encoding: str) -> None:
    """Non-UTF encodings fall back to ASCII."""

    assert detect_unicode_support(cast("TextIO", _FakeStream(encoding))) is False


@pytest.mark.parametrize(
    ("isatty", "expected"),
    [(True, True), (False, False)],
)
def test_detect_unicode_support_none_encoding(isatty: bool, expected: bool) -> None:
    """A stream with an unknown encoding falls back to ``isatty``."""

    class _AnonStream(_FakeStream):
        """Stream with no known encoding that reports ``isatty()``."""

        def isatty(self) -> bool:
            return isatty

    assert detect_unicode_support(cast("TextIO", _AnonStream(None))) is expected


@pytest.mark.parametrize("term", ["dumb", "DUMB", "unknown"])
def test_detect_unicode_support_dumb_terminals(
    monkeypatch: pytest.MonkeyPatch, term: str
) -> None:
    """Dumb/unknown terminals fall back to ASCII even on a UTF-8 stream."""

    monkeypatch.setenv("TERM", term)
    assert detect_unicode_support(cast("TextIO", _FakeStream("utf-8"))) is False


def test_ascii_forced_beats_utf8_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment override wins over an otherwise capable stream."""

    monkeypatch.setenv("KLEA_TUI_ASCII", "1")
    assert detect_unicode_support(cast("TextIO", _FakeStream("utf-8"))) is False


# --- glyph set selection ---------------------------------------------------


def test_get_glyphs_unicode_when_encoding_is_utf8() -> None:
    """A UTF-8 stream selects the Unicode set."""

    assert get_glyphs(cast("TextIO", _FakeStream("utf-8"))) == UNICODE_GLYPHS


def test_get_glyphs_ascii_when_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """``KLEA_TUI_ASCII=1`` selects the ASCII set even on UTF-8."""

    monkeypatch.setenv("KLEA_TUI_ASCII", "1")
    assert get_glyphs(cast("TextIO", _FakeStream("utf-8"))) == ASCII_GLYPHS


def test_get_glyphs_ascii_when_encoding_is_cp1252() -> None:
    """A CP1252 stream selects the ASCII set."""

    assert get_glyphs(cast("TextIO", _FakeStream("cp1252"))) == ASCII_GLYPHS


def test_get_glyphs_defaults_to_stdout(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no stream argument the selection follows ``sys.stdout``."""

    import sys

    monkeypatch.setattr(sys, "stdout", _FakeStream("utf-8"))
    assert get_glyphs() == UNICODE_GLYPHS
    monkeypatch.setattr(sys, "stdout", _FakeStream("cp1252"))
    assert get_glyphs() == ASCII_GLYPHS


@pytest.mark.parametrize(
    ("isatty", "expected_set"),
    [(True, UNICODE_GLYPHS), (False, ASCII_GLYPHS)],
)
def test_get_glyphs_unknown_encoding_follows_isatty(
    isatty: bool, expected_set: GlyphSet
) -> None:
    """An unknown encoding defers set selection to the stream's ``isatty()``."""

    class _AnonStream(_FakeStream):
        """Stream with no known encoding that reports ``isatty()``."""

        def isatty(self) -> bool:
            return isatty

    stream = _AnonStream(None)
    assert detect_unicode_support(cast("TextIO", stream)) is isatty
    assert get_glyphs(cast("TextIO", stream)) == expected_set


def test_get_glyphs_unknown_encoding_ignores_ascii_hint_terms() -> None:
    """A non-dumb terminal with an unknown encoding still follows ``isatty()``."""

    class _AnonTtyStream(_FakeStream):
        """TTY stream with an unrecognised encoding."""

        def isatty(self) -> bool:
            return True

    assert get_glyphs(cast("TextIO", _AnonTtyStream(""))) == UNICODE_GLYPHS
