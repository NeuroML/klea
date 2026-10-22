#!/usr/bin/env python3
"""
Status panel for the Klea TUI.

The full-height right panel: the chat's streamed state (``devdocs/system/tui.md``)
- the per-node ``state`` sections keyed by section key and the accumulated
token usage.  Each state section renders like the web status pane: a
collapsible heading with the node's ``display`` (Markdown, or a literal block
when ``preformatted``), its ``summary`` and its ``details`` via
:func:`klea_utils.ui.inspect_format.format_details`.

File: klea_utils/ui/tui/components/status_panel.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, MutableMapping
from typing import TYPE_CHECKING, Any, cast

from rich.markdown import Markdown
from textual import on
from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible

from klea_utils.llm import missing_required_roles, parse_model_name
from klea_utils.ui.inspect_format import format_details
from klea_utils.ui.model_config import role_label
from klea_utils.ui.state import format_token_usage, missing_credentials
from klea_utils.ui.tui.components.selectable import SelectableStatic

if TYPE_CHECKING:
    from klea_utils.ui.tui.context import TuiContext

logger = logging.getLogger(__name__)


def _usage_line(usage: Mapping[str, Any]) -> str:
    """Format the token usage totals.

    :param usage: The chat's ``token_usage`` dict.
    :returns: The shared usage line, including cached reads when present.
    """
    return format_token_usage(usage)


def _model_name(raw: str) -> str:
    """Return a role's short model name, or ``"Not set"`` when unset.

    :param raw: The role's model string.
    :returns: The parsed model name (mirrors the web status pane).
    """
    if not raw:
        return "Not set"
    return parse_model_name(raw).model_name or raw


def _models_widgets(model_info: Mapping[str, Any], *, collapsed: bool) -> Collapsible:
    """Build the selected-models group and any missing-config warning.

    Mirrors the web status pane: one bullet per role naming its provider, with
    the resolved model indented beneath it, then a warning when a required
    model or API key is missing.  Wrapped in a collapsible (titled ``Models``)
    so the fixed part of the pane stays compact.

    :param model_info: The chat's ``model_info`` (role -> config).
    :param collapsed: Whether the group starts collapsed.
    :returns: The models group widget.
    """
    widgets: list[SelectableStatic] = []
    for role, cfg in model_info.items():
        raw = str((cfg or {}).get("model", "") or "")
        provider = str((cfg or {}).get("provider", "") or "")
        if not provider and raw:
            provider = parse_model_name(raw).provider or ""
        widgets.append(
            SelectableStatic(
                f"- {role_label(role)}: {provider or '(unknown)'}", markup=False
            )
        )
        widgets.append(SelectableStatic(f"    {_model_name(raw)}", markup=False))
    missing = missing_required_roles(model_info)
    if missing:
        widgets.append(
            SelectableStatic(
                "Select a model to start: "
                + ", ".join(role_label(role) for role in missing),
                markup=False,
                classes="status-missing",
            )
        )
    else:
        missing_providers = sorted(
            {
                str((model_info[role].get("credential") or {}).get("provider", ""))
                for role in missing_credentials(dict(model_info))
            }
            - {""}
        )
        if missing_providers:
            widgets.append(
                SelectableStatic(
                    "Add an API key for: " + ", ".join(missing_providers),
                    markup=False,
                    classes="status-missing",
                )
            )
    return Collapsible(
        *widgets, title="Models", collapsed=collapsed, id="status-models"
    )


def _section_widget(key: str, section: Mapping[str, Any]) -> Collapsible:
    """Build the collapsible widget for one ``state`` section.

    :param key: The section key (``data.key`` or node label).
    :param section: The stored section (heading/display/summary/details/...).
    :returns: An open :class:`~textual.widgets.Collapsible`.
    """
    heading = section.get("heading", "") or key
    display = section.get("display", "")
    summary = section.get("summary", "")
    details = section.get("details", {})
    body: list[SelectableStatic] = []
    if display:
        if section.get("preformatted"):
            # Aligned/literal content (e.g. the plan): render as-is so columns
            # line up and markup-like characters are not interpreted.
            body.append(SelectableStatic(display, markup=False))
        else:
            body.append(SelectableStatic(Markdown(display)))
    elif summary:
        body.append(SelectableStatic(summary, markup=False))
    if details:
        # ``format_details`` is arbitrary JSON (arrays/objects); disable markup
        # so brackets are not parsed as Rich tags.
        body.append(SelectableStatic(format_details(details), markup=False))
    if not body:
        body.append(SelectableStatic("(no content)", markup=False))
    return Collapsible(*body, title=heading, collapsed=False)


class StatusPanel(Vertical):
    """The right panel: fixed app slots/info above a scrolling state body.

    The top is fixed so it is always visible: the app-defined controls
    registered on :attr:`TuiContext.status_extras` (e.g. the agent's operating
    mode and tool-access rows), then the selected models and the token usage.
    Only the streamed state sections below (the plan etc.) scroll, rebuilt on
    every :meth:`set_state`.
    """

    DEFAULT_CSS = """
    StatusPanel #status-extras {
        height: auto;
        margin-bottom: 1;
    }
    StatusPanel #status-fixed {
        height: auto;
    }
    StatusPanel #status-scroll {
        height: 1fr;
    }
    StatusPanel #status-models {
        height: auto;
        margin-bottom: 1;
        /* Left-align with the row above (the Collapsible default indents). */
        padding-left: 0;
    }
    /* Flush every status-pane collapsible (models + state sections) left, so
     * the titles line up with the rows above (the Collapsible default indents
     * both the box and its title/contents). */
    StatusPanel Collapsible {
        padding-left: 0;
        /* Restore the space below each section (setting an edge here resets
         * the Collapsible's own padding). */
        padding-bottom: 1;
    }
    StatusPanel CollapsibleTitle {
        padding-left: 0;
        padding-right: 0;
    }
    StatusPanel Collapsible > Contents {
        padding-left: 0;
    }
    StatusPanel #status-usage {
        margin-bottom: 1;
    }
    StatusPanel .status-missing {
        color: $error;
    }
    /* State sections sit on the status pane's own surface, not a distinct
     * Collapsible ``$surface`` box. */
    StatusPanel Collapsible,
    StatusPanel CollapsibleTitle {
        background: transparent;
    }
    """

    def __init__(self, *, ctx: TuiContext | None = None, id: str | None = None) -> None:
        """Build the panel for *ctx*.

        :param ctx: The shared TUI context (its ``status_extras`` are rendered
            at the top); ``None`` renders no extras.
        :param id: Optional DOM id for the widget.
        """
        super().__init__(id=id)
        self._ctx = ctx
        #: The chat whose Models open/closed state is being tracked.
        self._status_chat: MutableMapping[str, Any] | None = None

    def compose(self) -> ComposeResult:
        """Compose the app slots, the fixed info, and the scrolling sections."""
        with Vertical(id="status-extras"):
            yield from self._extra_widgets()
        yield Vertical(id="status-fixed")
        yield VerticalScroll(id="status-scroll")

    def _extra_widgets(self) -> list[Any]:
        """Build the app-slot widgets from the context's renderers."""
        if self._ctx is None:
            return []
        widgets: list[Any] = []
        for render in self._ctx.status_extras:
            widget = render(self._ctx)
            if widget is None:
                continue
            if isinstance(widget, (list, tuple)):
                widgets.extend(widget)
            else:
                widgets.append(widget)
        return widgets

    async def set_state(
        self, chat: Mapping[str, Any] | None, *, refresh_extras: bool = False
    ) -> None:
        """Rebuild the fixed info and the scrolling state body.

        :param chat: The chat's data dict, or ``None`` when there is none.
        :param refresh_extras: Also rebuild the app slots (a ``context``
            change can alter what a selector should show).
        """
        if refresh_extras:
            extras = self.query_one("#status-extras", Vertical)
            await extras.remove_children()
            widgets = self._extra_widgets()
            if widgets:
                await extras.mount_all(widgets)
        fixed = self.query_one("#status-fixed", Vertical)
        await fixed.remove_children()
        scroll = self.query_one("#status-scroll", VerticalScroll)
        await scroll.remove_children()
        self._status_chat = (
            cast("MutableMapping[str, Any]", chat) if chat is not None else None
        )
        fixed_widgets: list[Any] = []
        sections: list[Any] = []
        if chat:
            model_info = chat.get("model_info") or {}
            if model_info:
                fixed_widgets.append(
                    _models_widgets(
                        model_info,
                        collapsed=not bool(chat.get("status_models_expanded")),
                    )
                )
            usage = chat.get("token_usage") or {}
            if any(usage.values()):
                fixed_widgets.append(
                    SelectableStatic(_usage_line(usage), id="status-usage")
                )
            for key, section in (chat.get("state_sections") or {}).items():
                sections.append(_section_widget(key, section))
        if fixed_widgets:
            await fixed.mount_all(fixed_widgets)
        if not sections:
            sections.append(SelectableStatic("State updates will appear here."))
        await scroll.mount_all(sections)
        scroll.scroll_end(animate=False)

    @on(Collapsible.Toggled)
    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        """Persist the Models group's open/closed state across refreshes.

        The fixed widgets are rebuilt on every status update, so the collapsed
        state is remembered on the chat rather than on the widget.

        :param event: The ``Collapsible.Toggled`` event.
        """
        if event.collapsible.id != "status-models" or self._status_chat is None:
            return
        self._status_chat["status_models_expanded"] = not event.collapsible.collapsed
