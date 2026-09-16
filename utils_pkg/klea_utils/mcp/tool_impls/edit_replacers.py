#!/usr/bin/env python3
"""
Replacer chain for the search/replace edit tool (ADR-0039).

A replacer takes the file content and the model's ``old_string`` and yields
candidate substrings of the *actual* content that are meant to match.  The
chain is tried in order, simplest first, so an exact match always wins and
fuzzy matching is only reached when exact matching fails.  Every candidate is
subject to the uniqueness and proportional-match checks in :func:`apply_edit`.

The matchers are adapted from opencode's ``tool/edit.ts`` (which in turn
credits Cline and the Gemini CLI).

File: klea_utils/mcp/tool_impls/edit_replacers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import bisect
import difflib
import logging
import re
from collections.abc import Callable, Iterator

logger = logging.getLogger(__name__)

#: A replacer: ``(content, old_string) -> candidate spans of content``.
Replacer = Callable[[str, str], Iterator[str]]

#: Similarity threshold for a single block-anchor candidate.
SINGLE_CANDIDATE_SIMILARITY = 0.65

#: Similarity threshold for the best of several block-anchor candidates.
MULTIPLE_CANDIDATES_SIMILARITY = 0.65

#: Above this many lines, only the exact matcher is tried.  The fallback
#: matchers are Python-level and cost O(lines x old_string lines); bounding
#: them keeps an edit on a very large file from stalling the server.  Exact
#: matching is C-level so it stays available.
MAX_MATCH_LINES = 20_000


def simple_replacer(content: str, old_string: str) -> Iterator[str]:
    """Yield *old_string* unchanged (exact match).

    :param content: File content (LF-normalised).
    :param old_string: Span to find.
    :yields: ``old_string``.
    """
    yield old_string


def line_trimmed_replacer(content: str, old_string: str) -> Iterator[str]:
    """Yield blocks whose lines match *old_string*'s lines when trimmed.

    Fixes blocks that were re-indented or had per-line whitespace changed.

    :param content: File content (LF-normalised).
    :param old_string: Span to find.
    :yields: Matching blocks as they appear in *content*.
    """
    original_lines = content.split("\n")
    search_lines = old_string.split("\n")
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    count = len(search_lines)
    if count == 0:
        return
    for i in range(len(original_lines) - count + 1):
        if all(
            original_lines[i + j].strip() == search_lines[j].strip()
            for j in range(count)
        ):
            yield "\n".join(original_lines[i : i + count])


def _line_similarity(a: str, b: str) -> float:
    """Return a similarity ratio in ``[0, 1]`` between two lines.

    Uses :class:`difflib.SequenceMatcher` (``autojunk`` disabled for
    deterministic results on long lines).

    :param a: First line.
    :param b: Second line.
    :returns: Similarity ratio; 0 for no common content, 1 for identical.
    """
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def block_anchor_replacer(content: str, old_string: str) -> Iterator[str]:
    """Yield blocks anchored on first/last line with similar middle lines.

    Recovers an edit when the model's ``old_string`` is mostly right but one
    or more middle lines drifted slightly (a typo, a changed value), so exact
    and line-trimmed matching both fail.

    How it works:

    1. The block's first and last trimmed lines are treated as distinctive,
       correct *anchors*; only blocks of at least three lines (anchors plus at
       least one middle) are considered.
    2. Candidate blocks are found by pairing each content line equal to the
       first anchor with the *next* content line (at or after ``i + 2``) equal
       to the last anchor.  A pair is kept only when its length is within 25%
       of the search block's length, so a nearby same-anchor block is not
       mistaken for the target.
    3. Each candidate is scored by the average per-line similarity of its
       middle lines against the search block's middle lines
       (:class:`difflib.SequenceMatcher`).  A lone candidate must clear
       :data:`SINGLE_CANDIDATE_SIMILARITY`; with several, the best-scoring
       candidate wins if it clears :data:`MULTIPLE_CANDIDATES_SIMILARITY`.

    Only the candidate's *location* comes from the anchors: the text yielded
    is the file's real block, so the replacement preserves the file's actual
    indentation and content.

    :param content: File content (LF-normalised).
    :param old_string: Span to find.
    :yields: The best matching block, if any.
    """
    original_lines = content.split("\n")
    search_lines = old_string.split("\n")
    # A trailing newline leaves a final empty element; drop it so it does not
    # count as an extra (last) line.
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    size = len(search_lines)
    # Need first anchor + at least one middle line + last anchor.
    if size < 3:
        return

    first_line = search_lines[0].strip()
    last_line = search_lines[-1].strip()
    # A candidate block may differ in length from the search block (a changed
    # line can add or remove a line), so allow up to a quarter of its size.
    max_delta = max(1, int(size * 0.25))

    # Every content line that could close a candidate block.  Built with
    # enumerate, so the indices are ascending and safe to bisect.
    last_positions = [
        idx for idx, line in enumerate(original_lines) if line.strip() == last_line
    ]
    candidates: list[tuple[int, int]] = []
    for i in range(len(original_lines)):
        # Candidate start: a line matching the first anchor.
        if original_lines[i].strip() != first_line:
            continue
        # Candidate end: the next last-anchor line after at least one middle
        # line (start at i+2).  bisect returns the first stored position >=
        # i+2, so we never scan the lines in between.
        position = bisect.bisect_left(last_positions, i + 2)
        if position >= len(last_positions):
            continue
        j = last_positions[position]
        actual = j - i + 1
        # Discard implausible lengths (too close to or far from the target).
        if abs(actual - size) <= max_delta:
            candidates.append((i, j))
    if not candidates:
        return

    def _similarity(start: int, end: int) -> float:
        """Average middle-line similarity for the block ``start..end``."""
        actual = end - start + 1
        # Compare only the lines that are middle lines in both blocks.
        lines_to_check = min(size - 2, actual - 2)
        if lines_to_check <= 0:
            return 1.0
        total = 0.0
        for k in range(1, min(size - 1, actual - 1)):
            original_line = original_lines[start + k].strip()
            search_line = search_lines[k].strip()
            total += _line_similarity(original_line, search_line)
        return total / lines_to_check

    # A single location must still resemble the intended block, otherwise a
    # coincidental anchor pair (e.g. two repeated headers) would be replaced.
    if len(candidates) == 1:
        start, end = candidates[0]
        if _similarity(start, end) >= SINGLE_CANDIDATE_SIMILARITY:
            yield "\n".join(original_lines[start : end + 1])
        return

    # Several locations: keep the one whose middle lines are most similar.
    best: tuple[int, int] | None = None
    best_similarity = -1.0
    for start, end in candidates:
        similarity = _similarity(start, end)
        if similarity > best_similarity:
            best_similarity = similarity
            best = (start, end)
    if best is not None and best_similarity >= MULTIPLE_CANDIDATES_SIMILARITY:
        yield "\n".join(original_lines[best[0] : best[1] + 1])


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs in *text* to single spaces.

    :param text: Text to normalise.
    :returns: Whitespace-normalised text.
    """
    return " ".join(text.split())


