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


def _noop_args(*_args: Any, **_kwargs: Any) -> None:
    """No-op default for callbacks that take arguments."""


@dataclass
class PageContext:
    """Shared mutable state and element/callback registry for a Klea page.

    Components (``components/*.py``) attach into this object: they read
    the configuration, keep their mutable state here, and register the
    element references and cross-component callbacks they need.  The
    page assembly creates one context, attaches all components, then
    lets handlers resolve references at event time.

    .. warning::
       **Do not create NiceGUI elements from a
       ``nicegui.background_tasks`` task.**  NiceGUI resolves a new
       element's parent from a per-task slot stack; a background task has
       an empty stack and raises "The current slot cannot be determined
       because the slot stack for this task is empty".  Either wire the
       work as an ``async`` event handler (NiceGUI awaits it in the
       client/slot context, so ``ui.dialog()`` etc. work), or enter a
       slot explicitly before creating elements, e.g. ``with
       ctx.turn_status_container:`` (the turn status region) or ``with
       ctx.dialog_container:`` (dialogs opened from a background task).
       Updating existing elements and ``app.storage`` from a background
       task is fine.

    :param server_url: Base URL of the backend API server.
    :param user_id: Opaque persistent user identifier.
    :param title: Bold application title in the header bar.
    :param subtitle: Optional smaller text shown next to *title*.
    :param disclaimer: Optional text shown in the footer.
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
    #: Per-chat run registry, keyed by ``"{user_id}:{chat_id}"``: the asyncio
    #: task driving each chat's run and its Retry callback.  Page-scoped (the
    #: tasks are bound to this page's event loop) and independent of the
    #: process-global ``chats`` store, so several chats can run at once.
    stream_tasks: dict[str, Any] = field(default_factory=dict)
    retry_cbs: dict[str, Any] = field(default_factory=dict)
    mini_state: bool = True

    # Extra request fields merged into the ``/query/stream`` POST body
    # (e.g. an app-specific operating ``mode`` request, ADR-0030).  The
    # app UI writes it; the shared stream driver forwards it.
    query_extra: dict[str, Any] = field(default_factory=dict)

    # Element references (filled by components at attach time)
    dark: Any = None
    left_drawer: Any = None
    toggle_icon: Any = None
    center_panels: Any = None
    chat_area: Any = None
    scroll_area: Any = None
    turn_status_container: Any = None
    #: Live progress label inside the turn status region, updated in place during
    #: a run (the region itself is re-rendered from per-chat state).
    turn_status_label: Any = None
    text: Any = None
    loading_row: Any = None
    #: Stable inspector pane content column / scroll area (incremental append).
    inspector_container: Any = None
    inspector_scroll: Any = None
    #: Hidden container used as an explicit slot when building dialogs from
    #: a context that has no ambient slot (e.g. the first-run prompt runs in
    #: a background task).  See the slot warning in the class docstring.
    dialog_container: Any = None

    # Cross-component callbacks (registered by components at attach time
    # and invoked by handlers after the page is fully assembled)
    render_chat_area: Callable[[], None] = field(default=_noop)
    scroll_chat_bottom: Callable[[], None] = field(default=_noop)
    refresh_chat_list: Callable[..., Any] = field(default=_noop)
    refresh_status_pane: Callable[..., Any] = field(default=_noop)
    refresh_inspector: Callable[..., Any] = field(default=_noop)
    #: Incremental inspector updates: open a new query section (chat_id,
    #: marker) and append one entry (chat_id, entry) without a full rebuild.
    begin_inspector_section: Callable[..., None] = field(default=_noop_args)
    append_inspector: Callable[..., None] = field(default=_noop_args)
    reset_center_tab: Callable[[], None] = field(default=_noop)
    refresh_send_state: Callable[[], None] = field(default=_noop)
    #: Re-render the turn status region from the active chat's turn status.
    #: Registered by the chat-area component; the stream
    #: component calls it on each status transition.
    refresh_turn_status: Callable[[], None] = field(default=_noop)
    #: Flip the send control between Send and Stop based on the current chat's
    #: run state (registered by the input area; called by the stream component).
    refresh_stream_button: Callable[[], None] = field(default=_noop)
    #: Submit a HITL interrupt answer/cancel from the status-region form
    #: (``response``, ``interrupt_id``, ``cancel``); registered by the input
    #: area, called by the chat area's form (ADR-0046).
    submit_interrupt: Callable[..., Any] = field(default=_noop_args)
    fetch_model_info: Callable[[], Any] | None = None
    fetch_session_model_info: Callable[[], Any] | None = None
    fetch_credentials: Callable[[], Any] | None = None
    model_config_dialog: Callable[[], Any] | None = None
    # Cached per-session default model config (``fetch_session_models``) and
    # the user's masked provider credentials (``fetch_credentials``), used
    # when no chat is active and by the model dialog.
    session_model_info: dict[str, Any] = field(default_factory=dict)
    credentials: list[dict[str, Any]] = field(default_factory=list)
    # App-defined content rendered inside the (refreshable) status pane,
    # e.g. operating-mode or tool-access selectors (ADR-0030,
    # ADR-0037).  Each app UI appends its own render callable; the status
    # pane calls them in registration order.
    status_extras: list[Callable[[], Any]] = field(default_factory=list)
    #: Hooks invoked with a chat's data dict when it is first created, so an
    #: app context control can carry a selection made before the first message
    #: (e.g. operating mode / access level in ``query_extra``) onto that chat.
    chat_created_hooks: list[Callable[[Any], None]] = field(default_factory=list)
    switch_chat: Callable[[str], None] = field(default=_noop_arg)

    def chat_is_streaming(self, chat_id: str) -> bool:
        """Return whether *chat_id* has an active run on this page.

        Reads the per-chat registry (the ``chat_id`` is scoped by this page's
        ``user_id``), not the display state in the ``chats`` store.

        :param chat_id: Chat conversation identifier.
        :returns: True when a live run is registered for the chat.
        """
        task = self.stream_tasks.get(f"{self.user_id}:{chat_id}")
        return task is not None and not task.done()
