#!/usr/bin/env python3
"""
Sphinx documentation configuration

File: conf.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

# Configuration file for the Sphinx documentation builder.
project = "Klea"
copyright = "2026, NeuroML contributors"
author = "NeuroML contributors"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "sphinxcontrib.typer",
]

# fastmcp/mcp still fail to import inside Sphinx with pydantic 2.13:
# models using ``model_config = {"extra": "allow"}`` raise
# ``PydanticSchemaGenerationError: The type annotation for
# __pydantic_extra__ must be dict[str, ...]`` (they import fine outside
# Sphinx).  langchain/langchain_core/langgraph import cleanly and are no
# longer mocked.
autodoc_mock_imports = [
    "mcp",
    "fastmcp",
]

templates_path = ["_templates"]
exclude_patterns = ["_build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
html_title = "Klea"
html_logo = "_static/klea-logo.png"
html_favicon = "_static/klea-logo-notext.png"
html_show_sphinx = False

# Per-page "Edit this page" / "View this page" links pointing at the GitHub
# source. These work on GitHub Pages (not just ReadTheDocs) because Furo's
# basic-ng source-link macro reads source_repository/source_branch directly.
html_theme_options = {
    "source_repository": "https://github.com/NeuroML/klea",
    "source_branch": "development",
    "source_directory": "docs",
    # The logo is a wordmark that already reads "Klea", so hide the
    # duplicated project name text in the sidebar header.
    "sidebar_hide_name": True,
}


intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}
