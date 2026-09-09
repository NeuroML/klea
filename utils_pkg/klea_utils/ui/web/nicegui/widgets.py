"""
Reusable custom NiceGUI widgets for Klea web interfaces.

The widgets now live in ``klea_utils.ui.web.nicegui.components``
(ADR-0031); this module re-exports them so existing imports keep
working.

File: klea_utils/ui/web/nicegui/widgets.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from .components.chat_bubble import ChatBubble

__all__ = ["ChatBubble"]
