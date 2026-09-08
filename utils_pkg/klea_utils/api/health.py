#!/usr/bin/env python3
"""
Shared health check endpoint factory for Klea packages.

File: klea_utils/api/health.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging

from fastapi import APIRouter, Request, Response, status

logger = logging.getLogger(__name__)


def create_health_router() -> APIRouter:
    """Create an APIRouter with ``/health/live`` and ``/health/ready`` endpoints."""
    router = APIRouter()

    @router.get("/health/live")
    async def liveness():
        logger.debug("/health/live -> alive")
        return {"status": "alive"}

    @router.get("/health/ready")
    async def readiness(request: Request):
        is_ready = getattr(request.app.state, "is_ready", False)
        logger.debug("/health/ready -> %s", "ready" if is_ready else "not ready")

        if is_ready:
            return {"status": "ready"}

        return Response(
            content="Service not ready",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    return router
