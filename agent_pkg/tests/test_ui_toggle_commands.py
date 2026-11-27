#!/usr/bin/env python3
# Copyright (c) 2025 Klea. All rights reserved.
"""Tests that the web and TUI catalogues expose the three toggle commands."""

from klea_utils.commands.toggles import TOGGLE_COMMANDS
from klea_utils.ui.tui.components.commands import (
    TUI_CAPABILITIES,
    CommandSuggestions,
    handle_command,
)
from klea_utils.ui.tui.components.commands import (
    build_catalogue as build_tui_catalogue,
)
from klea_utils.ui.web.nicegui.components.commands import (
    WEB_CAPABILITIES,
)
from klea_utils.ui.web.nicegui.components.commands import (
    build_catalogue as build_web_catalogue,
)

TOGGLE_NAMES = {"pathcheck", "pathcheck-stance", "hitl"}

EXPECTED_TOKENS = {
    "pathcheck": ("strict", "declared", "off"),
    "pathcheck-stance": ("deny", "allow"),
    "hitl": ("on", "off"),
}

TOGGLE_METADATA = [command.metadata() for command in TOGGLE_COMMANDS]


def _web_catalogue():
    return build_web_catalogue(TOGGLE_METADATA)


def _tui_catalogue():
    return build_tui_catalogue(TOGGLE_METADATA)


def _available(catalogue, capabilities):
    return {command.name: command for command in catalogue.available(capabilities)}


def _backend_hints():
    return {command.name: command.arg_hint for command in TOGGLE_COMMANDS}


def _assert_surfaced(available):
    assert TOGGLE_NAMES <= set(available)
    hints = _backend_hints()
    for name in sorted(TOGGLE_NAMES):
        command = available[name]
        assert command.arg_hint == hints[name]
        for token in EXPECTED_TOKENS[name]:
            assert token in command.arg_hint


def test_web_catalogue_exposes_toggle_commands():
    _assert_surfaced(_available(_web_catalogue(), WEB_CAPABILITIES))


def test_tui_catalogue_exposes_toggle_commands():
    _assert_surfaced(_available(_tui_catalogue(), TUI_CAPABILITIES))


def test_tui_suggestions_match_pathcheck():
    matches = CommandSuggestions.matches("/pathcheck", _tui_catalogue())
    names = {getattr(match, "name", match) for match in matches}
    assert "pathcheck" in names


def test_tui_forwards_hitl_off_to_server():
    assert handle_command("/hitl off", _tui_catalogue()) is None
