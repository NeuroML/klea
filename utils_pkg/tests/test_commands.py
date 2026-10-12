#!/usr/bin/env python3
"""
Tests for the shared session-command framework.

File: tests/test_commands.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import pytest
from klea_utils.commands.common import (
    CAP_LOCAL_FS,
    Command,
    CommandContext,
    CommandRegistry,
    CommandResult,
    ParsedCommand,
    Subcommand,
    parse_command,
    render_help,
    resolve_subcommand,
    unescape_command,
)


class TestParseCommand:
    def test_non_command_returns_none(self):
        assert parse_command("hello world") is None
        assert parse_command("") is None
        assert parse_command("   ") is None
        assert parse_command("a /help") is None

    def test_escaped_slash_is_not_a_command(self):
        assert parse_command("//help") is None

    def test_bare_command(self):
        parsed = parse_command("/help")
        assert parsed is not None
        assert parsed.name == "help"
        assert parsed.args == []
        assert parsed.raw_args == ""
        assert parsed.error == ""

    def test_name_is_lowercased(self):
        parsed = parse_command("/Help")
        assert parsed is not None
        assert parsed.name == "help"

    def test_leading_whitespace_is_not_a_command(self):
        # A command must start with ``/`` as its literal first character.
        assert parse_command("  /help") is None
        assert parse_command("\n/help") is None

    def test_trailing_whitespace_is_tolerated(self):
        parsed = parse_command("/help  ")
        assert parsed is not None
        assert parsed.name == "help"

    def test_args_and_raw_args(self):
        parsed = parse_command("/mode scientific")
        assert parsed is not None
        assert parsed.name == "mode"
        assert parsed.args == ["scientific"]
        assert parsed.raw_args == "scientific"

    def test_quoted_args_group(self):
        parsed = parse_command('/run "a b" c')
        assert parsed is not None
        assert parsed.args == ["a b", "c"]

    def test_missing_name_is_an_error(self):
        parsed = parse_command("/")
        assert parsed is not None
        assert parsed.name == ""
        assert parsed.error != ""

    def test_malformed_quoting_is_an_error(self):
        parsed = parse_command('/run "unclosed')
        assert parsed is not None
        assert parsed.name == "run"
        assert parsed.error != ""


class TestUnescapeCommand:
    def test_escaped_command_is_unescaped(self):
        assert unescape_command("//help me") == "/help me"

    def test_plain_command_is_not_escaped(self):
        assert unescape_command("/help") is None

    def test_non_command_is_not_escaped(self):
        assert unescape_command("hello") is None

    def test_leading_whitespace_is_not_an_escape(self):
        assert unescape_command("  //help") is None


class TestCommandResult:
    def test_ok_drops_empty_lines(self):
        result = CommandResult.ok("a", "", "b")
        assert result.output == ["a", "b"]
        assert result.error == ""
        assert result.handled is True

    def test_fail_sets_error(self):
        result = CommandResult.fail("boom")
        assert result.error == "boom"
        assert result.output == []

    def test_info_sets_notice(self):
        assert CommandResult.info("done").notice == "done"


def _parse(text: str):
    """Parse *text*, asserting it is a command (for the type checker)."""
    parsed = parse_command(text)
    assert parsed is not None
    return parsed


def _registry() -> CommandRegistry:
    registry = CommandRegistry()
    registry.register(
        Command(
            name="help",
            summary="list commands",
            aliases=("h",),
            handler=lambda ctx, parsed: CommandResult.ok("help"),
        )
    )
    registry.register(
        Command(
            name="mode",
            summary="set the mode",
            side="server",
            klass="session-state",
            persists="checkpoint",
            while_streaming="block",
            arg_hint="[general|scientific]",
        )
    )
    registry.register(
        Command(
            name="open",
            summary="open a client file",
            capabilities=frozenset({CAP_LOCAL_FS}),
        )
    )
    registry.register(
        Command(
            name="upload",
            summary="attach a client file (not implemented)",
            capabilities=frozenset({CAP_LOCAL_FS}),
            implemented=False,
        )
    )
    return registry


class TestCommandRegistry:
    def test_get_by_name_and_alias_case_insensitively(self):
        registry = _registry()
        for name in ("help", "H", "HELP"):
            command = registry.get(name)
            assert command is not None
            assert command.name == "help"
        assert registry.get("nope") is None

    def test_all_is_sorted(self):
        names = [c.name for c in _registry().all()]
        assert names == ["help", "mode", "open", "upload"]

    def test_duplicate_name_raises(self):
        registry = _registry()
        with pytest.raises(ValueError):
            registry.register(Command(name="help", summary="dup"))

    def test_duplicate_alias_raises(self):
        registry = _registry()
        with pytest.raises(ValueError):
            registry.register(Command(name="other", summary="x", aliases=("h",)))

    def test_invalid_name_raises(self):
        registry = CommandRegistry()
        with pytest.raises(ValueError):
            registry.register(Command(name="bad name", summary="x"))

    def test_available_excludes_unimplemented(self):
        # ``upload`` is a stub; it is never published.
        assert [c.name for c in _registry().available()] == ["help", "mode", "open"]

    def test_available_filters_by_capabilities(self):
        registry = _registry()
        assert [c.name for c in registry.available([])] == ["help", "mode"]
        assert [c.name for c in registry.available([CAP_LOCAL_FS])] == [
            "help",
            "mode",
            "open",
        ]


class TestDispatch:
    def test_unknown_command(self):
        result = _registry().dispatch(_parse("/nope"), CommandContext())
        assert "Unknown command" in result.error

    def test_stub_is_not_implemented(self):
        result = _registry().dispatch(_parse("/upload x"), CommandContext())
        assert "not implemented" in result.error

    def test_parse_error_propagates(self):
        result = _registry().dispatch(_parse("/"), CommandContext())
        assert result.error != ""

    def test_handler_is_called_with_context_and_parsed(self):
        seen = {}

        def handler(ctx, parsed):
            seen["ctx"] = ctx
            seen["parsed"] = parsed
            return CommandResult.ok("ran")

        registry = CommandRegistry()
        registry.register(Command(name="ping", summary="p", handler=handler))
        ctx = CommandContext(user_id="u1", chat_id="c1")
        result = registry.dispatch(_parse("/ping now"), ctx)
        assert result.output == ["ran"]
        assert seen["ctx"] is ctx
        assert seen["parsed"].args == ["now"]


class TestRenderHelp:
    def test_lists_available_commands(self):
        result = render_help(_registry(), [])
        joined = "\n".join(result.output)
        assert "/help" in joined
        assert "/mode" in joined
        # ``/open`` needs a capability the frontend lacks; ``/upload`` is a stub.
        assert "/open" not in joined
        assert "/upload" not in joined

    def test_stub_is_never_shown(self):
        result = render_help(_registry(), [CAP_LOCAL_FS])
        joined = "\n".join(result.output)
        assert "/open" in joined  # capability satisfied
        assert "/upload" not in joined  # still a stub, never published
        assert "not implemented" not in joined

    def test_detail_for_one_command(self):
        result = render_help(_registry(), [], name="mode")
        joined = "\n".join(result.output)
        assert "`/mode [general|scientific]`" in joined
        assert "set the mode" in joined

    def test_detail_unknown(self):
        result = render_help(_registry(), [], name="nope")
        assert "Unknown command" in result.error


def _sessions_command() -> Command:
    """A command with first-class verbs (for the subcommand tests)."""
    return Command(
        name="sessions",
        summary="switch or manage sessions",
        subcommands=(
            Subcommand(name="list", summary="list the sessions"),
            Subcommand(name="switch", summary="switch session", arg_hint="<n|id>"),
            Subcommand(name="delete", summary="delete session", arg_hint="<n|id>"),
        ),
        handler=lambda ctx, parsed: CommandResult.ok(f"{parsed.verb}:{parsed.args}"),
    )


class TestSubcommands:
    def test_plain_command_is_returned_unchanged(self):
        parsed = _parse("/help")
        assert resolve_subcommand(Command(name="help", summary="h"), parsed) is parsed

    def test_bare_command_has_empty_verb(self):
        resolved = resolve_subcommand(_sessions_command(), _parse("/sessions"))
        assert isinstance(resolved, ParsedCommand)
        assert resolved.verb == ""
        assert resolved.args == []

    def test_known_verb_is_resolved_and_args_stripped(self):
        resolved = resolve_subcommand(_sessions_command(), _parse("/sessions switch 2"))
        assert isinstance(resolved, ParsedCommand)
        assert resolved.verb == "switch"
        assert resolved.args == ["2"]
        assert resolved.raw_args == "2"

    def test_verb_with_no_args(self):
        resolved = resolve_subcommand(_sessions_command(), _parse("/sessions list"))
        assert isinstance(resolved, ParsedCommand)
        assert resolved.verb == "list"
        assert resolved.args == []

    def test_unknown_verb_returns_an_error(self):
        error = resolve_subcommand(_sessions_command(), _parse("/sessions bogus"))
        assert isinstance(error, str)
        assert "Unknown subcommand" in error
        assert "switch" in error

    def test_dispatch_routes_the_verb(self):
        registry = CommandRegistry()
        registry.register(_sessions_command())
        result = registry.dispatch(_parse("/sessions switch 3"), CommandContext())
        assert result.output == ["switch:['3']"]

    def test_dispatch_rejects_an_unknown_verb(self):
        registry = CommandRegistry()
        registry.register(_sessions_command())
        result = registry.dispatch(_parse("/sessions bogus"), CommandContext())
        assert "Unknown subcommand" in result.error

    def test_duplicate_subcommand_raises(self):
        registry = CommandRegistry()
        with pytest.raises(ValueError):
            registry.register(
                Command(
                    name="x",
                    summary="x",
                    subcommands=(
                        Subcommand(name="a", summary="a"),
                        Subcommand(name="a", summary="again"),
                    ),
                )
            )

    def test_invalid_subcommand_name_raises(self):
        registry = CommandRegistry()
        with pytest.raises(ValueError):
            registry.register(
                Command(
                    name="x",
                    summary="x",
                    subcommands=(Subcommand(name="bad name", summary="s"),),
                )
            )


class TestRenderHelpSubcommands:
    def test_detail_lists_subcommands(self):
        registry = CommandRegistry()
        registry.register(_sessions_command())
        result = render_help(registry, [], name="sessions")
        joined = "\n".join(result.output)
        assert "Subcommands:" in joined
        assert "/sessions switch <n|id>" in joined
        assert "switch session" in joined


class TestSubcommandMetadata:
    def test_round_trips_subcommands(self):
        restored = Command.from_metadata(_sessions_command().metadata())
        assert [sub.name for sub in restored.subcommands] == [
            "list",
            "switch",
            "delete",
        ]
        assert restored.subcommands[1].arg_hint == "<n|id>"
        assert restored.subcommands[1].summary == "switch session"

    def test_metadata_shape(self):
        meta = _sessions_command().metadata()
        assert meta["subcommands"] == [
            {"name": "list", "summary": "list the sessions", "arg_hint": ""},
            {"name": "switch", "summary": "switch session", "arg_hint": "<n|id>"},
            {"name": "delete", "summary": "delete session", "arg_hint": "<n|id>"},
        ]


class TestCommandMetadata:
    def test_from_metadata_round_trips(self):
        command = Command(
            name="mode",
            summary="set the mode",
            side="server",
            klass="session-state",
            aliases=("m",),
            arg_hint="[x]",
            while_streaming="block",
            persists="checkpoint",
            capabilities=frozenset({CAP_LOCAL_FS}),
        )
        restored = Command.from_metadata(command.metadata())
        assert restored.name == "mode"
        assert restored.side == "server"
        assert restored.aliases == ("m",)
        assert restored.while_streaming == "block"
        assert restored.persists == "checkpoint"
        assert restored.capabilities == frozenset({CAP_LOCAL_FS})
        assert restored.handler is None

    def test_metadata_shape(self):
        command = Command(
            name="mode",
            summary="set the mode",
            side="server",
            klass="session-state",
            aliases=("m",),
            arg_hint="[x]",
            while_streaming="block",
            persists="checkpoint",
            capabilities=frozenset({CAP_LOCAL_FS}),
        )
        meta = command.metadata()
        assert meta["name"] == "mode"
        assert meta["aliases"] == ["m"]
        assert meta["side"] == "server"
        assert meta["klass"] == "session-state"
        assert meta["while_streaming"] == "block"
        assert meta["persists"] == "checkpoint"
        assert meta["capabilities"] == [CAP_LOCAL_FS]
        assert meta["implemented"] is True
        assert "handler" not in meta
