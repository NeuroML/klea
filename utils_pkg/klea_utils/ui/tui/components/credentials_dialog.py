#!/usr/bin/env python3
"""
Provider-credentials modal for the Klea TUI.

A masked, write-only editor for the provider API keys the active chat's
models need, mirroring the web's credentials dialog: one section per required
provider(+endpoint) scope with a stored-key indicator, a password field for a
new key, and Save / Clear.  Keys are stored per provider on the server; the
dialog only ever sends them (they are truncated in API responses).

File: klea_utils/ui/tui/components/credentials_dialog.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input

from klea_utils.api.sse import fetch_credentials
from klea_utils.ui.client import clear_credential, set_credential
from klea_utils.ui.model_config import required_credential_scopes
from klea_utils.ui.tui.components.buttons import action_button
from klea_utils.ui.tui.components.selectable import SelectableStatic

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CredentialsResult:
    """Outcome of the credentials dialog.

    :param changed: Whether a key was saved or cleared.
    """

    changed: bool = False


class CredentialsDialog(ModalScreen[CredentialsResult | None]):
    """Masked, write-only editor for the required provider API keys."""

    DEFAULT_CSS = """
    CredentialsDialog {
        align: center middle;
    }
    CredentialsDialog > VerticalScroll {
        width: 90%;
        max-width: 80;
        height: auto;
        max-height: 90%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    CredentialsDialog Input {
        width: 100%;
    }
    CredentialsDialog .cred-scope {
        height: auto;
        padding-top: 1;
    }
    CredentialsDialog #cred-list {
        height: auto;
    }
    CredentialsDialog #cred-actions {
        height: auto;
        align-horizontal: right;
        padding-top: 1;
    }
    CredentialsDialog #cred-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Close")]

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        info: dict[str, dict[str, Any]],
        id: str | None = None,
    ) -> None:
        """Build the dialog for the scopes *info* requires.

        :param server_url: Base URL of the backend API server.
        :param user_id: Opaque persistent user identifier.
        :param info: The (possibly live) model config per role.
        :param id: Optional DOM id for the screen.
        """
        super().__init__(id=id)
        self._server_url = server_url
        self._user_id = user_id
        self._info = info
        self._stored: dict[tuple[str, str], dict[str, Any]] = {}
        #: The scope list in render order (indexed by DOM id suffix).
        self._scope_list: list[tuple[str, str]] = []
        self._changed = False

    def compose(self) -> ComposeResult:
        """Compose the scope list container and the close button."""
        with VerticalScroll(id="cred-body"):
            yield SelectableStatic("API keys", id="cred-title")
            yield SelectableStatic(
                "Stored per provider on the server, truncated in API "
                "responses, and removed after 7 days unused. Leave blank to "
                "use the environment.",
                id="cred-hint",
                markup=False,
            )
            yield Vertical(id="cred-list")
            with Horizontal(id="cred-actions", classes="actions"):
                yield action_button("Close", id="cred-close", variant="default")

    def on_mount(self) -> None:
        """Load the stored credentials and render the scopes."""
        self.run_worker(self._load())

    async def _load(self) -> None:
        """Fetch the stored credentials and (re)build the scope list."""
        stored = await fetch_credentials(self._server_url, self._user_id)
        self._stored = {
            (str(c.get("provider", "")), str(c.get("endpoint", "") or "")): c
            for c in stored
        }
        scopes = required_credential_scopes(self._info)
        # Also expose stored keys whose provider is not currently in use.
        for scope in self._stored:
            scopes.setdefault(
                scope,
                {"provider": scope[0], "endpoint": scope[1], "requires_key": True},
            )
        self._scope_list = list(scopes)
        await self._render_list()

    async def _render_list(self) -> None:
        """Rebuild the per-scope sections."""
        container = self.query_one("#cred-list", Vertical)
        await container.remove_children()
        if not self._scope_list:
            await container.mount(
                SelectableStatic("No API keys are required by the current models.")
            )
            return
        await container.mount_all(
            self._scope_widget(index, scope)
            for index, scope in enumerate(self._scope_list)
        )

    def _scope_widget(self, index: int, scope: tuple[str, str]) -> Vertical:
        """Build one provider scope's section."""
        provider, endpoint = scope
        label = provider + (f" ({endpoint})" if endpoint else "")
        stored = self._stored.get(scope)
        status = f"Stored: {stored.get('masked', '')}" if stored else "Not set"
        children: list[Any] = [
            SelectableStatic(label, markup=False),
            SelectableStatic(status, markup=False),
            Input(password=True, placeholder="New API key", id=f"cred-key-{index}"),
        ]
        buttons: list[Button] = []
        if stored:
            buttons.append(action_button("Clear", id=f"cred-clear-{index}"))
        buttons.append(
            action_button("Save", id=f"cred-save-{index}", variant="primary")
        )
        children.append(Horizontal(*buttons, classes="actions"))
        return Vertical(*children, classes="cred-scope")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch the dialog's buttons."""
        button_id = event.button.id or ""
        if button_id == "cred-close":
            self.action_cancel()
            return
        if button_id.startswith("cred-save-"):
            self._save(int(button_id.rsplit("-", 1)[1]))
        elif button_id.startswith("cred-clear-"):
            self._clear(int(button_id.rsplit("-", 1)[1]))

    def action_cancel(self) -> None:
        """Dismiss, reporting whether anything changed."""
        self.dismiss(CredentialsResult(changed=self._changed))

    @work
    async def _save(self, index: int) -> None:
        """Store a new key for the scope at *index*."""
        provider, endpoint = self._scope_list[index]
        secret = self.query_one(f"#cred-key-{index}", Input).value.strip()
        if not secret:
            logger.debug(f"credentials: empty key ignored for {provider = }")
            return
        ok = await set_credential(
            self._server_url, self._user_id, provider, secret, endpoint
        )
        if not ok:
            self.notify("Failed to store the API key.", severity="error")
            return
        logger.debug(f"credentials: stored {provider = }\n{endpoint = }")
        self._changed = True
        await self._load()

    @work
    async def _clear(self, index: int) -> None:
        """Clear the stored key for the scope at *index*."""
        provider, endpoint = self._scope_list[index]
        ok = await clear_credential(self._server_url, self._user_id, provider, endpoint)
        if not ok:
            self.notify("Failed to clear the API key.", severity="error")
            return
        logger.debug(f"credentials: cleared {provider = }\n{endpoint = }")
        self._changed = True
        await self._load()
