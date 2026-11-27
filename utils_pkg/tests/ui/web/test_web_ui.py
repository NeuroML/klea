#!/usr/bin/env python3
"""
Tests for the shared NiceGUI components.

The components are rendered on a minimal in-process page (see
``conftest.py``) and driven with NiceGUI's user simulation, so the
assertions cover what a user actually sees (header, footer, chat
empty-state, session list), not just the underlying state helpers.

File: tests/ui/web/test_web_ui.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from klea_utils.ui.web.nicegui.components import header
from klea_utils.ui.web.nicegui.components.chat_list import _chat_preview
from nicegui import ui
from nicegui.testing.user_interaction import UserInteraction


def _buttons_with(user, predicate) -> list:
    return [button for button in user.find(ui.button).elements if predicate(button)]


def _power_button(user) -> list:
    return _buttons_with(
        user, lambda b: "power_settings_new" in str(getattr(b, "_props", ""))
    )


def _exit_button(user) -> list:
    return _buttons_with(user, lambda b: getattr(b, "text", "") == "Exit")


async def _wait_for(predicate, retries: int = 50, delay: float = 0.05) -> bool:
    """Poll *predicate* until it is true (or give up after *retries*)."""
    import asyncio

    for _ in range(retries):
        if predicate():
            return True
        await asyncio.sleep(delay)
    return False


def _stub_exit_hooks(monkeypatch) -> list[int]:
    """Replace the exit side effects (browser close + server shutdown)."""
    shutdown_calls: list[int] = []
    monkeypatch.setattr(header, "stop_all_streams", lambda ctx: None)
    monkeypatch.setattr(header.ui, "run_javascript", lambda *a, **k: None)
    monkeypatch.setattr(header.app, "shutdown", lambda: shutdown_calls.append(1))
    return shutdown_calls


async def test_page_renders_chrome(utils_user):
    """The header title, chat empty-state and center tabs render."""
    await utils_user.open("/")
    await utils_user.should_see("Klea Test")
    await utils_user.should_see("Start a conversation")
    await utils_user.should_see("inspect")


async def test_header_has_an_exit_button(utils_user):
    """The header offers an exit button (the ``/exit`` affordance)."""
    await utils_user.open("/")
    assert _power_button(utils_user)


async def test_exit_button_confirms_before_exiting(utils_user, monkeypatch):
    """The header exit button asks first; only Exit stops the server."""
    shutdown_calls = _stub_exit_hooks(monkeypatch)

    await utils_user.open("/")
    power = _power_button(utils_user)
    assert power
    UserInteraction(utils_user, {power[0]}, None).click()

    await utils_user.should_see("Exit Klea?", retries=50)
    assert shutdown_calls == []

    exit_buttons = _exit_button(utils_user)
    assert exit_buttons
    UserInteraction(utils_user, {exit_buttons[0]}, None).click()

    assert await _wait_for(lambda: shutdown_calls == [1])


async def test_exit_dialog_cancel_keeps_the_server_running(utils_user, monkeypatch):
    """Cancelling the exit dialog does not shut the server down."""
    shutdown_calls = _stub_exit_hooks(monkeypatch)

    await utils_user.open("/")
    power = _power_button(utils_user)
    assert power
    UserInteraction(utils_user, {power[0]}, None).click()
    await utils_user.should_see("Exit Klea?", retries=50)

    cancel_buttons = _buttons_with(
        utils_user, lambda b: getattr(b, "text", "") == "Cancel"
    )
    assert cancel_buttons
    UserInteraction(utils_user, {cancel_buttons[0]}, None).click()

    assert shutdown_calls == []


async def test_hitl_input_form_separates_consecutive_question_sets(
    fake_backend, utils_user
):
    """A multi-question ask renders one .hitl-question-set per question.

    The ask only renders for the active chat (``render_turn_status`` reads the
    active chat's own state), so the test hydrates one chat, switches to it,
    and then inspects the mounted form: one ``.hitl-question-set`` wrapper per
    question, each holding exactly one label and one input, in question order.
    The separator is the adjacent-sibling ``border-top`` CSS rule on the
    wrappers, so no divider element is mounted.
    """
    from klea_utils.ui.state import ensure_chat

    fake_backend.chats = [{"chat_id": "c1", "title": "Ask Chat", "created_at": 0}]

    await utils_user.open("/")
    await utils_user.should_see("Ask Chat", retries=50)

    chat = ensure_chat("u", "c1")
    chat["turn_status"] = {"kind": "awaiting_input"}
    chat["interrupt"] = {
        "kind": "input",
        "interrupt_id": "i1",
        "questions": [
            {"question": "first question?"},
            {"question": "second question?"},
        ],
    }

    def _descendants(element) -> list:
        """Return *element*'s descendants (version tolerant)."""
        walker = getattr(element, "descendants", None)
        if callable(walker):
            return list(walker())
        slot = getattr(element, "default_slot", None)
        found = []
        for child in list(getattr(slot, "children", []) or []):
            found.append(child)
            found.extend(_descendants(child))
        return found

    def _classes(element) -> str:
        """Return a rendered element's CSS classes (version tolerant)."""
        classes = getattr(element, "_classes", None)
        if classes is None:
            attr = getattr(element, "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    def _texts(element) -> list:
        """Return the text of *element* and its descendants."""
        nodes = [element, *_descendants(element)]
        return [str(getattr(node, "text", "") or "") for node in nodes]

    # Selecting the chat sets ``ctx.chat_id`` and re-renders the transcript,
    # which mounts the pending ask from that chat's state.
    chat_items = [
        item
        for item in utils_user.find(ui.item).elements
        if any("Ask Chat" in text for text in _texts(item))
    ]
    assert chat_items
    UserInteraction(utils_user, {chat_items[0]}, None).click()
    await utils_user.should_see("User input", retries=50)

    set_containers = [
        column
        for column in utils_user.find(ui.column).elements
        if "hitl-question-set" in _classes(column)
    ]
    assert len(set_containers) == 2

    question_texts = []
    for container in set_containers:
        descendants = _descendants(container)
        labels = [node for node in descendants if isinstance(node, ui.label)]
        inputs = [node for node in descendants if isinstance(node, ui.input)]
        assert len(labels) == 1
        assert len(inputs) == 1
        question_texts.append(str(getattr(labels[0], "text", "")))

    # ``find`` does not guarantee render order, so compare the questions as a
    # set here; the render order is asserted below from the ask's slot
    # children (which are mounted in question order).
    assert sorted(question_texts) == ["first question?", "second question?"]

    ask_containers = [
        column
        for column in utils_user.find(ui.column).elements
        if "hitl-ask" in _classes(column)
    ]
    assert len(ask_containers) == 1
    slot = getattr(ask_containers[0], "default_slot", None)
    ordered_sets = [
        child
        for child in list(getattr(slot, "children", []) or [])
        if "hitl-question-set" in _classes(child)
    ]
    assert len(ordered_sets) == 2
    ordered_texts = []
    for container in ordered_sets:
        labels = [
            node for node in _descendants(container) if isinstance(node, ui.label)
        ]
        assert len(labels) == 1
        ordered_texts.append(str(getattr(labels[0], "text", "")))
    assert ordered_texts == ["first question?", "second question?"]


async def test_empty_state_prompts_when_a_required_model_is_missing(
    fake_backend, utils_user
):
    """A missing required model renders the first-run setup card."""
    fake_backend.session_models = {
        "chat": {"model": "", "required": True, "credential": {}}
    }
    await utils_user.open("/")
    await utils_user.should_see("Models are not configured")
    await utils_user.should_see("Choose models")


async def test_session_list_shows_hydrated_chats(fake_backend, utils_user):
    """A chat returned by the backend appears in the session list."""
    fake_backend.chats = [{"chat_id": "c1", "title": "First Chat", "created_at": 0}]
    await utils_user.open("/")
    await utils_user.should_see("First Chat", retries=50)


class TestChatPreview:
    """The chat-list tooltip preview prefers the server's first query."""

    def test_prefers_the_hydrated_server_preview(self):
        chat = {
            "preview": "server query",
            "messages": [{"role": "user", "text": "local query"}],
        }
        assert _chat_preview(chat) == "server query"

    def test_falls_back_to_the_first_local_user_message(self):
        chat = {
            "messages": [
                {"role": "assistant", "text": "welcome"},
                {"role": "user", "text": "first local query"},
                {"role": "user", "text": "second"},
            ]
        }
        assert _chat_preview(chat) == "first local query"

    def test_collapses_and_truncates(self):
        chat = {"preview": "line one\nline two " + "x" * 400}
        preview = _chat_preview(chat)
        assert preview.startswith("line one line two ")
        assert preview.endswith("...")
        assert len(preview) == 200


async def test_status_pane_renders_content_blocks_with_fallback(
    fake_backend, utils_user
):
    """The status pane renders items and degrades unknown kinds/roles."""
    from typing import cast

    from klea_utils.ui.state import StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Renderer",
        "display": "",
        "summary": "",
        "details": {},
        "preformatted": False,
        "content": [
            {
                "kind": "items",
                "items": [
                    {"text": "ok item", "role": "ok"},
                    {"text": "error item", "role": "error"},
                    {"text": "current item", "role": "current"},
                    {"text": "pending item", "role": "pending"},
                    {"text": "unknown role item", "role": "mystery"},
                ],
            },
            {"kind": "mystery", "text": "unknown kind item"},
            {"kind": "text", "text": "plain item"},
        ],
    }
    chat["state_sections"]["render"] = cast(StateSection, section)

    await utils_user.open("/")
    await utils_user.should_see("ok item")
    await utils_user.should_see("error item")
    await utils_user.should_see("current item")
    await utils_user.should_see("pending item")
    await utils_user.should_see("unknown role item")
    await utils_user.should_see("unknown kind item")
    await utils_user.should_see("plain item")


async def test_status_pane_styles_the_current_role_and_degrades_unknown_roles(
    fake_backend, utils_user
):
    """'current' items are bold; an unknown role still renders unstyled."""
    from typing import cast

    from klea_utils.ui.state import StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Roles",
        "display": "",
        "summary": "",
        "details": {},
        "preformatted": False,
        "content": [
            {
                "kind": "items",
                "items": [
                    {"text": "bold current item", "role": "current"},
                    {"text": "mystery role item", "role": "mystery"},
                ],
            }
        ],
    }
    chat["state_sections"]["roles"] = cast(StateSection, section)

    await utils_user.open("/")
    await utils_user.should_see("bold current item")
    await utils_user.should_see("mystery role item")

    from klea_utils.ui.glyphs import UNICODE_GLYPHS

    # The pane prefixes every item with its role marker, so the rendered label
    # text carries the marker ahead of the item text.
    current_text = f"{UNICODE_GLYPHS.marker_current} bold current item"
    unknown_text = f"{UNICODE_GLYPHS.marker_plain} mystery role item"

    labels = {
        getattr(label, "text", ""): label
        for label in utils_user.find(ui.label).elements
    }

    def _css_classes(label) -> str:
        """Return a rendered label's CSS classes (version tolerant)."""
        classes = getattr(label, "_classes", None)
        if classes is None:
            attr = getattr(label, "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    assert current_text in labels
    assert unknown_text in labels
    current_classes = _css_classes(labels[current_text])
    unknown_classes = _css_classes(labels[unknown_text])
    assert "font-bold" in current_classes
    assert "font-bold" not in unknown_classes


async def test_status_pane_caps_each_section_body_with_an_inline_budget(
    fake_backend, utils_user
):
    """Every status-section body carries a per-section inline max-height.

    The body height comes from the shared ``section_budget_lines`` helper, so
    each rendered section is bounded by an inline ``max-height`` in ``rem``
    and scrolls inside itself rather than growing the drawer.  No ``Memory``
    section is rendered by the pane at all.
    """
    from typing import cast

    from klea_utils.ui.state import StateSection, ensure_chat

    # Seed a long, preformatted section: its rendered line count exceeds the
    # ceiling, so the shared helper must clamp it to the maximum budget.
    chat = ensure_chat("u", "")
    section = {
        "heading": "Budget",
        "display": "\n".join(f"line {index}" for index in range(40)),
        "summary": "",
        "details": {},
        "preformatted": True,
    }
    chat["state_sections"]["budget"] = cast(StateSection, section)

    await utils_user.open("/")
    await utils_user.should_see("Budget")

    def _inline_style(element) -> str:
        # Return an element's inline style text (version tolerant).
        raw = getattr(element, "_style", None)
        if isinstance(raw, dict):
            return "; ".join(f"{key}: {value}" for key, value in raw.items())
        if isinstance(raw, str):
            return raw
        attr = getattr(element, "style", "")
        return attr if isinstance(attr, str) else ""

    def _css_classes(element) -> str:
        # Return an element's CSS classes (version tolerant): the user
        # simulation treats a leading-dot ``find`` target as a marker, not a
        # CSS selector, so the body is located by type and filtered here.
        classes = getattr(element, "_classes", None)
        if classes is None:
            attr = getattr(element, "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    bodies = [
        element
        for element in utils_user.find(ui.column).elements
        if "status-entry-body" in _css_classes(element)
    ]
    assert bodies, "a rendered status-section body must exist"
    styles = [_inline_style(element) for element in bodies]
    assert any("max-height" in style for style in styles), styles
    assert any("rem" in style for style in styles), styles
    await utils_user.should_not_see("Memory")


async def test_status_pane_hides_not_displayed_detail_keys(fake_backend, utils_user):
    """The status pane never stringifies detail keys that are not displayed.

    Only the whitelisted detail keys are rendered: internal bookkeeping such
    as ``retry_count``/``max_retries`` must stay out of the rendered text, at
    the top level and nested, while a displayed key still shows.
    """
    from typing import cast

    from klea_utils.ui.state import StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Details",
        "display": "",
        "summary": "",
        "details": {
            "retry_count": 1,
            "max_retries": 3,
            "route": "task",
            "nested": {"retry_count": 2, "step": 1},
        },
        "preformatted": False,
    }
    chat["state_sections"]["details"] = cast(StateSection, section)

    await utils_user.open("/")
    await utils_user.should_see("route")
    await utils_user.should_see("step")
    await utils_user.should_not_see("retry_count")
    await utils_user.should_not_see("max_retries")


