"""
Klea Agent NiceGUI web frontend.

The agent app composes its page from the shared
``klea_utils.ui.web.nicegui.components`` (ADR-0031): ``page.py`` is the
composition, ``app.py`` is the process entry point.  Agent-specific UI
(the mode selector + resolved-mode badge from :mod:`mode_ui`) slots
into the shared components here.

File: klea_agent/ui/web/__init__.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""