def whitespace_normalized_replacer(content: str, old_string: str) -> Iterator[str]:
    """Yield spans matching *old_string* after whitespace collapsing.

    Fixes tab/space and repeated-space differences on single or multiple
    lines.

    :param content: File content (LF-normalised).
    :param old_string: Span to find.
    :yields: Matching spans as they appear in *content*.
    """
    normalized_find = _normalize_whitespace(old_string)
    if not normalized_find:
        return
    lines = content.split("\n")
    for line in lines:
        normalized_line = _normalize_whitespace(line)
        if normalized_line == normalized_find:
            yield line
        elif normalized_find in normalized_line:
            words = old_string.split()
            if words:
                pattern = r"\s+".join(re.escape(word) for word in words)
                match = re.search(pattern, line)
                if match:
                    yield match.group(0)

    find_lines = old_string.split("\n")
    if len(find_lines) > 1:
        for i in range(len(lines) - len(find_lines) + 1):
            block_lines = lines[i : i + len(find_lines)]
            if _normalize_whitespace("\n".join(block_lines)) == normalized_find:
                yield "\n".join(block_lines)


def _remove_indentation(text: str) -> str:
    """Remove the common minimum indentation from *text*'s lines.

    :param text: Text to dedent.
    :returns: Dedented text (non-empty lines only).
    """
    lines = text.split("\n")
    non_empty = [line for line in lines if line.strip()]
    if not non_empty:
        return text
    min_indent = min(len(line) - len(line.lstrip()) for line in non_empty)
    return "\n".join(line if not line.strip() else line[min_indent:] for line in lines)