async def test_status_pane_renders_no_empty_details_container(fake_backend, utils_user):
    """A filtered details block never leaves a bare ``{}`` in the pane.

    The not-displayed keys are dropped and the empty containers they leave
    behind are pruned, so a bookkeeping-only plan section renders its plan text
    with no ``{}`` block while a tool-call section with an empty value key
    renders only its legitimate key.
    """

    from klea_utils.ui.state import ensure_chat

    chat = ensure_chat("u", "")
    chat["state_sections"]["plan"] = {
        "heading": "Plan",
        "display": "Step 1: do the thing",
        "summary": "",
        "details": {"retry_count": 1, "max_retries": 3, "details": {}},
        "preformatted": True,
    }
    chat["state_sections"]["tool"] = {
        "heading": "Tool",
        "display": "",
        "summary": "",
        "details": {"arguments": {}, "route": "task"},
        "preformatted": False,
    }

    await utils_user.open("/")

    from nicegui import ui

    rendered = "\n".join(
        str(element.content) for element in utils_user.find(ui.code).elements
    )
    await utils_user.should_see("Step 1: do the thing")
    assert '"route": "task"' in rendered
    assert "{}" not in rendered
    assert "arguments" not in rendered


async def test_status_pane_shows_per_model_usage(fake_backend, utils_user):
    """The pane shows the thread total plus one row per model."""
    from klea_utils.ui.state import (
        TokenUsage,
        TokenUsageByModel,
        ensure_chat,
        format_token_usage,
        format_token_usage_compact,
        format_token_usage_lines,
    )

    chat = ensure_chat("u", "")
    total: TokenUsage = {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cached_tokens": 2,
    }
    by_model: TokenUsageByModel = {
        "anthropic:model-a": TokenUsage(
            input_tokens=6, output_tokens=3, total_tokens=9, cached_tokens=0
        ),
        "openai:model-b": TokenUsage(
            input_tokens=4, output_tokens=1, total_tokens=5, cached_tokens=0
        ),
    }
    chat["token_usage"] = total
    chat["token_usage_by_model"] = by_model

    await utils_user.open("/")

    from nicegui import ui

    # The breakdown lives in the usage collapsible, distinct from the Models
    # group: the total line is its heading and it starts collapsed.
    groups = [
        group
        for group in utils_user.find(ui.expansion).elements
        if "usage-group" in group.classes
    ]
    assert len(groups) == 1, "exactly one usage expansion must render"
    usage_group = groups[0]
    assert usage_group.value is False

    compact_heading = format_token_usage_compact(total)
    assert compact_heading == "10 in/4 out/2 cached"
    labels = {
        getattr(label, "text", "") for label in utils_user.find(ui.label).elements
    }
    assert compact_heading in labels

    # Expanding reveals one provider group per provider, with each model's
    # usage entry nested beneath its provider heading and rendered like the
    # total line.
    usage_group.value = True
    labels = {
        getattr(label, "text", ""): label
        for label in utils_user.find(ui.label).elements
    }

    def _css_classes(label) -> str:
        """Return a rendered label's CSS classes (version tolerant)."""
        classes = getattr(label, "_classes", None)
        if classes is None:
            attr = getattr(label, "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    for model, usage in by_model.items():
        provider, _, model_name = model.partition(":")
        assert provider in labels
        assert model_name in labels
        # The single verbose line is split into separate input and output
        # lines, each rendered on its own and together matching the shared
        # verbose format.
        in_line, out_line = format_token_usage_lines(usage)
        assert in_line in labels
        assert out_line in labels
        assert f"{in_line} / {out_line}" == format_token_usage(usage)
        # The provider heads its own list item; the model is indented by the
        # shared ``pl-4`` class and each usage line sits one level deeper.
        assert "pl-4" not in _css_classes(labels[provider])
        assert "pl-4" in _css_classes(labels[model_name])
        assert "pl-8" in _css_classes(labels[in_line])
        assert "pl-8" in _css_classes(labels[out_line])
    assert not any("{}" in text for text in labels)


async def test_status_pane_divider_closes_the_static_usage_area(
    fake_backend, utils_user
):
    """The divider ends the sticky usage header, above the scrolling sections."""
    from klea_utils.ui.state import TokenUsage, TokenUsageByModel, ensure_chat

    chat = ensure_chat("u", "")
    total: TokenUsage = {
        "input_tokens": 10,
        "output_tokens": 4,
        "total_tokens": 14,
        "cached_tokens": 2,
    }
    by_model: TokenUsageByModel = {
        "anthropic:model-a": TokenUsage(
            input_tokens=10, output_tokens=4, total_tokens=14, cached_tokens=2
        )
    }
    chat["token_usage"] = total
    chat["token_usage_by_model"] = by_model

    await utils_user.open("/")
    await utils_user.should_see("10 in/4 out/2 cached", retries=50)

    def _classes(element) -> str:
        """Return a rendered element's CSS classes (version tolerant)."""
        classes = getattr(element, "_classes", None)
        if classes is None:
            attr = getattr(element, "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    def _descendants(element) -> list:
        """Return *element*'s descendants (version tolerant)."""
        walker = getattr(element, "descendants", None)
        if callable(walker):
            return list(walker())
        slot = getattr(element, "default_slot", None)
        found = []
        for child in list(getattr(slot, "children", []) or []):
            found.append(child)
            found.extend(_descendants(child))
        return found

    sticky = [
        column
        for column in utils_user.find(ui.column).elements
        if "sticky" in _classes(column)
    ]
    assert sticky, "the usage header must be the pane's sticky column"
    header = sticky[0]

    dividers = [node for node in _descendants(header) if isinstance(node, ui.separator)]
    assert len(dividers) == 1, "exactly one divider closes the usage header"
    divider = dividers[0]

    # The divider is the header's last child and directly follows the usage
    # group, so no blank element is mounted between the usage rows and it.
    slot = getattr(header, "default_slot", None)
    children = list(getattr(slot, "children", []) or [])
    usage_index = next(
        index
        for index, child in enumerate(children)
        if "usage-group" in _classes(child)
    )
    assert children[usage_index + 1] is divider
    assert children[-1] is divider

    # The scrolling sections column never carries the divider.
    sections = [
        column
        for column in utils_user.find(ui.column).elements
        if "px-2" in _classes(column) and "pb-2" in _classes(column)
    ]
    assert sections
    assert not any(
        isinstance(node, ui.separator)
        for column in sections
        for node in _descendants(column)
    )


async def test_status_pane_renders_the_role_markers(fake_backend, utils_user):
    """Each role renders its Unicode marker; unknown roles use the plain one."""
    from typing import cast

    from klea_utils.ui.glyphs import UNICODE_GLYPHS
    from klea_utils.ui.state import ContentBlock, StateSection, ensure_chat

    chat = ensure_chat("u", "")
    section = {
        "heading": "Markers",
        "display": "",
        "summary": "",
        "details": {},
        "preformatted": False,
        "content": [
            {
                "kind": "items",
                "items": [
                    {"text": "ok item", "role": "ok"},
                    {"text": "error item", "role": "error"},
                    {"text": "current item", "role": "current"},
                    {"text": "pending item", "role": "pending"},
                    {"text": "unknown role item", "role": "mystery"},
                ],
            }
        ],
    }
    chat["state_sections"]["markers"] = cast(StateSection, section)
    # A two-item section pins the inter-item spacing: every item but the last
    # carries the spacing margin, and the last item carries none.
    spacing_content: list[ContentBlock] = [  # ty: ignore[invalid-assignment]
        {
            "kind": "items",
            "items": [
                {"text": "first spaced item", "role": "ok"},
                {"text": "second spaced item", "role": "ok"},
            ],
        }
    ]
    spacing_section: StateSection = {
        "preformatted": False,
        "heading": "Spacing",
        "display": "",
        "summary": "",
        "details": {},
        "content": spacing_content,
    }
    chat["state_sections"]["spacing"] = spacing_section

    await utils_user.open("/")
    await utils_user.should_see("ok item", retries=50)

    rendered = {
        getattr(label, "text", "") for label in utils_user.find(ui.label).elements
    }
    expected = {
        f"{UNICODE_GLYPHS.marker_ok} ok item",
        f"{UNICODE_GLYPHS.marker_error} error item",
        f"{UNICODE_GLYPHS.marker_current} current item",
        f"{UNICODE_GLYPHS.marker_pending} pending item",
        f"{UNICODE_GLYPHS.marker_plain} unknown role item",
    }
    assert expected <= rendered, f"missing markers: {sorted(expected - rendered)}"

    def _label_classes(text: str) -> str:
        """Return the CSS classes of the rendered label whose text is *text*."""
        matches = [
            element
            for element in utils_user.find(ui.label).elements
            if getattr(element, "text", "") == text
        ]
        assert matches, f"missing label: {text}"
        classes = getattr(matches[0], "_classes", None)
        if classes is None:
            attr = getattr(matches[0], "classes", "")
            classes = attr() if callable(attr) else attr
        if isinstance(classes, (list, tuple, set)):
            return " ".join(str(name) for name in classes)
        return str(classes)

    # Only items followed by another item carry the spacing margin.
    assert "mb-2" in _label_classes(f"{UNICODE_GLYPHS.marker_ok} first spaced item")
    assert "mb-2" not in _label_classes(
        f"{UNICODE_GLYPHS.marker_ok} second spaced item"
    )
