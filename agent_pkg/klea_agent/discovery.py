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


def refresh_project_files(
    discovery: Discovery, project_root: str | Path | None = None
) -> Discovery:
    """Upsert the project instruction file into *discovery*.

    Reads the first present file of :data:`PROJECT_INSTRUCTION_FILES` from
    *project_root* (default: the current working directory) and stores its
    text under an item named after the file.  The read is skipped when that
    item's source file mtime is unchanged, so a warm session does no repeated
    IO.

    :param discovery: The session's current discovery (not mutated).
    :param project_root: Directory holding the instruction file; defaults to
        ``Path.cwd()``.
    :returns: A discovery with the instruction file upserted, or *discovery*
        unchanged when no file exists or it cannot be read.
    """
    root = Path(project_root) if project_root is not None else Path.cwd()
    source = next(
        (name for name in PROJECT_INSTRUCTION_FILES if (root / name).is_file()),
        None,
    )
    if source is None:
        logger.debug(f"no project instruction file in {root}")
        return discovery

    path = root / source
    mtime = int(path.stat().st_mtime)
    if discovery.timestamp == mtime and any(
        item.source == source for item in discovery.items
    ):
        return discovery

    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("could not read project instruction file %s: %s", path, e)
        return discovery

    updated = discovery.model_copy(deep=True)
    updated.upsert(source, content)
    updated.timestamp = mtime
    logger.debug(f"discovery: loaded {source = }\n{len(content) = }\n{mtime = }")
    return updated
