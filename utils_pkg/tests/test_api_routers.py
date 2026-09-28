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
from klea_utils.api.credentials import (
    create_credentials_router,
    credential_status,
    mask_secret,
)
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

    async def test_user_defaults_active(self, models_client):
        """Per-session defaults are returned with overridden=True for that scope."""
        response = await models_client.post(
            "/chat/u1/models/overrides/plan", json={"model": "ollama:qwen3"}
        )
        assert response.status_code == 200
        data = (await models_client.get("/chat/u1/models/active")).json()
        assert data["plan"]["model"] == "ollama:qwen3"
        assert data["plan"]["overridden"] is True
        assert data["chat"]["overridden"] is False
        assert "session_overridden" not in data["plan"]

    async def test_chat_active_inherits_user_default(self, models_client):
        """A chat inherits the session default and flags it as session_overridden."""
        await models_client.post(
            "/chat/u1/models/overrides/plan", json={"model": "ollama:qwen3"}
        )
        data = (await models_client.get("/chat/u1/c1/models/active")).json()
        assert data["plan"]["model"] == "ollama:qwen3"
        assert data["plan"]["overridden"] is False
        assert data["plan"]["session_overridden"] is True

    async def test_chat_override_wins_and_promotes(self, models_client):
        """A chat override beats the session default and updates it by default."""
        await models_client.post(
            "/chat/u1/models/overrides/plan", json={"model": "ollama:qwen3"}
        )
        response = await models_client.post(
            "/chat/u1/c1/models/overrides/plan", json={"model": "openai:gpt-4o"}
        )
        assert response.status_code == 200
        assert response.json()["promoted"] is True

        chat = (await models_client.get("/chat/u1/c1/models/active")).json()
        assert chat["plan"]["model"] == "openai:gpt-4o"
        assert chat["plan"]["overridden"] is True
        assert chat["plan"]["session_overridden"] is True

        user = (await models_client.get("/chat/u1/models/active")).json()
        assert user["plan"]["model"] == "openai:gpt-4o"

    async def test_chat_override_promote_disabled(self, models_client):
        """promote_default=False keeps the session default unchanged."""
        await models_client.post(
            "/chat/u1/c1/models/overrides/chat",
            json={"model": "openai:gpt-4o", "promote_default": False},
        )
        user = (await models_client.get("/chat/u1/models/active")).json()
        assert user["chat"]["model"] == "ollama:qwen3:0.6b"

    async def test_clear_chat_override_falls_back_to_default(self, models_client):
        """Clearing a chat override reverts to the session default."""
        await models_client.post(
            "/chat/u1/models/overrides/plan", json={"model": "ollama:qwen3"}
        )
        await models_client.post(
            "/chat/u1/c1/models/overrides/plan",
            json={"model": "openai:gpt-4o", "promote_default": False},
        )
        await models_client.delete("/chat/u1/c1/models/overrides/plan")
        data = (await models_client.get("/chat/u1/c1/models/active")).json()
        assert data["plan"]["model"] == "ollama:qwen3"
        assert data["plan"]["overridden"] is False

    async def test_active_includes_credential_status(
        self, models_app, models_client, monkeypatch
    ):
        """The active config reports how each role's credential is supplied."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        models_app.state.chat_sessions.set_credential("u1", "openai", "", "sk-1234")
        await models_client.post(
            "/chat/u1/models/overrides/chat", json={"model": "openai:gpt-4o"}
        )
        data = (await models_client.get("/chat/u1/models/active")).json()
        assert data["chat"]["credential"]["source"] == "user"
        assert data["chat"]["credential"]["masked"] == "...1234"
        # A fresh user with the default Ollama chat role needs no key.
        default = (await models_client.get("/chat/u2/models/active")).json()
        assert default["chat"]["credential"]["requires_key"] is False
        assert default["chat"]["credential"]["source"] == "none"

    async def test_locked_role_rejected(self, models_client):
        """A non-modifiable role cannot be set at either scope."""
        user = await models_client.post(
            "/chat/u1/models/overrides/guard", json={"model": "x"}
        )
        assert user.status_code == 403
        chat = await models_client.post(
            "/chat/u1/c1/models/overrides/guard", json={"model": "x"}
        )
        assert chat.status_code == 403

    async def test_post_ignores_legacy_api_key(self, models_app, models_client):
        """A legacy api_key in the body is not persisted in the override."""
        await models_client.post(
            "/chat/u1/c1/models/overrides/chat",
            json={"model": "openai:gpt-4o", "api_key": "sk-secret"},
        )
        overrides = models_app.state.chat_sessions.get_overrides("u1", "c1")
        assert overrides["chat"] == {"model": "openai:gpt-4o"}

    async def test_overrides_get_masks_api_key(self, models_app, models_client):
        """GET /models/overrides masks any legacy plaintext api_key."""
        store: SessionStore = models_app.state.chat_sessions
        store.create_chat("u1", "c1")
        store.set_override(
            "u1",
            "c1",
            "chat",
            {"model": "openai:gpt-4o", "api_key": "sk-secret-1234"},
        )
        response = await models_client.get("/chat/u1/c1/models/overrides")
        assert response.status_code == 200
        assert "sk-secret-1234" not in response.text
        assert response.json()["chat"]["api_key"] == "...1234"


def test_mask_secret():
    """mask_secret exposes only the last four characters."""
    assert mask_secret("sk-secret-1234") == "...1234"
    assert mask_secret("") == ""
    assert mask_secret(None) == ""


@pytest.fixture
def credentials_app(tmp_path):
    """FastAPI app exposing the credentials router over a real store."""
    _app = FastAPI()
    _app.state.is_ready = True
    _app.state.chat_sessions = SessionStore(str(tmp_path / "creds.db"))
    _app.include_router(create_credentials_router())
    yield _app
    _app.state.chat_sessions.close()


@pytest.fixture
async def credentials_client(credentials_app):
    transport = ASGITransport(app=credentials_app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test"
    ) as _client:
        yield _client


class TestCredentials:
    """Credentials endpoint tests (per-user provider credentials)."""

    def setup_method(self):
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")

    async def test_list_empty(self, credentials_client):
        """A user with no stored credentials lists nothing."""
        response = await credentials_client.get("/credentials/u1")
        assert response.status_code == 200
        assert response.json() == []

    async def test_put_then_list_masked(self, credentials_client):
        """A stored credential is listed masked, never raw."""
        response = await credentials_client.put(
            "/credentials/u1",
            json={"provider": "OpenAI", "secret": "sk-secret-1234"},
        )
        assert response.status_code == 200
        assert response.json()["provider"] == "openai"

        response = await credentials_client.get("/credentials/u1")
        assert response.status_code == 200
        assert "sk-secret-1234" not in response.text
        items = response.json()
        assert len(items) == 1
        assert items[0]["provider"] == "openai"
        assert items[0]["source"] == "user"
        assert items[0]["masked"] == "...1234"

    async def test_put_rejects_empty_fields(self, credentials_client):
        """Empty provider/secret are rejected."""
        r1 = await credentials_client.put(
            "/credentials/u1", json={"provider": "  ", "secret": "sk"}
        )
        assert r1.status_code == 422
        r2 = await credentials_client.put(
            "/credentials/u1", json={"provider": "openai", "secret": "   "}
        )
        assert r2.status_code == 422

    async def test_delete_credential(self, credentials_client):
        """DELETE removes the provider's credential."""
        await credentials_client.put(
            "/credentials/u1", json={"provider": "openai", "secret": "sk"}
        )
        response = await credentials_client.delete("/credentials/u1/openai")
        assert response.status_code == 200
        assert (await credentials_client.get("/credentials/u1")).json() == []

    async def test_endpoint_scoped_credentials(self, credentials_client):
        """Custom endpoints under one provider are distinct scopes."""
        await credentials_client.put(
            "/credentials/u1",
            json={"provider": "custom", "endpoint": "https://a/v1", "secret": "sk-a"},
        )
        await credentials_client.put(
            "/credentials/u1",
            json={"provider": "custom", "endpoint": "https://b/v1", "secret": "sk-b"},
        )
        items = (await credentials_client.get("/credentials/u1")).json()
        scopes = {(i["provider"], i["endpoint"]) for i in items}
        assert scopes == {
            ("custom", "https://a/v1"),
            ("custom", "https://b/v1"),
        }

    def test_credential_status_user(self, credentials_app):
        """A stored credential reports source=user with a masked value."""
        store: SessionStore = credentials_app.state.chat_sessions
        store.set_credential("u1", "openai", "", "sk-secret-1234")
        status = credential_status(store, "u1", "openai:gpt-4o")
        assert status["source"] == "user"
        assert status["requires_key"] is True
        assert status["masked"] == "...1234"

    def test_credential_status_env(self, credentials_app, monkeypatch):
        """With no stored credential, a set env var reports source=env."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-env-9999")
        status = credential_status(
            credentials_app.state.chat_sessions, "u1", "openai:gpt-4o"
        )
        assert status["source"] == "env"
        assert status["masked"] == "...9999"

    def test_credential_status_none(self, credentials_app, monkeypatch):
        """With neither stored nor env, source=none."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        status = credential_status(
            credentials_app.state.chat_sessions, "u1", "openai:gpt-4o"
        )
        assert status["source"] == "none"
        assert status["masked"] == ""

    def test_credential_status_local_provider(self, credentials_app):
        """A local provider needs no key and is not 'missing'."""
        status = credential_status(
            credentials_app.state.chat_sessions, "u1", "ollama:qwen3"
        )
        assert status["requires_key"] is False
        assert status["source"] == "none"
