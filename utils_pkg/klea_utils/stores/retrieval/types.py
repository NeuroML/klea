#!/usr/bin/env python3
"""
Shared types for the retriever managers

File: klea_utils/stores/retrieval/types.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

#: Current k for each store: ``{domain: {store: k}}``.  Stores not listed use
#: their default k.
KValues = dict[str, dict[str, int]]
