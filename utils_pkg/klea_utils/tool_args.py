#!/usr/bin/env python3
"""
Shared helpers for classifying tool argument names.

The checks are name-based, not value-based: an argument named ``path`` or
``working_directory`` is path-like regardless of its contents, while free-text
arguments such as ``content`` or ``command`` are not.  Callers that need to
look at values can layer their own heuristics on top.

File: klea_utils/tool_args.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
import re

logger = logging.getLogger(__name__)


#: Argument-name words that mark a value as filesystem-path-like.  Names are
#: split on snake_case/kebab-case/whitespace, so ``working_directory`` and
#: ``input_file`` match via ``directory`` and ``file`` respectively.
PATH_ARGUMENT_NAME_WORDS = frozenset(
    {
        "path",
        "paths",
        "file",
        "files",
        "filename",
        "filepath",
        "dir",
        "dirs",
        "folder",
        "directory",
        "root",
        "cwd",
        "workdir",
    }
)

#: Argument-name words that mark a value as a URI/URL reference.
URI_ARGUMENT_NAME_WORDS = frozenset(
    {
        "url",
        "urls",
        "uri",
        "link",
        "href",
    }
)


def _name_parts(name: str) -> list[str]:
    """Split an argument name into lower-case words."""
    return [part for part in re.split(r"[_\-\s]+", name.lower()) if part]


#: Name words that mark an argument as carrying a shell command line.
_COMMAND_NAME_WORDS = frozenset({"command", "cmd", "shell", "script", "argv", "args"})


def is_command_argument_name(name: str) -> bool:
    """Return whether an argument name looks like a shell command argument.

    Used by value-based path discovery: only such an argument is shell-split,
    so a file body or a note is never chopped into pseudo shell tokens.

    :param name: The argument name.
    :returns: ``True`` when the name looks command-like.
    """
    return any(part in _COMMAND_NAME_WORDS for part in _name_parts(name))


def is_path_argument_name(name: str) -> bool:
    """Return whether *name* looks like it holds a filesystem path."""
    return any(part in PATH_ARGUMENT_NAME_WORDS for part in _name_parts(name))


def is_uri_argument_name(name: str) -> bool:
    """Return whether *name* looks like it holds a URI/URL."""
    return any(part in URI_ARGUMENT_NAME_WORDS for part in _name_parts(name))
