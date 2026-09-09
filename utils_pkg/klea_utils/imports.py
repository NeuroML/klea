#!/usr/bin/env python3
"""
Helpers for optional-dependency import checks.

File: klea_utils/imports.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import importlib.util
import logging

logger = logging.getLogger(__name__)


def require_extra(
    import_names: str | list[str],
    extra: str,
    dist: str = "klea_utils",
) -> None:
    """Raise a helpful ``ImportError`` when optional imports are missing.

    Checks each *import_names* entry with :func:`importlib.util.find_spec`
    (no module code is executed) and, if any are not findable, raises a
    single ``ImportError`` that tells the user how to install the Klea
    ``[extra]`` (``pip install klea_utils[extra]``).  The message is
    extra-level only -- the caller passes the *import* names (e.g. ``"bs4"``,
    not ``"beautifulsoup4"``) but the fix is the extra, which is the
    user-facing knob in ``setup.cfg``.

    Accepts a single import name or a list so callers can batch
    ``["bs4", "anydoc"]`` into one call and get one combined hint instead
    of N separate ``try`` blocks.

    :param import_names: Import name or list of import names to check
        (e.g. ``"bs4"`` or ``["bs4", "anydoc"]``).
    :param extra: Extra name in ``setup.cfg`` (e.g. ``"mcp"``, ``"chroma"``).
    :param dist: Distribution name that owns the extra (default ``"klea_utils"``).
    :raises ImportError: when any of *import_names* is not findable.
    """
    if isinstance(import_names, str):
        names = [import_names]
    else:
        names = list(import_names)

    missing = [m for m in names if importlib.util.find_spec(m) is None]
    if not missing:
        logger.debug(f"Optional imports present for [{extra}]: {names}")
        return

    logger.debug(f"Optional imports missing for [{extra}]: {missing}")

    if len(missing) == 1:
        msg = (
            f"{missing[0]} is required for the [{extra}] feature. "
            f"Install with: pip install {dist}[{extra}]"
        )
    else:
        msg = (
            f"{', '.join(missing)} are required for the [{extra}] feature. "
            f"Install with: pip install {dist}[{extra}]"
        )
    raise ImportError(msg) from None
