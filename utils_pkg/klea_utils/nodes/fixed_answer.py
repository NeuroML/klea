#!/usr/bin/env python3
"""
Provide a fixed answer.

File: rag_pkg/klea_rag/nodes/fixed_answer.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import Any, override

from pydantic import BaseModel

from klea_utils.nodes.abstract import AbstractLangGraphNode


class FixedAnswer(AbstractLangGraphNode[BaseModel, dict[str, Any]]):
    """Provide a fixed answer"""

    def __init__(
        self, logger: logging.Logger, label: str, state_attr: str, message: str
    ):
        """Initialise with logger and message to return.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param message: str message to return
        """
        super().__init__(logger, label)
        self.message = message
        self.state_attr = state_attr

    @override
    async def execute(self, state: BaseModel) -> dict[str, Any]:
        """Return fixed message."""
        self._emit_progress()
        self.logger.debug({self.state_attr: self.message})
        return {self.state_attr: self.message}
