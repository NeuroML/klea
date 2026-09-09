"""
Reusable NiceGUI page components for Klea frontends.

Each component lives in its own module and stays small and specific.
Components are *not* a page: they attach into a shared
:class:`~klea_utils.ui.web.nicegui.components.context.PageContext` and
register their element references and cross-component callbacks there.
Each app's ``ui.web`` package does the page composition (ADR-0031): it
creates one context, attaches the components it needs, and lets
handlers resolve references at event time.

Components never encode a single app's API contract or page layout.

File: klea_utils/ui/web/nicegui/components/__init__.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""
