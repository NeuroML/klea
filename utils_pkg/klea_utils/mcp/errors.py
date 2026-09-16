#!/usr/bin/env python3
"""
Custom error classes for the MCP tooling.

File: klea_utils/mcp/errors.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""


class PermissionDeniedError(PermissionError):
    """Raised when a tool is denied access to a path."""


class DocumentConversionError(Exception):
    """Raised when a document file cannot be converted to text.

    Carries a user-facing message that tools report through their ``error``
    result field.
    """


class FileEditError(Exception):
    """Raised when a file cannot be read or written by the editing tools.

    Carries a user-facing message that the write/edit tool implementations
    report through their ``error`` result field (e.g. a missing file, a
    binary or non-UTF-8 file, or an atomic-write failure).
    """
