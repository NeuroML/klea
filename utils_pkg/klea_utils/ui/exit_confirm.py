#!/usr/bin/env python3
"""
Shared exit-confirmation text for the web and terminal frontends.

Kept in one place so both frontends show identical wording (the web header's
exit button and the TUI's ``Ctrl+Q``).

File: klea_utils/ui/exit_confirm.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

#: Confirmation title.
EXIT_CONFIRM_TITLE = "Exit Klea?"

#: Confirmation body (used verbatim by both frontends).
EXIT_CONFIRM_MESSAGE = (
    "This stops any active run and closes this interface. The backend server "
    "is stopped only if this client started it."
)
