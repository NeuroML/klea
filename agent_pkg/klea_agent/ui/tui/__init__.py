"""
Klea Agent Textual TUI frontend.

The agent app composes its terminal UI from the shared
``klea_utils.ui.tui`` components (ADR-0050): ``page.py`` builds the context
and launches the shared app.  Agent-specific UI (the mode/access selectors,
ADR-0030/0037) slots into the shared components here.

File: klea_agent/ui/tui/__init__.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""