def indentation_flexible_replacer(content: str, old_string: str) -> Iterator[str]:
    """Yield blocks matching *old_string* once common indentation is removed.

    Fixes a block that was shifted left or right as a whole.

    :param content: File content (LF-normalised).
    :param old_string: Span to find.
    :yields: Matching blocks as they appear in *content*.
    """
    normalized_find = _remove_indentation(old_string)
    lines = content.split("\n")
    find_count = len(old_string.split("\n"))
    for i in range(len(lines) - find_count + 1):
        block = "\n".join(lines[i : i + find_count])
        if _remove_indentation(block) == normalized_find:
            yield block


def context_aware_replacer(content: str, old_string: str) -> Iterator[str]:
    """Yield blocks whose anchors match and whose middle lines mostly match.

    A complementary fallback to :func:`block_anchor_replacer`, not simply a
    looser version of it.  Both use the first and last trimmed lines as
    anchors, but they accept different kinds of drift:

    * :func:`block_anchor_replacer` lets the block *length* differ (by up to
      25%) and scores every middle line by fuzzy similarity.  It fits a block
      where each middle line is slightly wrong (for example a typo per line).
    * this replacer requires the *exact* same number of lines and at least
      half of the middle lines to match *exactly*.  It fits a block where some
      middle lines are entirely different or replaced, but the surrounding
      context (and the anchors) is intact.

    Neither subsumes the other: a block whose middle lines are all ~80%
    similar scores above the anchor threshold but has almost no exact matches,
    while a block that is half exact and half unrelated averages ~0.5
    similarity (below the anchor threshold) yet passes the half-exact rule
    here.

    How it works: ``old_string`` must have at least three lines.  For each
    line matching the first anchor, the next last-anchor line at or after
    ``i + 2`` is located; because the block length must equal the search
    length, that hit only counts when it is exactly at ``i + size - 1``.  The
    middle lines are compared for exact equality (after trimming) and the
    block is yielded when at least half of the comparable middle lines match
    (or when there are none to compare).

    Unlike :func:`block_anchor_replacer`, which returns the single best
    candidate, this yields every qualifying block; the uniqueness check in
    :func:`apply_edit` then decides whether the edit is safe.

    :param content: File content (LF-normalised).
    :param old_string: Span to find.
    :yields: Matching blocks as they appear in *content*.
    """
    search_lines = old_string.split("\n")
    # A trailing newline leaves a final empty element; drop it so it does not
    # inflate the required block length.
    if search_lines and search_lines[-1] == "":
        search_lines.pop()
    size = len(search_lines)
    # Need first anchor + at least one middle line + last anchor.
    if size < 3:
        return
    original_lines = content.split("\n")
    first_line = search_lines[0].strip()
    last_line = search_lines[-1].strip()

    # Every content line that could close a block; ascending by construction.
    last_positions = [
        idx for idx, line in enumerate(original_lines) if line.strip() == last_line
    ]
    for i in range(len(original_lines)):
        # Candidate start: a line matching the first anchor.
        if original_lines[i].strip() != first_line:
            continue
        # Only the first last-anchor hit at/after i+2 is considered, matching
        # the sequential scan; because the block length must equal the search
        # length, that hit has to be exactly at i+size-1.
        position = bisect.bisect_left(last_positions, i + 2)
        if position >= len(last_positions):
            continue
        j = last_positions[position]
        block = original_lines[i : j + 1]
        # Length must match exactly here (unlike the anchor matcher).
        if len(block) == size:
            matching = 0
            total = 0
            for k in range(1, len(block) - 1):
                # Compare the middle lines exactly, after trimming.  Blank
                # pairs are ignored so padding does not count either way.
                block_line = block[k].strip()
                search_line = search_lines[k].strip()
                if block_line or search_line:
                    total += 1
                    if block_line == search_line:
                        matching += 1
            # Accept when at least half the comparable middle lines are exact
            # (or there is nothing to compare).
            if total == 0 or matching / total >= 0.5:
                yield "\n".join(block)


