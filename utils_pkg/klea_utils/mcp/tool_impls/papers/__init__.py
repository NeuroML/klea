#!/usr/bin/env python3
"""
Academic paper search sources (OpenAlex, Crossref, Semantic Scholar,
Europe PMC, PubMed, arXiv).

Framework-agnostic functions that search scholarly APIs and return
normalised :class:`~klea_utils.mcp.tool_impls.papers.record.PaperRecord`
objects.  The entry point is
:func:`klea_utils.mcp.tool_impls.papers.search.search_papers`.
"""
