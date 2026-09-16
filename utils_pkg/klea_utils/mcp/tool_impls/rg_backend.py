#!/usr/bin/env python3
"""
ripgrep backend resolution for the read-only search tools.

File: klea_utils/mcp/tool_impls/rg_backend.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

import functools
import logging
from importlib.metadata import PackageNotFoundError, distribution
from pathlib import Path

logger = logging.getLogger(__name__)

#: Distribution that provides a pinned ripgrep binary.  Built and published
#: by Astral (https://astral.sh) from a pinned upstream ripgrep commit; see
#: the ``[search]`` extra in ``setup.cfg``.  We deliberately do not probe
#: ``PATH``: only this known, pinned artifact is used, so the read-only
#: guarantee of the search tools does not depend on the host environment.
RG_DISTRIBUTION = "astral-dev-toolchain-ripgrep"

#: Filenames the ripgrep executable can have, per platform.
_RG_EXECUTABLE_NAMES = frozenset({"rg", "rg.exe"})


@functools.lru_cache(maxsize=1)
def resolve_rg() -> str | None:
    """Return the path to the pinned ripgrep executable, or ``None``.

    Locates the binary via the installed :data:`RG_DISTRIBUTION`
    distribution's ``RECORD`` (``importlib.metadata``), so it works whether
    the wheel places the executable in the environment's scripts directory
    or ships it as package data.  No ``PATH`` lookup is performed.

    The result is cached for the process lifetime: the installed set of
    distributions does not change while the server runs.  Tests can reset it
    with ``resolve_rg.cache_clear()``.

    :returns: Absolute path to the executable when the distribution is
        installed and provides an ``rg``/``rg.exe`` file; ``None`` otherwise.
    """
    try:
        dist = distribution(RG_DISTRIBUTION)
    except PackageNotFoundError:
        logger.debug(
            f"{RG_DISTRIBUTION} is not installed; "
            "search tools will use the in-house walker"
        )
        return None

    for record in dist.files or []:
        if Path(str(record)).name.lower() not in _RG_EXECUTABLE_NAMES:
            continue
        path = Path(str(dist.locate_file(record)))
        if path.is_file():
            logger.debug(f"Resolved ripgrep executable: {path}")
            return str(path.resolve())
        logger.warning(f"ripgrep record {record} does not exist at {path}")

    logger.warning(
        f"{RG_DISTRIBUTION} {dist.version} is installed but provides no "
        "rg executable; search tools will use the in-house walker"
    )
    return None
