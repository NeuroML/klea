#!/usr/bin/env python3
"""
Compact single-choice control for the NiceGUI status pane.

A ``q-btn-group`` (connected buttons on one line) is used rather than
``ui.toggle`` because each option can carry its own active colour (for
example destructive = red, scientific = green), which a ``q-btn-toggle``
cannot express.  Labels and button groups use fixed widths so that stacked
controls line up regardless of their text lengths.

File: klea_utils/ui/web/nicegui/components/choice.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from collections.abc import Callable

from nicegui import ui

#: Fixed width for the row label (fits the longest expected label, e.g.
#: ``"Access:"``), so labels of different lengths stay aligned.
DEFAULT_LABEL_WIDTH = "7ch"

#: Fixed width for the button group, so controls with different option
#: lengths (e.g. ``General | Scientific`` vs ``Full | Read-only``) match.
DEFAULT_GROUP_WIDTH = "10rem"


def choice_buttons(
    label: str,
    options: dict[str, str],
    selected: str,
    on_select: Callable[[str], None],
    *,
    colors: dict[str, str] | None = None,
    tooltips: dict[str, str] | None = None,
    info: str = "",
    label_width: str = DEFAULT_LABEL_WIDTH,
    group_width: str = DEFAULT_GROUP_WIDTH,
) -> None:
    """Render a compact, aligned segmented single-choice control.

    The selected option is filled with its colour (or ``primary``) and white
    text; the others are flat grey.  Buttons are ``dense``/``size=sm`` and
    share the group width equally, so the control stays close to the
    surrounding label text and two stacked controls align.

    :param label: Row label (e.g. ``"Mode:"``).
    :param options: ``{value: label}`` mapping; iteration order is display
        order.
    :param selected: The currently selected value.
    :param on_select: Callback invoked with the newly selected value.
    :param colors: Optional ``{value: quasar-colour}`` for the active state
        (e.g. ``{"full": "red-5", "read_only": "green-5"}``).
    :param tooltips: Optional ``{value: text}`` tooltips per option.
    :param info: Optional explanatory tooltip attached to the whole group
        (e.g. an operating-mode downgrade note).
    :param label_width: CSS width for the label (default
        :data:`DEFAULT_LABEL_WIDTH`); pass the same value for stacked
        controls to align them.
    :param group_width: CSS width for the button group (default
        :data:`DEFAULT_GROUP_WIDTH`); pass the same value for stacked
        controls to align them.
    """
    colors = colors or {}
    tooltips = tooltips or {}
    with ui.row().classes("items-center w-full gap-2"):
        ui.label(label).classes("text-xs font-bold text-grey-6").style(
            f"min-width: {label_width}"
        )
        group = (
            ui.button_group()
            .props("dense")
            .classes("text-xs")
            .style(f"width: {group_width}")
        )
        with group:
            for value, text in options.items():
                active = value == selected
                props = (
                    f"dense size=sm no-caps color={colors.get(value, 'primary')} "
                    "text-color=white"
                    if active
                    else "dense size=sm no-caps flat color=grey-7"
                )
                with (
                    ui.button(text, on_click=lambda v=value: on_select(v))
                    .props(props)
                    .classes("flex-1")
                ):
                    tip = tooltips.get(value)
                    if tip:
                        ui.tooltip(tip)
        if info:
            with group:
                ui.tooltip(info)
