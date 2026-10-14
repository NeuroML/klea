#!/usr/bin/env python3
"""
Shared context for the Klea Textual terminal client.

A :class:`TuiContext` carries the configuration the shared widgets need and
the app-specific slots the per-app entries register (mirroring the web's
``PageContext``, ADR-0031/ADR-0050).  Shared widgets under
``klea_utils/ui/tui/`` import no app package (invariant I8); per-app controls
enter only through the documented slots here.

File: klea_utils/ui/tui/context.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class TuiContext:
    """Configuration and app slots for a Klea Textual app.

    The per-app entry builds one context and hands it to the shared
    :func:`klea_utils.ui.tui.app.run_tui`, which constructs the Textual app
    from it.  App-specific status-panel controls (the agent's operating-mode
    and tool-access selectors, ADR-0030/0037) are appended to
    :attr:`status_extras`; RAG registers none.

    :param server_url: Base URL of the backend API server.
    :param title: Application title shown in the chat panel header.
    :param app_prefix: Prompt label prefix (e.g. ``"klea-rag"``).
    :param app_name: Log identity for this frontend (e.g. ``"klea-rag-tui"``).
    :param user_id: Opaque persistent user identifier (interim: the
        ``resolve_user_id()`` seam and the session picker land in Phase 5).
    :param chat_id: Active chat conversation identifier (interim: minted at
        launch; the keyed store + active pointer land in Phase 5).
    :param resume_chat_id: Chat to resume on launch (``--resume <chat_id>``).
    :param continue_session: Resume the most recently updated chat on launch
        (``--continue``).
    """

    server_url: str
    title: str = "Klea"
    app_prefix: str = "klea"
    app_name: str = "klea-tui"
    user_id: str = ""
    chat_id: str = ""
    resume_chat_id: str = ""
    continue_session: bool = False

    #: App-defined status-panel renderers, called with this context in
    #: registration order (e.g. mode/access selectors).  Empty for RAG.
    status_extras: list[Callable[[TuiContext], Any]] = field(default_factory=list)
