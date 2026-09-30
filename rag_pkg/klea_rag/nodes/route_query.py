#!/usr/bin/env python3
"""
Route query node

File: rag_pkg/klea_rag/nodes/route_query.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from klea_utils.nodes.abstract import AbstractRouterNode

from klea_rag.schemas import RAGState


class RouteQuery(AbstractRouterNode):
    """Route based on Query node results"""

    def __init__(
        self,
        logger: logging.Logger,
        label: str,
        non_domain_chat: bool = False,
    ):
        """Initialise the node.

        :param logger: Logger instance
        :param label: Human-readable label for UI progress display
        :param non_domain_chat: boolean if non domain chat is enabled
        """
        super().__init__(logger, label)
        self.non_domain_chat = non_domain_chat

    def execute(self, state: RAGState):
        """Route based on query domains, set by query classifier node."""
        self._emit_progress()
        self.logger.debug(f"{state =}")
        query_domains = state.query_domains

        # ["undefined"]
        if len(query_domains) == 1 and "undefined" in query_domains:
            if self.non_domain_chat:
                res = "non_domain_query"
            else:
                res = "non_domain_refuse"
        else:
            res = "domain_query"

        self.logger.debug(f"{res = }")
        return res
