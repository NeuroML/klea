#!/usr/bin/env python3
"""
Guard router node for routing based on guard decision

File: klea_utils/nodes/guard_router.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import override

from pydantic import BaseModel

from .abstract import AbstractRouterNode


class GuardRouterNode(AbstractRouterNode):
    """Router node that routes based on guard decision.

    Reads the guard_decision from state and returns routing label:
    - "safe" -> continue to next node
    - "unsafe" -> decline to respond node
    """

    def __init__(self, logger: logging.Logger, label: str):
        """Initialise the guard router.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        """
        super().__init__(logger, label)

    @override
    async def execute(self, state: BaseModel) -> str:
        """Route based on guard_decision in state.

        :param state: Current graph state
        :returns: Routing label ("safe" or "unsafe")
        """
        self._emit_progress()
        self.logger.debug(f"{state = }")

        guard_decision = getattr(state, "guard_decision", "safe")
        self.logger.debug(f"{guard_decision = }")

        return guard_decision
