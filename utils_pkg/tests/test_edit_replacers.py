#!/usr/bin/env python3
"""
Tests for the search/replace replacer chain.

File: utils_pkg/tests/test_edit_replacers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import logging

from klea_utils.mcp.tool_impls import edit_replacers as er

logger = logging.getLogger(__name__)


def test_simple_replacer_yields_old_string():
    assert list(er.simple_replacer("content", "find")) == ["find"]


def test_line_trimmed_replacer_matches_reindented_block():
    content = "def f():\n    x = 1\n    return x\n"

    spans = list(er.line_trimmed_replacer(content, "x = 1\nreturn x"))

    logger.debug(f"{spans = }")
    assert spans == ["    x = 1\n    return x"]


def test_whitespace_normalized_replacer_matches_spacing():
    content = "value    =    1\n"

    spans = list(er.whitespace_normalized_replacer(content, "value = 1"))

    logger.debug(f"{spans = }")
    assert spans == ["value    =    1"]


def test_indentation_flexible_replacer_matches_shifted_block():
    content = "def f():\n    x = 1\n    y = 2\n"

    spans = list(
        er.indentation_flexible_replacer(content, "        x = 1\n        y = 2")
    )

    logger.debug(f"{spans = }")
    assert spans == ["    x = 1\n    y = 2"]


def test_block_anchor_replacer_tolerates_middle_line_drift():
    content = "def f():\n    alpha one\n    beta two\n    gamma three\n    return x\n"
    old = "def f():\n    alpha one changed\n    beta two\n    gamma three\n    return x"

    spans = list(er.block_anchor_replacer(content, old))

    logger.debug(f"{spans = }")
    assert spans == [
        ("def f():\n    alpha one\n    beta two\n    gamma three\n    return x")
    ]


def test_block_anchor_replacer_ignores_short_blocks():
    assert list(er.block_anchor_replacer("a\nb\n", "a\nx")) == []


def test_block_anchor_replacer_picks_best_of_multiple_candidates():
    content = (
        "def f():\n"
        "    alpha\n"
        "    beta\n"
        "    return x\n"
        "middle\n"
        "def f():\n"
        "    alpha\n"
        "    delta\n"
        "    return x\n"
    )
    old = "def f():\n    alpha\n    beta changed\n    return x"

    spans = list(er.block_anchor_replacer(content, old))

    logger.debug(f"{spans = }")
    assert spans == ["def f():\n    alpha\n    beta\n    return x"]


def test_context_aware_replacer_tolerates_half_middle_mismatch():
    content = "start\na\nb\nc\nend\n"
    old = "start\na\nX\nc\nend"

    spans = list(er.context_aware_replacer(content, old))

    logger.debug(f"{spans = }")
    assert spans == ["start\na\nb\nc\nend"]


def test_context_aware_replacer_ignores_short_blocks():
    assert list(er.context_aware_replacer("a\nb\n", "a\nx")) == []


def test_line_similarity():
    assert er._line_similarity("abc", "abc") == 1.0
    assert er._line_similarity("abc", "") == 0.0
    ratio = er._line_similarity("alpha one", "alpha one changed")
    logger.debug(f"{ratio = }")
    assert 0.0 < ratio < 1.0


def test_is_disproportionate_match():
    old = "a\nb"
    assert er.is_disproportionate_match("a\nb", old) is False
    # A span several times longer than old_string is disproportionate.
    assert er.is_disproportionate_match("a\nb\nc\nd\ne\nf\ng", old) is True
    # Single-line old_string is never flagged on length alone.
    assert er.is_disproportionate_match("x" * 5000, "x") is False


def test_apply_edit_exact():
    updated, count, matcher, error = er.apply_edit("a\nb\nc\n", "b", "B")

    logger.debug(f"{updated = } {count = } {matcher = } {error = }")
    assert updated == "a\nB\nc\n"
    assert count == 1
    assert matcher == "exact"
    assert error == ""


def test_apply_edit_falls_back_to_line_trimmed():
    content = "def f():\n    x = 1\n    return x\n"

    updated, _count, matcher, _error = er.apply_edit(
        content, "x = 1\nreturn x", "    x = 2\n    return x"
    )

    logger.debug(f"{updated = } {matcher = }")
    assert matcher == "line-trimmed"
    assert updated == "def f():\n    x = 2\n    return x\n"


def test_apply_edit_falls_back_to_whitespace_normalized():
    updated, _count, matcher, _error = er.apply_edit(
        "value    =    1\n", "value = 1", "value = 2"
    )

    logger.debug(f"{updated = } {matcher = }")
    assert matcher == "whitespace-normalised"
    assert updated == "value = 2\n"


def test_apply_edit_block_anchor_end_to_end():
    content = "def f():\n    alpha one\n    beta two\n    gamma three\n    return x\n"
    old = "def f():\n    alpha one changed\n    beta two\n    gamma three\n    return x"

    updated, _count, matcher, _error = er.apply_edit(
        content, old, "def f():\n    return 0"
    )

    logger.debug(f"{matcher = } {updated = }")
    assert matcher == "block-anchor"
    assert updated == "def f():\n    return 0\n"


def test_apply_edit_not_found():
    _updated, count, _matcher, error = er.apply_edit("abc\n", "zzz", "y")

    logger.debug(f"{error = }")
    assert count == 0
    assert "could not find" in error.lower()


def test_apply_edit_multiple_matches_refused():
    _updated, count, _matcher, error = er.apply_edit("hit\nhit\n", "hit", "x")

    logger.debug(f"{error = }")
    assert count == 0
    assert "multiple" in error.lower()


def test_apply_edit_replace_all():
    updated, count, _matcher, _error = er.apply_edit(
        "hit\nhit\nhit\n", "hit", "x", replace_all=True
    )

    logger.debug(f"{updated = } {count = }")
    assert count == 3
    assert updated == "x\nx\nx\n"


def test_apply_edit_empty_and_identical():
    _, _, _, empty_error = er.apply_edit("abc", "", "x")
    assert "must not be empty" in empty_error

    _, _, _, same_error = er.apply_edit("abc", "a", "a")
    assert "identical" in same_error


def test_apply_edit_guard_skips_fuzzy_on_large_file(monkeypatch):
    content = "def f():\n    x = 1\n    return x\n"
    monkeypatch.setattr(er, "MAX_MATCH_LINES", 2)

    _updated, count, _matcher, error = er.apply_edit(
        content, "x = 1\nreturn x", "    x = 2\n    return x"
    )

    logger.debug(f"{error = }")
    assert count == 0
    assert "could not find" in error.lower()


def test_apply_edit_guard_keeps_exact_on_large_file(monkeypatch):
    monkeypatch.setattr(er, "MAX_MATCH_LINES", 2)

    updated, _count, matcher, error = er.apply_edit("a\nb\n", "b", "B")

    logger.debug(f"{updated = } {matcher = }")
    assert error == ""
    assert matcher == "exact"
    assert updated == "a\nB\n"
