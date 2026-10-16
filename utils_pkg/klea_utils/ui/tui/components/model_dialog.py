#!/usr/bin/env python3
"""
Model-configuration modal for the Klea TUI.

A per-role editor for the active chat's models, mirroring the web's model
dialog: each role (chat / plan / guard / ...) gets provider, model, a
HuggingFace backend + inference provider, and an optional custom URL; the
fields join back into a model string via the shared
:mod:`klea_utils.ui.model_config` helpers.  Save persists every changed role
as a per-chat override (promoting it into the session defaults), Reset clears
one role's override, and Use-for-all copies a model onto the other modifiable
roles of the same kind.  Catalogue providers/models and the stored credentials
are fetched through the same shared helpers the web dialog uses.

File: klea_utils/ui/tui/components/model_dialog.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, ClassVar

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import ModalScreen
from textual.suggester import Suggester
from textual.widgets import Button, Collapsible, Input, Select, Static

from klea_utils.api.sse import fetch_active_models, fetch_credentials
from klea_utils.llm import (
    HUGGINGFACE_DEFAULT_INFERENCE_PROVIDER,
    HUGGINGFACE_PROVIDER,
    PROVIDERS_WITHOUT_CUSTOM_URL,
)
from klea_utils.ui.model_config import (
    TONE_ERROR,
    CatalogueCache,
    ModelOrigin,
    build_model_string,
    clear_role,
    credential_display,
    live_info,
    parse_model_origin,
    role_label,
    save_roles,
    use_for_all_targets,
)
from klea_utils.ui.tui.components.credentials_dialog import (
    CredentialsDialog,
    CredentialsResult,
)

logger = logging.getLogger(__name__)

#: Backend choices for a HuggingFace model (label, value).
_HF_BACKENDS: list[tuple[str, str]] = [
    ("Hosted (inference providers)", "endpoint"),
    ("Local (downloads the model)", "local"),
]


@dataclass(frozen=True)
class ModelResult:
    """Outcome of the model dialog.

    :param saved: The ``(role, model)`` pairs persisted before the dialog
        closed.
    :param changed: Whether anything changed (a saved model or a credential),
        so the app re-fetches the active models and re-evaluates gating.
    """

    saved: list[tuple[str, str]] = field(default_factory=list)
    changed: bool = False


class _CatalogueSuggester(Suggester):
    """Inline completion over a dynamic catalogue list.

    The option list is read through *options* on every keystroke (so it tracks
    a changed provider); caching is disabled for that reason.
    """

    def __init__(self, options: Callable[[], list[str]]) -> None:
        """Store the option provider.

        :param options: Returns the current suggestion options.
        """
        super().__init__(use_cache=False)
        self._options = options

    async def get_suggestion(self, value: str) -> str | None:
        """Return the first option starting with *value* (case-insensitive)."""
        if not value:
            return None
        folded = value.casefold()
        for option in self._options():
            if option.casefold().startswith(folded) and option.casefold() != folded:
                return option
        return None


class ModelDialog(ModalScreen[ModelResult | None]):
    """Per-role model editor for the active chat."""

    DEFAULT_CSS = """
    ModelDialog {
        align: center middle;
    }
    ModelDialog > VerticalScroll {
        width: 90%;
        max-width: 90;
        height: auto;
        max-height: 90%;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    ModelDialog Input {
        width: 100%;
    }
    /* The per-role button row is a Horizontal, which otherwise defaults to
     * height: 1fr and balloons each role panel open. */
    ModelDialog Collapsible Horizontal {
        height: auto;
    }
    ModelDialog Collapsible > Contents {
        padding: 0 0 0 1;
    }
    ModelDialog #model-actions {
        height: auto;
        align-horizontal: right;
        padding-top: 1;
    }
    ModelDialog #model-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [Binding("escape", "cancel", "Close")]

    def __init__(
        self,
        *,
        server_url: str,
        user_id: str,
        chat_id: str,
        info: dict[str, dict[str, Any]],
        id: str | None = None,
    ) -> None:
        """Build the dialog for the chat's current model config.

        :param server_url: Base URL of the backend API server.
        :param user_id: Opaque persistent user identifier.
        :param chat_id: The active chat id.
        :param info: The resolved model config per role.
        :param id: Optional DOM id for the screen.
        """
        super().__init__(id=id)
        self._server_url = server_url
        self._user_id = user_id
        self._chat_id = chat_id
        self._info = dict(info)
        self._roles = list(info.keys())
        self._catalogue = CatalogueCache(server_url, user_id)
        self._providers: list[str] = []
        self._stored_by_scope: dict[tuple[str, str], dict[str, Any]] = {}
        self._credentials_changed = False
        self._origins: dict[str, ModelOrigin] = {
            role: parse_model_origin(self._model_of(role)) for role in self._roles
        }

    def compose(self) -> ComposeResult:
        """Compose the per-role editors and the action row."""
        with VerticalScroll(id="model-body"):
            yield Static("Models for this chat", id="model-title")
            for role in self._roles:
                yield self._role_collapsible(role)
            with Horizontal(id="model-actions"):
                yield Button("Manage API keys", id="model-keys")
                yield Button("Close", id="model-close", variant="error")
                yield Button("Save", id="model-save", variant="primary")

    def on_mount(self) -> None:
        """Set field visibility, credential status and load the catalogue."""
        for role in self._roles:
            self._update_visibility(role)
        self._refresh_credentials()
        self._load_catalogue()

    # -- composition helpers ---------------------------------------------

    def _model_of(self, role: str) -> str:
        """Return the role's saved model string."""
        return str((self._info.get(role) or {}).get("model", "") or "")

    def _modifiable(self, role: str) -> bool:
        """Return whether the role's model may be edited."""
        return bool((self._info.get(role) or {}).get("modifiable", True))

    def _credential_of(self, role: str) -> dict[str, Any]:
        """Return the role's saved credential block (or an empty dict)."""
        cred = (self._info.get(role) or {}).get("credential")
        return cred if isinstance(cred, dict) else {}

    def _same_kind(self, role: str) -> list[str]:
        """Return the other modifiable roles of the same model kind."""
        cfg = self._info.get(role) or {}
        kind = cfg.get("model_type", "text_generation")
        return [
            other
            for other, other_cfg in self._info.items()
            if other != role
            and other_cfg.get("modifiable", True)
            and other_cfg.get("model_type", "text_generation") == kind
        ]

    def _role_collapsible(self, role: str) -> Collapsible:
        """Build one role's editor."""
        origin = self._origins[role]
        modifiable = self._modifiable(role)
        provider = Input(
            value=origin.provider,
            placeholder="provider",
            id=self._dom_id("provider", role),
        )
        provider.suggester = _CatalogueSuggester(lambda: self._providers)
        model = Input(
            value=origin.model, placeholder="model", id=self._dom_id("model", role)
        )
        model.suggester = _CatalogueSuggester(
            lambda r=role: self._catalogue.models_for(self._provider_of(r))
        )
        backend = Select(
            _HF_BACKENDS,
            value=origin.backend,
            allow_blank=False,
            id=self._dom_id("backend", role),
        )
        inference = Input(
            value=origin.inference_provider,
            placeholder="inference provider",
            id=self._dom_id("inference", role),
        )
        inference.suggester = _CatalogueSuggester(
            lambda: [HUGGINGFACE_DEFAULT_INFERENCE_PROVIDER]
        )
        url = Input(
            value=origin.url, placeholder="custom URL", id=self._dom_id("url", role)
        )
        cred = Static("", id=self._dom_id("cred", role))
        children: list[Any] = [provider, model, backend, inference, url, cred]
        if not modifiable:
            for widget in (provider, model, backend, inference, url):
                widget.disabled = True
            children.append(Static("Locked by administrator", markup=False))
        else:
            buttons: list[Button] = []
            if len(self._same_kind(role)) >= 1:
                buttons.append(
                    Button("Use for all roles", id=self._dom_id("useall", role))
                )
            buttons.append(Button("Reset", id=self._dom_id("reset", role)))
            children.append(Horizontal(*buttons))
        return Collapsible(*children, title=role_label(role), collapsed=False)

    @staticmethod
    def _dom_id(kind: str, role: str) -> str:
        """Return the DOM id for *kind* on *role*."""
        return f"model-{kind}-{role}"

    def _field(self, kind: str, role: str) -> Any:
        """Return the widget for *kind* on *role*."""
        return self.query_one(f"#{self._dom_id(kind, role)}")

    def _provider_of(self, role: str) -> str:
        """Return the role's current provider field value."""
        return str(self._field("provider", role).value or "").strip()

    def _current_model(self, role: str) -> str:
        """Return the role's model string built from its current fields."""
        return build_model_string(
            self._provider_of(role),
            str(self._field("model", role).value or ""),
            url=str(self._field("url", role).value or ""),
            backend=str(self._field("backend", role).value or "endpoint"),
            inference_provider=str(self._field("inference", role).value or ""),
            original=self._origins[role],
        )

    # -- reactivity -------------------------------------------------------

    def _update_visibility(self, role: str) -> None:
        """Show the custom URL or the HuggingFace fields for the role."""
        provider = self._provider_of(role).lower()
        is_hf = provider == HUGGINGFACE_PROVIDER
        backend = self._field("backend", role)
        inference = self._field("inference", role)
        url = self._field("url", role)
        backend.display = is_hf
        inference.display = is_hf and (backend.value or "endpoint") != "local"
        url.display = not is_hf and provider not in PROVIDERS_WITHOUT_CUSTOM_URL

    def _refresh_credentials(self) -> None:
        """Update every role's credential status label."""
        for role in self._roles:
            text, tone = credential_display(
                self._current_model(role),
                self._credential_of(role),
                self._stored_by_scope,
            )
            label = self._field("cred", role)
            label.update(text)
            label.set_class(tone == TONE_ERROR, "model-cred-error")

    @work
    async def _load_catalogue(self) -> None:
        """Fetch the catalogue providers/models and the stored credentials."""
        self._providers = await self._catalogue.providers()
        for role in self._roles:
            provider = self._provider_of(role)
            if provider:
                await self._catalogue.load_models(provider)
        stored = await fetch_credentials(self._server_url, self._user_id)
        self._stored_by_scope = {
            (str(c.get("provider", "")), str(c.get("endpoint", "") or "")): c
            for c in stored
        }
        self._refresh_credentials()

    async def _reload_info(self) -> None:
        """Re-fetch the active models and reset the fields from them."""
        info = await fetch_active_models(self._server_url, self._user_id, self._chat_id)
        if info:
            self._info = info
        for role in self._roles:
            origin = parse_model_origin(self._model_of(role))
            self._origins[role] = origin
            self._set_fields(role, origin)
        self._refresh_credentials()

    def _set_fields(self, role: str, origin: ModelOrigin) -> None:
        """Set *role*'s fields from *origin*."""
        self._field("provider", role).value = origin.provider
        self._field("model", role).value = origin.model
        self._field("url", role).value = origin.url
        self._field("backend", role).value = origin.backend
        self._field("inference", role).value = origin.inference_provider
        self._update_visibility(role)

    def on_input_changed(self, event: Input.Changed) -> None:
        """React to a field edit (visibility, credentials, provider models)."""
        parsed = self._parse_id(event.input.id)
        if parsed is None:
            return
        kind, role = parsed
        if kind == "provider":
            self._update_visibility(role)
            self._load_provider_models(role)
        self._refresh_credentials()

    def on_select_changed(self, event: Select.Changed) -> None:
        """React to the HuggingFace backend change."""
        parsed = self._parse_id(event.select.id)
        if parsed is None:
            return
        kind, role = parsed
        if kind == "backend":
            self._update_visibility(role)
            self._refresh_credentials()

    def _parse_id(self, widget_id: str | None) -> tuple[str, str] | None:
        """Split a ``model-<kind>-<role>`` id, or return ``None``."""
        if not widget_id or not widget_id.startswith("model-"):
            return None
        parts = widget_id.split("-", 2)
        if len(parts) != 3:
            return None
        return parts[1], parts[2]

    @work
    async def _load_provider_models(self, role: str) -> None:
        """Load (and cache) the current provider's catalogue models."""
        provider = self._provider_of(role)
        if provider:
            await self._catalogue.load_models(provider)

    # -- actions ----------------------------------------------------------

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Dispatch the dialog's buttons."""
        button_id = event.button.id or ""
        if button_id == "model-close":
            self.action_cancel()
            return
        if button_id == "model-save":
            self._save()
            return
        if button_id == "model-keys":
            self._open_credentials()
            return
        parsed = self._parse_id(button_id)
        if parsed is None:
            return
        kind, role = parsed
        if kind == "reset":
            self._reset(role)
        elif kind == "useall":
            self._use_for_all(role)

    def action_cancel(self) -> None:
        """Dismiss without saving models (credentials may have changed)."""
        self.dismiss(ModelResult(changed=self._credentials_changed))

    @work
    async def _save(self) -> None:
        """Persist every changed role and close the dialog."""
        role_values = {
            role: self._current_model(role)
            for role in self._roles
            if self._modifiable(role)
        }
        outcome = await save_roles(
            self._server_url,
            self._user_id,
            self._chat_id,
            session_scope=False,
            info=self._info,
            role_values=role_values,
        )
        if not outcome.ok:
            self.notify(
                f"Failed to save the model for {outcome.failed_role}.",
                severity="error",
            )
            return
        logger.debug(f"model dialog: saved {outcome.saved = }")
        self.dismiss(
            ModelResult(
                saved=outcome.saved,
                changed=bool(outcome.saved) or self._credentials_changed,
            )
        )

    @work
    async def _reset(self, role: str) -> None:
        """Clear one role's override and reload the inherited model."""
        ok = await clear_role(
            self._server_url,
            self._user_id,
            self._chat_id,
            session_scope=False,
            role=role,
        )
        if not ok:
            self.notify("Failed to reset the model.", severity="error")
            return
        logger.debug(f"model dialog: reset {role = }")
        await self._reload_info()

    def _use_for_all(self, role: str) -> None:
        """Copy this role's model onto the other same-kind roles."""
        value = self._current_model(role)
        applied = use_for_all_targets(self._info, role, value)
        for target in applied:
            origin = parse_model_origin(value)
            self._origins[target] = origin
            self._set_fields(target, origin)
        logger.debug(f"model dialog: use for all {role = }\n{list(applied) = }")
        self._refresh_credentials()

    def _open_credentials(self) -> None:
        """Open the credentials dialog for the (live) required scopes."""
        role_values = {role: self._current_model(role) for role in self._roles}
        self.app.push_screen(
            CredentialsDialog(
                server_url=self._server_url,
                user_id=self._user_id,
                info=live_info(self._info, role_values, self._stored_by_scope),
            ),
            callback=self._after_credentials,
        )

    def _after_credentials(self, result: CredentialsResult | None) -> None:
        """Refresh the credential statuses when keys changed.

        :param result: The credentials dialog's result, or ``None``.
        """
        if result and result.changed:
            self._credentials_changed = True
            self._reload_credentials()

    @work
    async def _reload_credentials(self) -> None:
        """Re-fetch the stored credentials and the active models."""
        stored = await fetch_credentials(self._server_url, self._user_id)
        self._stored_by_scope = {
            (str(c.get("provider", "")), str(c.get("endpoint", "") or "")): c
            for c in stored
        }
        # Re-fetch the active models too: a stored key changes the resolved
        # credential ``source`` per role (used by the send gating).
        info = await fetch_active_models(self._server_url, self._user_id, self._chat_id)
        if info:
            self._info = info
        self._refresh_credentials()