#: The replacer chain, tried in order.
REPLACERS: tuple[tuple[str, Replacer], ...] = (
    ("exact", simple_replacer),
    ("line-trimmed", line_trimmed_replacer),
    ("block-anchor", block_anchor_replacer),
    ("whitespace-normalised", whitespace_normalized_replacer),
    ("indentation-flexible", indentation_flexible_replacer),
    ("context-aware", context_aware_replacer),
)


def is_disproportionate_match(span: str, old_string: str) -> bool:
    """Return whether *span* is far larger than *old_string*.

    Guards against a loose replacer selecting a much larger region than the
    model asked to replace.

    :param span: Candidate span found in the content.
    :param old_string: The model's requested span.
    :returns: ``True`` when the match should be refused.
    """
    old_lines = old_string.count("\n") + 1
    span_lines = span.count("\n") + 1
    if span_lines >= max(old_lines + 3, old_lines * 2):
        return True
    if old_lines == 1:
        return False
    return len(span.strip()) > max(
        len(old_string.strip()) + 500, len(old_string.strip()) * 4
    )


def apply_edit(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool = False,
) -> tuple[str, int, str, str]:
    """Apply *old_string* -> *new_string* using the replacer chain.

    Returns the updated content, the number of replacements, the name of the
    replacer that matched, and an error message (empty on success).

    :param content: File content (LF-normalised).
    :param old_string: Span to replace (LF-normalised).
    :param new_string: Replacement span (LF-normalised).
    :param replace_all: Replace every occurrence of the matched span.
    :returns: ``(updated, replacements, matcher, error)``.
    """
    logger.debug(
        f"Applying edit\n"
        f"{len(content) = }\n"
        f"{len(old_string) = }\n"
        f"{len(new_string) = }\n"
        f"{replace_all = }"
    )
    if old_string == "":
        return (
            content,
            0,
            "",
            (
                "old_string must not be empty; use write_file to create or "
                "overwrite a file."
            ),
        )
    if old_string == new_string:
        return content, 0, "", "old_string and new_string are identical."

    line_count = content.count("\n") + 1
    chain = REPLACERS
    if line_count > MAX_MATCH_LINES:
        logger.warning(
            f"File has {line_count} lines (limit {MAX_MATCH_LINES}); "
            "using exact matching only"
        )
        chain = REPLACERS[:1]

    found_any = False
    for matcher, replacer in chain:
        for span in replacer(content, old_string):
            index = content.find(span)
            if index == -1:
                continue
            found_any = True
            if is_disproportionate_match(span, old_string):
                logger.warning(
                    f"Refusing disproportionate match\n{len(span) = }\n"
                    f"{len(old_string) = }\n{matcher = }"
                )
                return (
                    content,
                    0,
                    "",
                    (
                        "Refusing replacement because the matched span is much "
                        "larger than old_string. Re-read the file and provide the "
                        "full exact span."
                    ),
                )
            if replace_all:
                count = content.count(span)
                logger.debug(f"Replacing all {count} matches via {matcher}")
                return content.replace(span, new_string), count, matcher, ""
            if content.find(span) != content.rfind(span):
                # Not unique; try the next candidate or replacer.
                logger.debug(
                    f"Skipping non-unique candidate\n{matcher = }\n{len(span) = }"
                )
                continue
            logger.debug(f"Replacing via {matcher}")
            updated = content[:index] + new_string + content[index + len(span) :]
            return updated, 1, matcher, ""

    if not found_any:
        logger.debug(f"No replacer matched\n{len(old_string) = }")
        return (
            content,
            0,
            "",
            (
                "Could not find old_string in the file. It must match exactly, "
                "including whitespace and indentation."
            ),
        )
    logger.debug(f"Matched but not unique; refusing\n{len(old_string) = }")
    return (
        content,
        0,
        "",
        (
            "Found multiple matches for old_string. Provide more surrounding "
            "context to make it unique, or set replace_all."
        ),
    )
