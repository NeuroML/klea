#!/usr/bin/env python3
"""
Status panel for the Klea TUI.

The full-height right panel: the chat's streamed state (``devdocs/system/tui.md``)
- the per-node ``state`` sections keyed by section key and the accumulated
token usage.  Each state section renders like the web status pane: a
collapsible heading with the node's ``display`` (Markdown, or a literal block
when ``preformatted``), its ``summary`` and its ``details`` via
:func:`klea_utils.ui.inspect_format.format_details_block`.

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
from textual.widgets import Collapsible, Rule

from klea_utils.llm import missing_required_roles, parse_model_name
from klea_utils.ui.glyphs import get_glyphs, role_marker
from klea_utils.ui.inspect_format import format_details_block
from klea_utils.ui.model_config import role_label
from klea_utils.ui.state import (
    format_token_usage_by_model,
    format_token_usage_compact,
    format_token_usage_lines,
    missing_credentials,
)
from klea_utils.ui.status_layout import estimate_section_lines, section_budget_lines
from klea_utils.ui.tui.components.scroll import KleaScroll
from klea_utils.ui.tui.components.selectable import SelectableStatic

if TYPE_CHECKING:
    from klea_utils.ui.tui.context import TuiContext

logger = logging.getLogger(__name__)


def _usage_line(usage: Mapping[str, Any]) -> str:
    """Format the token usage totals.

    :param usage: The chat's ``token_usage`` dict.
    :returns: The shared usage line, including cached reads when present.
    """
    return format_token_usage_compact(usage)


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


def _usage_widgets(
    usage: Mapping[str, Any],
    by_model: Mapping[str, Any] | None,
    *,
    collapsed: bool,
) -> list[Any]:
    """Build the token-usage group: the total line heading and per-model rows.

    Mirrors the Models collapsible (:func:`_models_widgets`): the accumulated
    total is the heading and one row per model sits in the body, so the fixed
    part of the pane stays compact.  When no per-model breakdown is available
    the total is rendered as a plain static, matching the web status pane.

    :param usage: The chat's ``token_usage`` dict.
    :param by_model: The chat's ``token_usage_by_model`` mapping.
    :param collapsed: Whether the group starts collapsed.
    :returns: A one-item list: the usage collapsible, or the total static.
    """
    raw_by_model = by_model or {}
    rows = format_token_usage_by_model(raw_by_model)
    if not rows:
        return [SelectableStatic(_usage_line(usage), id="status-usage")]
    # One heading per provider with the model and its usage row indented
    # beneath it, mirroring the Models collapsible's plain-text indentation.
    # A model string with no provider prefix groups under its own name, so a
    # bare (or ``unknown``) key still renders.
    grouped: dict[str, list[tuple[str, tuple[str, str]]]] = {}
    for model, _ in rows:
        parsed = parse_model_name(model)
        provider = parsed.provider or model
        grouped.setdefault(provider, []).append(
            (parsed.model_name or model, format_token_usage_lines(raw_by_model[model]))
        )
    widgets: list[SelectableStatic] = []
    for provider, entries in grouped.items():
        widgets.append(
            SelectableStatic(
                f"- {provider}",
                id=_section_id(f"usage-provider-{provider}"),
                markup=False,
            )
        )
        for model, (in_line, out_line) in entries:
            # The ids carry the provider as well as the short model name: the
            # ledger key is the full model string, so two providers may expose
            # a model with the same short name and their ids must stay unique.
            widgets.append(
                SelectableStatic(
                    f"    {model}",
                    id=_section_id(f"usage-{provider}-{model}"),
                    markup=False,
                )
            )
            # Input and output totals render on their own lines, one indent
            # level deeper than the model label above them.
            widgets.append(
                SelectableStatic(
                    f"        {in_line}",
                    id=_section_id(f"usage-in-{provider}-{model}"),
                    markup=False,
                )
            )
            widgets.append(
                SelectableStatic(
                    f"        {out_line}",
                    id=_section_id(f"usage-out-{provider}-{model}"),
                    markup=False,
                )
            )
    return [
        Collapsible(
            *widgets,
            title=_usage_line(usage),
            collapsed=collapsed,
            id="status-usage",
        )
    ]


def _section_id(key: str) -> str:
    """Build the stable widget id for a state-section *key*.

    Textual ids may only contain letters, digits, underscores and hyphens, so
    the key is slugged; the fixed prefix keeps the id valid and namespaced.

    :param key: The section key (``data.key`` or node label).
    :returns: A stable, valid Textual id for the section.
    """
    slug = "".join(char if char.isalnum() else "-" for char in key.lower())
    slug = "-".join(part for part in slug.split("-") if part)
    return f"status-section-{slug or 'section'}"


_ROLE_STYLES: dict[str, str] = {
    "ok": "green",
    "error": "red",
    "current": "bold",
    "pending": "dim",
}


def _escape_markup(text: str) -> str:
    """Escape Rich markup in *text* so literal content is not parsed as tags.

    :param text: The raw block text.
    :returns: The text with Rich markup escaped.
    """
    return text.replace("\\", "\\\\").replace("[", "\\[")


def _styled(text: str, role: Any) -> str:
    """Wrap *text* in the tint for *role*, degrading unknown roles to plain.

    :param text: The block text to style.
    :param role: The block/item role (``ok``/``error``/``current``/``pending``).
    :returns: Rich markup for the styled text (plain when the role is unknown).
    """
    style = _ROLE_STYLES.get(role) if isinstance(role, str) else None
    escaped = _escape_markup(text)
    return f"[{style}]{escaped}[/{style}]" if style else escaped


def _block_lines(block: Any) -> list[str]:
    """Render one content block to styled Rich-markup lines.

    Supported kinds are ``items``, ``fields`` and ``text``; any unknown kind
    (or malformed block) degrades to plain text instead of raising.

    :param block: A content block (a mapping with a ``type``/``kind`` key).
    :returns: The rendered lines, possibly empty.
    """
    if not isinstance(block, Mapping):
        return [str(block)] if block else []
    kind = block.get("type") or block.get("kind") or "text"
    if kind == "items":
        lines: list[str] = []
        # Marker table for the active terminal (ASCII under KLEA_TUI_ASCII or
        # when the stream cannot render Unicode).
        glyphs = get_glyphs()
        for item in block.get("items") or ():
            if isinstance(item, Mapping):
                text = str(item.get("text") or item.get("label") or "")
                if text:
                    marker = role_marker(glyphs, item.get("role"))
                    lines.append(_styled(f"{marker} {text}", item.get("role")))
                else:
                    lines.append("")
            else:
                if item:
                    marker = role_marker(glyphs, None)
                    lines.append(_styled(f"{marker} {item}", None))
                else:
                    lines.append("")
        # Separate consecutive visible step items with one blank line.  The
        # spacer is a single space, not an empty string: ``_content_body``
        # drops falsy lines before they can be mounted as widgets.
        rendered = [line for line in lines if line]
        # The block may ask for compact spacing; any other or absent value
        # falls back to the spaced default.  The positional rule below is
        # unchanged: nothing before the first item and nothing after the last.
        layout = block.get("layout", "spaced")
        spaced: list[str] = []
        for line in rendered:
            if spaced and layout != "compact":
                spaced.append(" ")
            spaced.append(line)
        return spaced
    if kind == "fields":
        lines = []
        fields = block.get("fields") or {}
        if isinstance(fields, Mapping):
            for label, value in fields.items():
                lines.append(_styled(f"{label}: {value}", block.get("role")))
        return lines
    text = block.get("text")
    if text is None:
        text = block.get("content") or ""
    return [_styled(str(text), block.get("role"))] if text else []


def _content_body(content: Any) -> list[SelectableStatic]:
    """Render a section's structured content into body widgets.

    :param content: The stored ``content`` (a list of content blocks).
    :returns: One widget per non-empty rendered line, never raising.
    """
    if not isinstance(content, (list, tuple)):
        content = [content]
    widgets: list[SelectableStatic] = []
    for block in content:
        for line in _block_lines(block):
            if line:
                widgets.append(SelectableStatic(line))
    return widgets


def _section_body(section: Mapping[str, Any]) -> list[SelectableStatic]:
    """Build the body widgets for one ``state`` section.

    Structured ``content`` (when the emitter provided it) is rendered block by
    block; otherwise the plain ``display`` (honouring ``preformatted``) is used
    as a fallback.

    :param section: The stored section (content/display/summary/details/...).
    :returns: The body widgets, never empty.
    """
    display = section.get("display", "")
    summary = section.get("summary", "")
    details = section.get("details", {})
    content = section.get("content")
    body: list[SelectableStatic] = []
    if content:
        body.extend(_content_body(content))
    elif display:
        if section.get("preformatted"):
            # Aligned/literal content (e.g. the plan): render as-is so columns
            # line up and markup-like characters are not interpreted.
            body.append(SelectableStatic(display, markup=False))
        else:
            body.append(SelectableStatic(Markdown(display)))
    elif summary:
        body.append(SelectableStatic(summary, markup=False))
    rendered_details = format_details_block(details)
    if rendered_details:
        # ``format_details_block`` is arbitrary JSON (arrays/objects); disable
        # markup so brackets are not parsed as Rich tags.
        body.append(SelectableStatic(rendered_details, markup=False))
    if not body:
        body.append(SelectableStatic("(no content)", markup=False))
    return body


def _body_budget(section: Mapping[str, Any]) -> tuple[int, bool]:
    """Return a section body's height budget and whether it overflows it.

    :param section: The stored section (content/display/summary/details/...).
    :returns: The budget in lines and whether the body renders more lines than
        that, in which case it scrolls inside itself and may take focus.
    """
    budget = section_budget_lines(section)
    return budget, estimate_section_lines(section) > budget


def _section_scroll(section: Mapping[str, Any], *, collapsed: bool) -> KleaScroll:
    """Wrap a section's body in a height-budgeted scroll container.

    The budget comes from the shared, frontend-neutral helper
    (:mod:`klea_utils.ui.status_layout`), so the body never grows to swallow the
    pane; the outer ``#status-scroll`` stays the authoritative scroll.

    :param section: The stored section (content/display/summary/details/...).
    :param collapsed: Whether the owning collapsible starts collapsed (a
        collapsed body never takes focus).
    :returns: The body container, sized from the shared budget.
    """
    budget, overflows = _body_budget(section)
    scroll = KleaScroll(
        *_section_body(section),
        classes="status-section-body",
        can_focus=overflows and not collapsed,
    )
    scroll.styles.max_height = budget
    return scroll


def _section_widget(key: str, section: Mapping[str, Any]) -> Collapsible:
    """Build the collapsible widget for one ``state`` section.

    The body is height-budgeted (see :func:`_section_scroll`) and is recomputed
    here on every rebuild.

    :param key: The section key (``data.key`` or node label).
    :param section: The stored section (heading/display/summary/details/...).
    :returns: An open :class:`~textual.widgets.Collapsible`.
    """
    heading = section.get("heading", "") or key
    return Collapsible(
        _section_scroll(section, collapsed=False),
        title=heading,
        collapsed=False,
        id=_section_id(key),
        classes="status-section",
    )


#: Attribute stamped on a mounted section widget with the signature of the
#: content it was last built from.  Keeping it on the widget means the stamp is
#: dropped with the widget, so a rebuilt pane starts clean.
_SIGNATURE_ATTR = "_klea_section_signature"


def _section_signature(section: Mapping[str, Any]) -> str:
    """Return a cheap, stable signature of a section's rendered content.

    The signature covers exactly the values :func:`_section_body` and
    :func:`_section_widget` read, so two sections sharing a signature render
    identically.  The pane uses it to skip re-mounting a section that did not
    change: a streamed ``state`` event for one section then repaints that
    section only, instead of tearing down every mounted body on every event
    (``devdocs/system/tui.md``).

    :param section: The stored section (heading/display/summary/details/...).
    :returns: A ``repr`` of the rendered-content fields.
    """
    return repr(
        (
            section.get("heading"),
            section.get("display"),
            section.get("summary"),
            section.get("details"),
            section.get("content"),
            bool(section.get("preformatted")),
        )
    )


async def _update_section(
    widget: Collapsible, key: str, section: Mapping[str, Any]
) -> None:
    """Refresh an already-mounted section widget in place.

    The title is reassigned and the body is replaced inside the collapsible's
    contents, so the section keeps its position and its expanded/collapsed
    state instead of being rebuilt.

    :param widget: The mounted section collapsible.
    :param key: The section key (``data.key`` or node label).
    :param section: The stored section (heading/display/summary/details/...).
    """
    title = section.get("heading", "") or key
    widget.title = title
    # Also stamp the title onto the header widget, so an update renders even
    # before the reactive watcher repaints it.
    for header in widget.query("CollapsibleTitle"):
        if hasattr(header, "label"):
            header.label = title
    # The body is re-mounted inside its scroll container, so the
    # content-derived budget is recomputed here: a section that grew or shrank
    # is re-sized in place instead of keeping a stale height.
    budget, overflows = _body_budget(section)
    scroll = widget.query_one("Contents").query_one(KleaScroll)
    scroll.styles.max_height = budget
    await scroll.set_content(
        _section_body(section), focusable=overflows and not widget.collapsed
    )


async def _reconcile_sections(
    scroll: VerticalScroll, chat: Mapping[str, Any] | None
) -> None:
    """Reconcile the scrolling state sections with the chat's store.

    Sections are matched by their stable id, so the pane is updated rather
    than rebuilt: an existing key has its title/body refreshed in place, a new
    key gains exactly one collapsible, and a key that vanished from the store
    has its widget removed.

    :param scroll: The scrolling container that holds the sections.
    :param chat: The chat's data dict, or ``None`` when there is none.
    """
    sections = (chat.get("state_sections") or {}) if chat else {}
    rendered: dict[str, Any] = {
        widget.id: widget
        for widget in scroll.query(".status-section")
        if widget.id is not None
    }
    new_widgets: list[Collapsible] = []
    for key, section in sections.items():
        widget = rendered.pop(_section_id(key), None)
        signature = _section_signature(section)
        if widget is None:
            widget = _section_widget(key, section)
            setattr(widget, _SIGNATURE_ATTR, signature)
            new_widgets.append(widget)
            continue
        if getattr(widget, _SIGNATURE_ATTR, None) == signature:
            # Unchanged: keep the mounted widget, its mounted body and the
            # reader's place instead of re-rendering it.  This is the common
            # case while streaming, so it keeps the per-event cost bounded.
            logger.debug(f"Status section unchanged, keeping widget: {key}")
            continue
        setattr(widget, _SIGNATURE_ATTR, signature)
        await _update_section(widget, key, section)
    for widget in rendered.values():
        await widget.remove()
    placeholder = scroll.query("#status-empty")
    if sections:
        if placeholder:
            await placeholder.remove()
        if new_widgets:
            await scroll.mount_all(new_widgets)
    elif not placeholder:
        await scroll.mount(
            SelectableStatic("State updates will appear here.", id="status-empty")
        )


class StatusPanel(Vertical):
    """The right panel: fixed app slots/info above a scrolling state body.

    The top is fixed so it is always visible: the app-defined controls
    registered on :attr:`TuiContext.status_extras` (e.g. the agent's operating
    mode and tool-access rows), then the selected models and the token usage.
    Only the streamed state sections below (the plan etc.) scroll; they are
    reconciled by key on every :meth:`set_state`.
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
    /* A section body is capped from its content (``section_budget_lines``) and
     * scrolls inside itself once it overflows, so an expanded section never
     * swallows the pane; the outer #status-scroll stays authoritative. */
    StatusPanel .status-section-body {
        height: auto;
        overflow-y: auto;
    }
    StatusPanel #status-usage {
        /* The usage group closes the static header: the divider below it
         * separates the usage rows from the scrolling state sections, so no
         * trailing blank row is left between them (the general Collapsible
         * bottom padding is cancelled for this group only). */
        margin-bottom: 0;
        padding-bottom: 0;
    }
    StatusPanel .status-divider {
        margin: 0;
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
        """Rebuild the fixed info and reconcile the scrolling state body.

        The fixed slots are rebuilt wholesale; the state sections are
        reconciled by key (:func:`_reconcile_sections`), so an unchanged
        section is refreshed in place rather than rebuilt.

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
        scroll = self.query_one("#status-scroll", VerticalScroll)
        # Capture the scroll position before mutating anything: the fixed block
        # above the sections is rebuilt and sections may be removed, so the
        # pane only follows the tail when the user was already at the bottom;
        # otherwise its offset is restored (a section dropped above the viewport
        # must not yank the view).
        follow_tail = scroll.scroll_y >= scroll.max_scroll_y - 1
        previous_y = scroll.scroll_y
        fixed = self.query_one("#status-fixed", Vertical)
        await fixed.remove_children()
        self._status_chat = (
            cast("MutableMapping[str, Any]", chat) if chat is not None else None
        )
        fixed_widgets: list[Any] = []
        if chat:
            model_info = chat.get("model_info") or {}
            if model_info:
                fixed_widgets.append(
                    _models_widgets(
                        model_info,
                        collapsed=not bool(chat.get("status_models_expanded")),
                    )
                )
            # The usage group mirrors the Models collapsible: the total line
            # is the heading and one row per model sits in the body.  The
            # per-model ids are built via ``_section_id`` so they are stable
            # and unique: the fixed block is rebuilt on every refresh, so a
            # positional id would churn.
            # The usage group follows the Models collapsible directly: no
            # divider is mounted between them, so the two read as one block.
            # The only rule in the fixed block is the end-of-usage
            # ``status-divider`` appended below.
            usage = chat.get("token_usage") or {}
            if any(usage.values()):
                fixed_widgets.extend(
                    _usage_widgets(
                        usage,
                        chat.get("token_usage_by_model"),
                        collapsed=not bool(chat.get("status_usage_expanded")),
                    )
                )
        if fixed_widgets:
            # The divider is the last child of the static block, so it closes
            # the usage area and never scrolls: the state sections below start
            # directly with the plan in #status-scroll.
            fixed_widgets.append(Rule(classes="status-divider"))
            await fixed.mount_all(fixed_widgets)
        await _reconcile_sections(scroll, chat)
        if follow_tail:
            scroll.scroll_end(animate=False)
        else:
            scroll.scroll_to(y=previous_y, animate=False)

    @on(Collapsible.Toggled)
    def on_collapsible_toggled(self, event: Collapsible.Toggled) -> None:
        """Persist the Models group's open/closed state across refreshes.

        The fixed widgets are rebuilt on every status update, so the collapsed
        state is remembered on the chat rather than on the widget.

        :param event: The ``Collapsible.Toggled`` event.
        """
        if self._status_chat is None:
            return
        expanded = not event.collapsible.collapsed
        if event.collapsible.id == "status-models":
            self._status_chat["status_models_expanded"] = expanded
        elif event.collapsible.id == "status-usage":
            self._status_chat["status_usage_expanded"] = expanded
