#!/usr/bin/env python3
"""
Shared filesystem helpers for the file-editing tools.

File: klea_utils/mcp/tool_impls/file_ops.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import contextlib
import difflib
import logging
import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from klea_utils.mcp.errors import FileEditError

logger = logging.getLogger(__name__)

#: Byte-order mark, preserved when present.
BOM = "\ufeff"

#: Default cap on the size of a file the editing tools will read or write.
DEFAULT_MAX_BYTES = 100 * 1024 * 1024

#: Fallback mode for newly created files when the target did not exist.  The
#: process umask is applied to ``0o666`` once at import (the two-call read is
#: the only portable way to query it; the window where the umask is 0 is two
#: C calls and runs at import time).
_umask = os.umask(0)
os.umask(_umask)
DEFAULT_FILE_MODE = 0o666 & ~_umask


@dataclass(frozen=True)
class TextFile:
    """A text file's content plus the write-time properties to preserve."""

    #: File text with any leading BOM stripped.
    text: str
    #: Whether the file began with a UTF-8 BOM (re-applied on write).
    bom: bool
    #: Dominant line ending, LF or CRLF; re-applied on write.
    newline: str
    #: ``st_mode`` captured at read time, used to preserve the file mode.
    mode: int


def detect_newline(text: str) -> str:
    """Return the dominant line ending in *text*.

    :param text: Text to inspect.
    :returns: ``"\\r\\n"`` when the text contains CRLF, else ``"\\n"``.
    """
    return "\r\n" if "\r\n" in text else "\n"


def normalize_newlines(text: str) -> str:
    """Return *text* with CRLF endings converted to LF.

    :param text: Text to normalise.
    :returns: Text with ``\\r\\n`` replaced by ``\\n``.
    """
    return text.replace("\r\n", "\n")


def apply_newline(text: str, newline: str) -> str:
    """Re-apply *newline* to LF-normalised *text*.

    :param text: LF-only text.
    :param newline: Target line ending (``"\\n"`` or ``"\\r\\n"``).
    :returns: Text with *newline* endings.
    """
    if newline == "\n":
        return text
    return text.replace("\n", newline)


def split_bom(text: str) -> tuple[str, bool]:
    """Split a leading BOM off *text*.

    :param text: Decoded text that may start with a BOM.
    :returns: ``(text_without_bom, had_bom)``.
    """
    if text.startswith(BOM):
        return text[len(BOM) :], True
    return text, False


def join_bom(text: str, bom: bool) -> str:
    """Prefix *text* with a BOM when *bom* is set.

    :param text: Text without a BOM.
    :param bom: Whether to add the BOM.
    :returns: Text, optionally BOM-prefixed.
    """
    return BOM + text if bom else text


def is_binary(data: bytes) -> bool:
    """Return whether *data* looks binary (contains a NUL byte).

    :param data: Raw file bytes.
    :returns: ``True`` when a NUL byte is present.
    """
    return b"\x00" in data


def read_whole_text(
    path: str | os.PathLike, *, max_bytes: int = DEFAULT_MAX_BYTES
) -> TextFile:
    """Read a UTF-8 text file, preserving BOM, newline and mode.

    :param path: File to read.
    :param max_bytes: Maximum size accepted.
    :raises FileEditError: when the path is missing, not a regular file, too
        large, binary, not valid UTF-8, or unreadable.
    :returns: A :class:`TextFile` with normalised metadata.
    """
    the_path = Path(path)
    if not the_path.exists():
        raise FileEditError(f"File not found: {the_path}")
    if not the_path.is_file():
        raise FileEditError(f"Not a regular file: {the_path}")

    try:
        stat_result = the_path.stat()
    except OSError as exc:
        raise FileEditError(f"Could not stat file: {exc}") from exc

    if stat_result.st_size > max_bytes:
        raise FileEditError(
            f"File too large to edit: {stat_result.st_size} bytes (limit {max_bytes})"
        )

    try:
        data = the_path.read_bytes()
    except OSError as exc:
        raise FileEditError(f"Could not read file: {exc}") from exc

    if is_binary(data):
        raise FileEditError(f"Cannot edit binary file: {the_path}")

    try:
        decoded = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise FileEditError(f"File is not valid UTF-8 text: {the_path}") from exc

    text, bom = split_bom(decoded)
    return TextFile(
        text=text,
        bom=bom,
        newline=detect_newline(text),
        mode=stat_result.st_mode,
    )


