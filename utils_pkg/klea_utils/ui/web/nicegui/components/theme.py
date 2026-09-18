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
    # margin on ``<pre>``; a long line would overflow the bubble and the page.
    # The block is kept inside the bubble and its lines wrap (see the wrap
    # rule below); ``overflow-x: auto`` remains as a fallback.
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
    # Wrap long lines in chat code blocks - user/agent fenced code
    # (``.chat-markdown pre``) and tool code boxes (``.tool-code``, a
    # ``ui.code`` element) - so they stay within the bubble instead of
    # producing a horizontal scrollbar.  The chat area itself still scrolls
    # vertically.  ``overflow-wrap: anywhere`` also breaks long unbroken
    # tokens (URLs, base64) that would otherwise overflow.
    ui.add_css(
        ".chat-markdown pre, .chat-markdown pre code, "
        ".tool-code pre, .tool-code code, .tool-code .codehilite pre { "
        "white-space: pre-wrap !important; "
        "overflow-wrap: anywhere !important; "
        "word-break: break-word !important; }"
    )
    # Keep embedded media (future image/audio blocks) within the bubble.
    ui.add_css(
        ".chat-bubble img, .chat-bubble video { max-width: 100%; height: auto; }"
    )
    # Chat input: match the transcript blocks' corner radius (the ``rounded``
    # prop is a pill, so it is not used).  The textarea autogrows upward as the
    # message gets longer (Quasar handles that), capped at 40vh before it
    # scrolls, and the send button in the ``append`` slot is anchored to the
    # bottom-right so it stays on the textarea's last line.
    ui.add_css(".chat-input .q-field__control { border-radius: 0.5rem; }")
    ui.add_css(".chat-input .q-field__native { max-height: 40vh; overflow: auto; }")
    ui.add_css(".chat-input .q-field__append { align-self: flex-end; }")
    # Status-pane context controls (operating mode / tool access).  Quasar's
    # ``flat`` buttons have no border and its fixed grey palette is hard to see
    # on the dark drawer, so the inactive option is drawn as an outline whose
    # colour follows its text (``currentColor``).  The row gets a little
    # vertical breathing room.
    ui.add_css(".choice-row { padding-top: 0.35rem; padding-bottom: 0.35rem; }")
    # Design tokens: the single source of truth for themed colours.  Light
    # values live under ``:root``; dark values override the *same* custom
    # properties under ``body.body--dark`` (NiceGUI/Quasar's dark-mode class),
    # so components use one variable and never need paired light/dark rules.
    #
    # The block must live in NiceGUI's ``overrides`` cascade layer: Quasar's
    # utilities (e.g. ``.text-primary``) are ``!important`` inside the later
    # ``quasar_importants`` layer, and for ``!important`` declarations earlier
    # layers win while unlayered rules (where ``ui.add_css`` lands) lose.
    # Without the layer these rules are silently ignored and the controls fall
    # back to Quasar's fixed blue/greys.
    #
    # Quasar brand tokens (``--q-*``) additionally need ``!important``: NiceGUI
    # auto-applies a ``ui.colors`` element whose ``colors.js`` sets them
    # *inline* on ``<body>`` (``document.body.style.setProperty``), and inline
    # normal declarations beat stylesheet declarations whatever their layer.
    ui.add_css(
        "@layer overrides {\n"
        "  :root {\n"
        "    --klea-control: #424242;\n"
        "    --klea-muted: #757575;\n"
        "    --klea-hint: #9e9e9e;\n"
        "    --klea-surface: #e0e0e0;\n"
        "    --klea-bubble-user: #e8f0fe;\n"
        "    --klea-bubble-tool: #f0f0f0;\n"
        "  }\n"
        "  body.body--dark {\n"
        # Quasar brand token: lighten ``primary`` so primary-tinted widgets (the
        # send button, active tab, Save, links, ...) stay legible on dark.
        "    --q-primary: #64b5f6 !important;\n"
        # Unify the dark page background with the ``q-dark`` panels and drawers
        # (both use ``--q-dark``, #1d1d1d).  Quasar's default ``--q-dark-page``
        # (#121212) is near-black, which left the page/tab strip visibly darker
        # than the panels sitting on it.
        "    --q-dark-page: #1d1d1d !important;\n"
        "    --klea-control: #e0e0e0;\n"
        "    --klea-muted: #cfcfcf;\n"
        "    --klea-hint: #bdbdbd;\n"
        # Footer stays a distinct, slightly darker bar (mirrors light mode,
        # where the footer is darker than the page).
        "    --klea-surface: #161616;\n"
        "    --klea-bubble-user: #1e2a3a;\n"
        "    --klea-bubble-tool: #262626;\n"
        "  }\n"
        # Neutral interactive icons and the inactive segmented option; the
        # outline border follows the text colour (Quasar draws
        # ``.q-btn--outline`` with ``currentColor``).
        "  .icon-btn,\n"
        "  .choice-btn:not(.choice-btn--active) {\n"
        "    color: var(--klea-control) !important;\n"
        "  }\n"
        "  .choice-label {\n"
        "    color: var(--klea-muted) !important;\n"
        "  }\n"
        # Bar surfaces (footer, ...): Quasar sets no footer background, so the
        # app pins one explicitly; this keeps it theme-aware.
        "  .footer-bar { background: var(--klea-surface) !important; }\n"
        # Chat transcript blocks (web-theming: semantic class + token).  All
        # three roles span the full chat width; only the surface distinguishes
        # them, so the user's prompt reads as a tinted block, the agent's reply
        # as plain document text, and a tool round as a neutral block.
        "  .chat-bubble--user { background: var(--klea-bubble-user); }\n"
        "  .chat-bubble--agent { background: transparent; }\n"
        "  .chat-bubble--tool { background: var(--klea-bubble-tool); }\n"
        # Quasar's grey text classes use fixed literals, so map the ones we use
        # onto the tokens in dark mode.
        "  body.body--dark .text-grey-5 { color: var(--klea-hint) !important; }\n"
        "  body.body--dark .text-grey-6 { color: var(--klea-muted) !important; }\n"
        "  body.body--dark .text-grey-7 { color: var(--klea-muted) !important; }\n"
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
    # Preformatted status sections (e.g. the plan): a ``ui.code`` element whose
    # visual "code box" is undone, so the content reads as plain pane text.
    # ``nicegui-code-noformat`` targets the wrapper (which ``ui.code`` gives the
    # ``nicegui-code`` class), cancelling its background, border, shadow,
    # radius and copy button; the inner layers are cleared too and long lines
    # wrap instead of scrolling.  The plan uses this; chat bubbles and the
    # inspector/status JSON keep their normal code styling.
    ui.add_css(
        ".nicegui-code-noformat { "
        "background: transparent !important; "
        "border: none !important; "
        "box-shadow: none !important; "
        "border-radius: 0 !important; "
        "padding: 0.25rem 0 !important; }"
    )
    ui.add_css(
        ".nicegui-code-noformat .nicegui-code-copy { display: none !important; }"
    )
    ui.add_css(
        ".nicegui-code-noformat .nicegui-markdown, "
        ".nicegui-code-noformat pre, "
        ".nicegui-code-noformat pre code, "
        ".nicegui-code-noformat .codehilite, "
        ".nicegui-code-noformat .highlight { "
        "white-space: pre-wrap !important; "
        "word-break: break-word !important; "
        "background: transparent !important; "
        "padding: 0 !important; "
        "margin: 0 !important; }"
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
