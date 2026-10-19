#!/usr/bin/env python3
"""
Project-context discovery for the Klea agent.

Gathers stable, project-wide context once per session (currently the
``AGENTS.md`` / ``CLAUDE.md`` instruction file) into the session-scoped
``Discovery`` state so it can be rendered into the Planner and answer-composer
prompts (ADR-0035).  The file is re-read only when it changes.

File: klea_agent/discovery.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from pathlib import Path

from klea_agent.schemas import Discovery

logger = logging.getLogger(__name__)

#: Project instruction files, in preference order (the first present wins).
PROJECT_INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")


def _drop_instruction_items(discovery: Discovery, keep: str | None) -> bool:
    """Remove instruction-file items other than *keep* (in place).

    A session's discovery is reconciled against the **current** project root,
    which the ``/cwd`` command can change.  Instruction items from a previous
    root must not linger: the resolved source replaces them, and a root with no
    instruction file clears them all.

    :param discovery: The discovery to prune (mutated).
    :param keep: The source to retain, or ``None`` to drop every instruction
        item.
    :returns: ``True`` when any item was removed.
    """
    kept = [
        item
        for item in discovery.items
        if item.source not in PROJECT_INSTRUCTION_FILES or item.source == keep
    ]
    if len(kept) == len(discovery.items):
        return False
    discovery.items = kept
    return True


def refresh_project_files(
    discovery: Discovery, project_root: str | Path | None = None
) -> Discovery:
    """Reconcile the project instruction file in *discovery*.

    Reads the first present file of :data:`PROJECT_INSTRUCTION_FILES` from
    *project_root* (default: the current working directory) and stores its text
    under an item named after the file.  The read is skipped when that item's
    source file mtime is unchanged, so a warm session does no repeated IO.

    The refresh is a reconciliation, not a pure upsert: the current root's
    instruction file is authoritative, so an earlier root's item (``AGENTS.md``
    vs ``CLAUDE.md``, or a root with no instruction file at all) is removed.
    This is what keeps ``/cwd`` from leaving the previous root's discovery in
    place.

    :param discovery: The session's current discovery (not mutated).
    :param project_root: Directory holding the instruction file; defaults to
        ``Path.cwd()``.
    :returns: The reconciled discovery; the *same* object when there is nothing
        to change (no file and no stale instruction item, or an unchanged
        mtime).
    """
    root = Path(project_root) if project_root is not None else Path.cwd()
    source = next(
        (name for name in PROJECT_INSTRUCTION_FILES if (root / name).is_file()),
        None,
    )

    if source is None:
        # No instruction file at the current root: any previous root's
        # instruction item is stale and must be cleared.
        stale = any(
            item.source in PROJECT_INSTRUCTION_FILES for item in discovery.items
        )
        if not stale and discovery.timestamp == 0:
            logger.debug(f"no project instruction file in {root}")
            return discovery
        updated = discovery.model_copy(deep=True)
        _drop_instruction_items(updated, keep=None)
        updated.timestamp = 0
        logger.debug(f"discovery: cleared stale instruction items for {root}")
        return updated

    path = root / source
    mtime = int(path.stat().st_mtime)
    has_item = any(item.source == source for item in discovery.items)
    has_stale = any(
        item.source in PROJECT_INSTRUCTION_FILES and item.source != source
        for item in discovery.items
    )
    if mtime == discovery.timestamp and has_item and not has_stale:
        return discovery

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("could not read project instruction file %s: %s", path, e)
        return discovery

    updated = discovery.model_copy(deep=True)
    _drop_instruction_items(updated, keep=source)
    updated.upsert(source, content)
    updated.timestamp = mtime
    logger.debug(f"discovery: loaded {source = }\n{len(content) = }\n{mtime = }")
    return updated