def write_whole_text(
    path: str | os.PathLike,
    text: str,
    *,
    bom: bool = False,
    newline: str = "\n",
    mode: int | None = None,
    create_parents: bool = True,
) -> None:
    """Atomically write *text* to *path*, preserving BOM/newline/mode.

    The content is written to a temporary file in the target directory and
    ``os.replace``d into place, so a failure leaves any existing file
    untouched.  An existing file's mode is preserved when *mode* is ``None``;
    a new file gets :data:`DEFAULT_FILE_MODE`.

    :param path: Destination file.
    :param text: Full file content (LF endings).
    :param bom: Whether to prefix the UTF-8 BOM.
    :param newline: Line ending to apply (``"\\n"`` or ``"\\r\\n"``).
    :param mode: Octal mode to apply, or ``None`` to preserve/default.
    :param create_parents: Create missing parent directories.
    :raises FileEditError: when the destination is a directory or the write
        fails.
    """
    the_path = Path(path)
    if the_path.is_dir():
        raise FileEditError(f"Path is a directory: {the_path}")

    if create_parents:
        try:
            the_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise FileEditError(f"Could not create directory: {exc}") from exc

    if mode is None:
        if the_path.exists():
            with contextlib.suppress(OSError):
                mode = the_path.stat().st_mode
        if mode is None:
            mode = DEFAULT_FILE_MODE

    payload = join_bom(apply_newline(normalize_newlines(text), newline), bom)
    try:
        data = payload.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise FileEditError(f"Content is not valid UTF-8 text: {exc}") from exc

    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(the_path.parent), prefix=f".{the_path.name}.", suffix=".tmp"
        )
    except OSError as exc:
        raise FileEditError(f"Could not write file: {exc}") from exc

    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.chmod(tmp_name, stat.S_IMODE(mode))
        os.replace(tmp_name, the_path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise FileEditError(f"Could not write file: {exc}") from exc

    logger.debug(f"Wrote {the_path} ({len(data)} bytes, mode {oct(mode)})")


def unified_diff(old_text: str, new_text: str, path: str = "") -> str:
    """Return a unified diff between two texts.

    :param old_text: Original text.
    :param new_text: Updated text.
    :param path: Label used for both sides of the diff.
    :returns: Unified diff text (empty when the texts are identical).
    """
    diff = difflib.unified_diff(
        old_text.splitlines(keepends=True),
        new_text.splitlines(keepends=True),
        fromfile=path,
        tofile=path,
    )
    return "".join(diff)


def diff_counts(old_text: str, new_text: str) -> tuple[int, int]:
    """Return the number of added and removed lines between two texts.

    :param old_text: Original text.
    :param new_text: Updated text.
    :returns: ``(additions, deletions)``.
    """
    additions = 0
    deletions = 0
    matcher = difflib.SequenceMatcher(
        None, old_text.splitlines(), new_text.splitlines()
    )
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag in ("replace", "delete"):
            deletions += i2 - i1
        if tag in ("replace", "insert"):
            additions += j2 - j1
    return additions, deletions


#: Maximum size of the unified diff returned in a tool result.
MAX_DIFF_CHARS = 20_000


def diff_payload(old_text: str, new_text: str, path: str = "") -> tuple[str, int, int]:
    """Return ``(diff, additions, deletions)`` for a change.

    The diff is truncated to :data:`MAX_DIFF_CHARS`.

    :param old_text: Original text.
    :param new_text: Updated text.
    :param path: Label used for both sides of the diff.
    :returns: Unified diff text plus the added and removed line counts.
    """
    additions, deletions = diff_counts(old_text, new_text)
    diff = unified_diff(old_text, new_text, path=path)
    if len(diff) > MAX_DIFF_CHARS:
        diff = diff[:MAX_DIFF_CHARS] + "\n... (diff truncated)"
    return diff, additions, deletions
