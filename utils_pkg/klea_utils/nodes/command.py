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

from klea_utils.commands.common import Command, CommandRegistry, parse_command
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
        """Dispatch the command in ``state.query`` and end the run."""
        self._emit_progress()
        query = getattr(state, "query", "")
        parsed = parse_command(query)
        command = self._registry.get(parsed.name) if parsed and parsed.name else None
        if parsed is None or command is None:
            self.logger.warning(f"command node reached without a command: {query!r}")
            return {"message_for_user": "No command was recognised."}
        handler = self._handlers.get(command.name)
        if not command.implemented or handler is None:
            message = f"Command /{command.name} is not implemented yet."
            self._emit_inspect(command, message, {})
            return {"message_for_user": message}
        self.logger.debug(f"running command /{command.name} args={parsed.args}")
        result = handler(state, parsed)
        self._emit_inspect(command, result.summary or result.message, result.details)
        updates: dict[str, Any] = dict(result.updates)
        updates["message_for_user"] = result.message
        if command.persists != "message":
            self._drop_command_message(state, query, updates)
        return updates

    @staticmethod
    def _drop_command_message(
        state: BaseModel, query: str, updates: dict[str, Any]
    ) -> None:
        """Remove the command turn init appended, unless it is kept.

        ``InitGraphState`` appends the query to ``messages``; an ephemeral
        command must not appear in the LLM's history.  Only strips when the
        last message is the command itself (guards a resume).
        """
        messages = list(getattr(state, "messages", []) or [])
        if messages and getattr(messages[-1], "content", None) == query:
            updates["messages"] = messages[:-1]

    def _emit_inspect(
        self, command: Command, summary: str, details: dict[str, Any]
    ) -> None:
        """Emit an ``inspect`` event recording the command and its outcome."""
        info = NodeStreamData(
            heading=f"/{command.name}", summary=summary, details=details
        )
        self.write_custom_stream(
            NodeStreamEvent(type="inspect", node=self.label, data=info).model_dump()
        )
