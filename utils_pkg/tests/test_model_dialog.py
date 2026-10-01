"""Tests for the model configuration dialog helpers.

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail dot com>
"""

from klea_utils.ui.web.nicegui.components.model_dialog import _role_label


def test_role_label_formats_underscored_keys():
    """Underscored role keys render as readable tab labels."""
    assert _role_label("tool_picker") == "Tool picker"
    assert _role_label("chat") == "Chat"
    assert _role_label("embedding") == "Embedding"
