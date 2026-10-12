#!/usr/bin/env python3
"""
Command node for the session-command framework (ADR-0047).

Commands are user-invoked tool calls; this node is the user-facing
counterpart of the tools picker/caller.  It parses a leading ``/`` command
from ``state.query``, dispatches it to an app-provided graph handler, emits an
``inspect`` event for provenance, and ends the run with the handler's message.
A command that is not ``persists: message`` is kept out of the LLM's message
history (the graph-state change still persists in the checkpoint).

See ``devdocs/system/session-commands.md``; ADR-0047.

File: klea_utils/nodes/command.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from collections.abc import Mapping
from typing import Any, override

from pydantic import BaseModel

from klea_utils.commands.common import (
    Command,
    CommandRegistry,
    parse_command,
    resolve_subcommand,
)
from klea_utils.commands.graph import GraphCommandHandler
from klea_utils.nodes.abstract import (
    AbstractLangGraphNode,
    NodeStreamData,
    NodeStreamEvent,
)
from klea_utils.nodes.context import NodeContext


class CommandNode(AbstractLangGraphNode[BaseModel, dict[str, Any], NodeContext]):
    """Execute a graph (server-side) command and end the run.

    Constructed with the app's :class:`CommandRegistry` (metadata) and its
    graph handler map.  A command with no handler, or one marked
    ``implemented=False``, replies that it is not implemented yet.
    """

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        *,
        registry: CommandRegistry,
        handlers: Mapping[str, GraphCommandHandler],
    ) -> None:
        """Initialise with the command registry and graph handlers.

        :param logger: Logger instance.
        :param label: Human-readable label for UI progress display.
        :param registry: The app's command catalogue.
        :param handlers: Graph handlers, keyed by canonical command name.
        """
        super().__init__(logger, label)
        self._registry = registry
        self._handlers = handlers

    @override
    async def execute(self, state: BaseModel) -> dict[str, Any]:
        """Dispatch the command in ``state.query`` and end the run.

        An executable command runs (its ``inspect`` event records the effect).
        An unknown, client-side, not-yet-implemented, or malformed command is
        rejected with a direct user message; the inspect event is reserved for
        a command's actual effect, not for rejections.
        """
        self._emit_progress()
        query = getattr(state, "query", "")
        self.logger.debug(f"command node dispatch: {query = }")
        parsed = parse_command(query)
        if parsed is None:
            # Defensive: the router only sends command queries here.
            self.logger.warning(f"command node reached without a command: {query!r}")
            return {"message_for_user": "No command was recognised."}
        if parsed.error:
            return self._reject(state, query, parsed.error)
        command = self._registry.get(parsed.name)
        if command is None:
            self.logger.debug(f"unknown command: {parsed.name}")
            return self._reject(
                state,
                query,
                f"Unknown command: /{parsed.name}. Type /help for the list.",
            )
        handler = self._handlers.get(command.name)
        if not command.implemented or handler is None:
            self.logger.debug(f"unimplemented command: {command.name}")
            return self._reject(
                state, query, f"Command /{command.name} is not implemented yet."
            )
        resolved = resolve_subcommand(command, parsed)
        if isinstance(resolved, str):
            self.logger.debug(f"command /{command.name}: {resolved}")
            return self._reject(state, query, resolved)
        self.logger.debug(
            f"running command /{command.name} {resolved.verb = } args={resolved.args}"
        )
        result = handler(state, resolved)
        self._emit_inspect(command, result.summary or result.message, result.details)
        updates: dict[str, Any] = dict(result.updates)
        updates["message_for_user"] = result.message
        if command.persists != "message":
            self._drop_command_message(state, query, updates)
        self.logger.debug(
            f"command /{command.name} done: {command.persists = } "
            f"{len(result.message) = }"
        )
        return updates

    def _reject(self, state: BaseModel, query: str, message: str) -> dict[str, Any]:
        """Return a direct rejection reply and drop the command turn."""
        self.logger.debug(f"rejecting command: {message!r}")
        updates: dict[str, Any] = {"message_for_user": message}
        self._drop_command_message(state, query, updates)
        return updates

    def _drop_command_message(
        self, state: BaseModel, query: str, updates: dict[str, Any]
    ) -> None:
        """Remove the command turn init appended, unless it is kept.

        ``InitGraphState`` appends the query to ``messages``; an ephemeral
        command must not appear in the LLM's history.  Only strips when the
        last message is the command itself (guards a resume).
        """
        messages = list(getattr(state, "messages", []) or [])
        if messages and getattr(messages[-1], "content", None) == query:
            updates["messages"] = messages[:-1]
            self.logger.debug("dropped the command turn from message history")

    def _emit_inspect(
        self, command: Command, summary: str, details: dict[str, Any]
    ) -> None:
        """Emit an ``inspect`` event recording the command and its outcome."""
        self.logger.debug(f"emitting inspect for /{command.name}: {summary!r}")
        info = NodeStreamData(
            heading=f"/{command.name}", summary=summary, details=details
        )
        self.write_custom_stream(
            NodeStreamEvent(type="inspect", node=self.label, data=info).model_dump()
        )
