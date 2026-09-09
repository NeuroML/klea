#!/usr/bin/env python3
"""
Tests for the generic shared API routers (health and models).

File: tests/test_api_routers.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import logging
from typing import ClassVar

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

# https://www.python-httpx.org/advanced/transports/#asgi-transport
# Talk to FastAPI in-process without a real server.
# httpx.AsyncClient is needed over TestClient so
# streaming responses can be consumed via
# ``client.stream()`` + ``aiter_lines()``.
from klea_utils.api.health import create_health_router
from klea_utils.api.models import create_models_router
from klea_utils.api.sessions_db import SessionStore
from klea_utils.llm import LLMModel


@pytest.fixture
def app(tmp_path):
    """Create a minimal FastAPI app with health router and real SessionStore."""
    _app = FastAPI()
    _app.state.is_ready = True
    _app.state.chat_sessions = SessionStore(str(tmp_path / "sessions.db"))
    _app.include_router(create_health_router())
    yield _app
    _app.state.chat_sessions.close()


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as _client:
        yield _client


class TestHealth:
    """Health endpoint tests."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def test_liveness(self, client):
        self.logger.info("Checking /health/live returns alive")
        response = await client.get("/health/live")
        self.logger.info(f"Status: {response.status_code}, body: {response.json()}")
        assert response.status_code == 200
        assert response.json() == {"status": "alive"}

    async def test_readiness_ready(self, client):
        self.logger.info("Checking /health/ready returns ready when is_ready=True")
        response = await client.get("/health/ready")
        self.logger.info(f"Status: {response.status_code}, body: {response.json()}")
        assert response.status_code == 200
        assert response.json() == {"status": "ready"}

    async def test_readiness_not_ready(self, app, client):
        self.logger.info("Checking /health/ready returns 503 when is_ready=False")
        app.state.is_ready = False
        self.logger.debug("Set app.state.is_ready = False")
        response = await client.get("/health/ready")
        self.logger.info(f"Status: {response.status_code}, body: {response.text!r}")
        assert response.status_code == 503
        assert response.text == "Service not ready"


@pytest.fixture
def models_app(tmp_path):
    """FastAPI app with a graph exposing ``llm_models`` with required flags."""
    _app = FastAPI()
    _app.state.is_ready = True
    _app.state.chat_sessions = SessionStore(str(tmp_path / "models.db"))

    class _Graph:
        llm_models: ClassVar[dict[str, LLMModel]] = {
            "chat": LLMModel(instance=None, model_name="ollama:qwen3:0.6b"),
            "guard": LLMModel(
                instance=None, model_name="", required=False, modifiable=False
            ),
            "plan": LLMModel(instance=None, model_name="", required=True),
        }

    _app.state.graph = _Graph()
    _app.include_router(create_models_router())
    yield _app
    _app.state.chat_sessions.close()


@pytest.fixture
async def models_client(models_app):
    transport = ASGITransport(app=models_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as _client:
        yield _client


class TestModels:
    """Models endpoint tests (active model config)."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def test_active_models_includes_required_flag(self, models_client):
        """GET /models/active reports required and modifiable per role."""
        response = await models_client.get(
            "/chat/u1/c1/models/active",
        )
        assert response.status_code == 200
        data = response.json()
        assert data["chat"]["required"] is True
        assert data["chat"]["modifiable"] is True
        assert data["chat"]["model"] == "ollama:qwen3:0.6b"
        # Guard is optional and locked; plan is required but not set.
        assert data["guard"]["required"] is False
        assert data["guard"]["modifiable"] is False
        assert data["plan"]["required"] is True
        assert data["plan"]["model"] == ""
