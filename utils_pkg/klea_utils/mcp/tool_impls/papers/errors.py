#!/usr/bin/env python3
"""
Errors for the academic paper search layer.

File: klea_utils/mcp/tool_impls/papers/errors.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""


class PaperSourceError(Exception):
    """Raised when a paper source cannot be queried (HTTP error, 429, bad body).

    The search falls back to the next source when this is raised.
    """
