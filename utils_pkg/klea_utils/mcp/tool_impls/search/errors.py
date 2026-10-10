#!/usr/bin/env python3
"""
Errors for the web-search provider layer.

File: klea_utils/mcp/tool_impls/search/errors.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""


class SearchProviderError(Exception):
    """Raised by a provider adapter when a search request fails.

    The resolver catches this (and any other exception) and falls back to
    the next provider in the pool.
    """
