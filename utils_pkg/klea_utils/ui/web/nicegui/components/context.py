#!/usr/bin/env python3
"""
Shared page context for Klea NiceGUI components.

A :class:`PageContext` carries everything the reusable components need
to coordinate without being assembled into a single closure: the page
configuration, the mutable runtime state (active chat, expand state,
streaming flag), the NiceGUI element references, and the
cross-component callbacks.  Components write into the context at
attach time; handlers read it at event time, after the whole page has
been assembled.

File: klea_utils/ui/web/nicegui/components/context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

DEFAULT_FOOTER = 'Powered by <a href="https://github.com/neuroml/klea">Klea</a>'


def _noop() -> None:
    """No-op default for callbacks that are not yet/never registered."""


def _noop_arg(_: str) -> None:
    """No-op default for one-argument callbacks (e.g. chat switching)."""


@dataclass
class PageContext:
    """Shared mutable state and element/callback registry for a Klea page.

    Components (``components/*.py``) attach into this object: they read
    the configuration, keep their mutable state here, and register the
    element references and cross-component callbacks they need.  The
    page assembly creates one context, attaches all components, then
    lets handlers resolve references at event time.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param title: Bold application title in the header bar.
    :param subtitle: Optional smaller text shown next to *title*.
    :param disclaimer: Optional text shown below the chat input.
    :param footer_text: HTML content for the footer bar.
    :param chat_id: Active chat conversation identifier.
    """

    server_url: str
    user_id: str
    title: str = "Klea"
    subtitle: str = ""
    disclaimer: str = ""
    footer_text: str = DEFAULT_FOOTER
    chat_id: str = ""

    # Mutable runtime state
    expanded: set[int] = field(default_factory=set)
    is_streaming: bool = False
    mini_state: bool = True

    # Element references (filled by components at attach time)
    dark: Any = None
    left_drawer: Any = None
    toggle_icon: Any = None
    center_panels: Any = None
    chat_area: Any = None
    scroll_area: Any = None
    stream_container: Any = None
    text: Any = None
    loading_row: Any = None

    # Cross-component callbacks (registered by components at attach time
    # and invoked by handlers after the page is fully assembled)
    render_chat_area: Callable[[], None] = field(default=_noop)
    scroll_chat_bottom: Callable[[], None] = field(default=_noop)
    refresh_chat_list: Callable[..., Any] = field(default=_noop)
    refresh_status_pane: Callable[..., Any] = field(default=_noop)
    refresh_inspector: Callable[..., Any] = field(default=_noop)
    reset_center_tab: Callable[[], None] = field(default=_noop)
    fetch_model_info: Callable[[], Any] | None = None
    model_config_dialog: Callable[[], Any] | None = None
    switch_chat: Callable[[str], None] = field(default=_noop_arg)
