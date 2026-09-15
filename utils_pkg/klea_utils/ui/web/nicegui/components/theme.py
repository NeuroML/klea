#!/usr/bin/env python3
"""
Page theme component: CSS overrides and persistent dark mode.

File: klea_utils/ui/web/nicegui/components/theme.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from nicegui import ui

from klea_utils.ui.web.nicegui.components.context import PageContext
from klea_utils.ui.web.nicegui.components.storage import user_storage_or_none

logger = logging.getLogger(__name__)


def _add_css_overrides() -> None:
    """Install the shared page CSS overrides (flex layout, alerts, panels)."""
    # Make q-page a flex container so the nicegui-content can flex-fill
    # the available page height, which in turn lets the center column
    # grow and pin the input row to the bottom.
    ui.add_css(".q-page { display: flex; flex-direction: column; }")
    ui.add_css(
        ".nicegui-content { display: flex; flex-direction: column; flex: 1; min-height: 0; }"
    )
    # GitHub-style alerts (rendered from ``> [!WARNING]`` etc. by the markdown2
    # 'alerts' extra) used for the fallback / best-effort warnings in bubbles.
    # No default style defined by nicegui for these extras
    ui.add_css(
        ".nicegui-markdown div.alert { "
        "padding: 0.4rem 0.75rem; "
        "border-left: 4px solid #d29922; "
        "border-radius: 0.25rem; "
        "background: rgba(210, 153, 34, 0.12); "
        "margin: 0.5rem 0; "
        "}"
    )
    ui.add_css(
        ".nicegui-markdown div.alert em { font-style: normal; font-weight: 600; }"
    )
    # Collapse long bot messages to 4 lines with an expand / collapse toggle.
    ui.add_css(".msg-collapsed { max-height: 6em; overflow: hidden; }")
    ui.add_css(".msg-expanded { max-height: none; }")
    # Fenced code blocks in chat bubbles.  NiceGUI's markdown CSS only sets a
    # margin on ``<pre>``, so a long line would overflow the bubble and the
    # page.  Keep the block inside the bubble and scroll it internally; the
    # ``chat-markdown`` class scopes this to bubbles (the inspector and status
    # panes have their own rules).
    ui.add_css(
        ".chat-markdown pre { "
        "margin: 0.5rem 0; "
        "padding: 0.5rem 0.75rem; "
        "overflow-x: auto; "
        "max-width: 100%; "
        "border-radius: 0.375rem; "
        "background: rgba(127, 127, 127, 0.15); }"
    )
    ui.add_css(".chat-markdown code { font-size: 0.8rem; }")
    ui.add_css(
        ".chat-markdown :not(pre) > code { "
        "background: rgba(127, 127, 127, 0.15); "
        "padding: 0.1rem 0.3rem; "
        "border-radius: 0.25rem; "
        "word-break: break-word; }"
    )
    # Status-pane context controls (operating mode / tool access).  Quasar's
    # ``flat`` buttons have no border and its fixed grey palette is hard to see
    # on the dark drawer, so the inactive option is drawn as an outline whose
    # colour follows its text (``currentColor``).  The row gets a little
    # vertical breathing room.
    ui.add_css(".choice-row { padding-top: 0.35rem; padding-bottom: 0.35rem; }")
    # Theme-aware colours must live in NiceGUI's ``overrides`` cascade layer:
    # Quasar's utilities (e.g. ``.text-primary``) are ``!important`` inside the
    # later ``quasar_importants`` layer, and for ``!important`` declarations
    # earlier layers win while unlayered rules (where ``ui.add_css`` lands)
    # lose.  Without the layer these rules are silently ignored and the
    # controls fall back to Quasar's fixed blue/greys.
    ui.add_css(
        "@layer overrides {\n"
        # Neutral, theme-aware icon buttons (kebab, copy, expand, settings):
        # Quasar's flat buttons default to ``primary``, which does not adapt.
        "  .icon-btn { color: #424242 !important; }\n"
        "  .body--dark .icon-btn { color: #e0e0e0 !important; }\n"
        # Segmented single-choice controls: outlined inactive option with a
        # neutral label; the border follows the text colour (Quasar draws
        # ``.q-btn--outline`` with ``currentColor``).
        "  .choice-label { color: #757575 !important; }\n"
        "  .body--dark .choice-label { color: #cfcfcf !important; }\n"
        "  .choice-btn:not(.choice-btn--active) { color: #424242 !important; }\n"
        "  .body--dark .choice-btn:not(.choice-btn--active) { color: #e0e0e0 !important; }\n"
        # Primary call-to-action (send): keep the blue family but lighten it on
        # dark backgrounds.
        "  .send-btn { color: #1976d2 !important; }\n"
        "  .body--dark .send-btn { color: #64b5f6 !important; }\n"
        # Quasar's fixed greys do not adapt; lighten the muted text.
        "  .body--dark .text-grey-5 { color: #bdbdbd !important; }\n"
        "  .body--dark .text-grey-6 { color: #cfcfcf !important; }\n"
        "  .body--dark .text-grey-7 { color: #dcdcdc !important; }\n"
        "}"
    )
    ui.add_css(
        ".inspector-entry > summary { list-style: none; display: flex; align-items: center; gap: 0.25rem; }"
    )
    ui.add_css(
        ".inspector-entry > summary::before { content: '\\25B6'; font-size: 0.65rem; margin-right: 0.35rem; transition: transform 0.15s; }"
    )
    ui.add_css(".inspector-entry[open] > summary::before { content: '\\25BC'; }")
    ui.add_css(
        ".inspector-details summary { list-style: none; display: flex; align-items: center; gap: 0.25rem; }"
    )
    ui.add_css(
        ".inspector-details summary::before { content: '\\25B6'; font-size: 0.6rem; margin-right: 0.35rem; }"
    )
    ui.add_css(".inspector-details[open] summary::before { content: '\\25BC'; }")
    ui.add_css(
        ".inspector-details .md-div { overflow: hidden !important; height: auto !important; }"
    )
    ui.add_css(
        ".inspector-details code { white-space: pre-wrap !important; word-break: break-all !important; }"
    )
    ui.add_css(
        ".q-tooltip { max-width: 350px !important; overflow: visible !important; white-space: nowrap !important; padding: 4px 8px !important; }"
    )
    ui.add_css(
        ".model-tooltip { white-space: pre-wrap !important; max-width: none !important; }"
    )
    # The chat input must stay pinned to the bottom of the central pane.
    # Quasar wraps each tab panel in a `.q-panel` container between
    # `.q-tab-panels` and `.q-tab-panel`; it must also flex-fill so the chat
    # panel's scroll area can grow and push the input row down.
    ui.add_css(
        ".center-tab-panels > .q-panel { flex: 1; min-height: 0; display: flex; flex-direction: column; }"
    )
    # Status pane styling  ---  uses disclosure triangles (same pattern as inspector)
    ui.add_css(
        ".status-entry > summary { list-style: none; display: flex; align-items: center; gap: 0.25rem; }"
    )
    ui.add_css(
        ".status-entry > summary::before { content: '\\25B6'; font-size: 0.65rem; margin-right: 0.35rem; transition: transform 0.15s; }"
    )
    ui.add_css(".status-entry[open] > summary::before { content: '\\25BC'; }")
    ui.add_css(
        ".status-details summary { list-style: none; display: flex; align-items: center; gap: 0.25rem; }"
    )
    ui.add_css(
        ".status-details summary::before { content: '\\25B6'; font-size: 0.6rem; margin-right: 0.35rem; }"
    )
    ui.add_css(".status-details[open] summary::before { content: '\\25BC'; }")
    ui.add_css(
        ".status-details code { white-space: pre-wrap !important; word-break: break-all !important; }"
    )
    ui.add_css(
        ".status-entry .nicegui-markdown { overflow: hidden !important; height: auto !important; overflow-wrap: break-word !important; word-break: break-word !important; }"
    )
    # Keep heading sizes in status pane small so they don't compete with
    # the section summary label.  Nodes can use # freely without worrying
    # about hierarchy.
    ui.add_css(
        ".status-entry .nicegui-markdown h1, .status-entry .nicegui-markdown h2, "
        ".status-entry .nicegui-markdown h3, .status-entry .nicegui-markdown h4, "
        ".status-entry .nicegui-markdown h5, .status-entry .nicegui-markdown h6 { "
        "font-size: 0.7rem !important; "
        "font-weight: 600; "
        "margin: 0.15rem 0; "
        "line-height: 1.2; }"
    )
    # Reduce default padding on lists in the status pane (40px is too wide
    # at text-xs scale).
    ui.add_css(
        ".status-entry .nicegui-markdown ul, "
        ".status-entry .nicegui-markdown ol { "
        "padding-inline-start: 1rem; }"
    )


def install_theme(ctx: PageContext) -> None:
    """Install the page CSS overrides and persistent dark mode.

    Binds the dark-mode flag to ``app.storage.user["dark_mode"]`` when
    storage is available, and stores the resulting ``ui.dark_mode``
    element on ``ctx.dark`` for the header toggle.

    :param ctx: The shared page context.
    """
    _add_css_overrides()

    dark = ui.dark_mode()
    ctx.dark = dark
    store = user_storage_or_none()
    if store is not None:
        if "dark_mode" not in store:
            store["dark_mode"] = False
        dark.bind_value(store, "dark_mode")
