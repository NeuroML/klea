"""Offline tests for the model-override helper (:mod:`klea_eval.run_config`).

Everything here is injected: the HTTP client is a stub that records its calls
and replays canned responses, and ``env`` is always passed explicitly so the
process environment (and the network) is never touched.  The tests carry the
``eval`` marker only.
"""

from __future__ import annotations

from typing import Any

import pytest
from klea_eval.run_config import ModelOverrideError, apply_run_models

pytestmark = pytest.mark.eval

_BASE_URL = "http://backend.test"
_USER_ID = "user-1"
_CHAT_ID = "chat-1"
_PROVIDER = "openai"
_ACTIVE_URL = f"{_BASE_URL}/chat/{_USER_ID}/{_CHAT_ID}/models/active"
_CREDENTIAL_URL = f"{_BASE_URL}/credentials/{_USER_ID}"


class _Response:
    """Minimal stand-in for an ``httpx.Response``."""

    def __init__(
        self, status_code: int = 200, payload: Any = None, text: str = ""
    ) -> None:
        self.status_code = status_code
        self.payload = payload
        self.text = text

    def json(self) -> Any:
        """Return the canned JSON payload, or raise when there is none."""
        if self.payload is None:
            raise ValueError("the response carries no JSON body")
        return self.payload


class _StubClient:
    """Injected client that records ``(method, url, json)`` and replays answers."""

    def __init__(self, responses: list[_Response]) -> None:
        self._responses = list(responses)
        self.calls: list[tuple[str, str, Any]] = []

    def _dispatch(self, method: str, url: str, body: Any) -> _Response:
        self.calls.append((method, url, body))
        if not self._responses:
            raise AssertionError(f"unexpected request: {method} {url}")
        return self._responses.pop(0)

    def get(self, url: str, **kwargs: Any) -> _Response:
        """Record and answer a GET request."""
        return self._dispatch("GET", url, kwargs.get("json"))

    def post(self, url: str, **kwargs: Any) -> _Response:
        """Record and answer a POST request."""
        return self._dispatch("POST", url, kwargs.get("json"))

    def put(self, url: str, **kwargs: Any) -> _Response:
        """Record and answer a PUT request."""
        return self._dispatch("PUT", url, kwargs.get("json"))


def _override_url(role: str) -> str:
    """Return the override URL of ``role`` on the stub chat."""
    return f"{_BASE_URL}/chat/{_USER_ID}/{_CHAT_ID}/models/overrides/{role}"


def _key_env(provider: str, secret: str) -> dict[str, str]:
    """Return an ``env`` mapping carrying the API key ``provider`` needs.

    The env var name is taken from the same helper the module under test uses,
    so the mapping stays correct if the naming convention ever changes.
    """
    from klea_utils.llm import provider_api_key_env

    name = provider_api_key_env(provider)
    assert isinstance(name, str) and name.strip(), (
        f"provider '{provider}' is expected to need an API key"
    )
    return {name.strip(): secret}


def test_known_role_is_discovered_and_its_override_is_set() -> None:
    """A role present in the active map is discovered and overridden."""
    active = {"chat": {"model": "openai:gpt-4o"}}
    client = _StubClient(
        [
            _Response(200, active),
            _Response(200, {}),
            _Response(200, active),
        ]
    )

    resolved = apply_run_models(
        client,
        _BASE_URL,
        _USER_ID,
        _CHAT_ID,
        {"chat": "openai:gpt-4o"},
        env={},
    )

    assert resolved == {"chat": "openai:gpt-4o"}
    assert client.calls == [
        ("GET", _ACTIVE_URL, None),
        (
            "POST",
            _override_url("chat"),
            {"model": "openai:gpt-4o", "promote_default": False},
        ),
        ("GET", _ACTIVE_URL, None),
    ]


def test_unknown_role_is_rejected_and_lists_the_exposed_roles() -> None:
    """A requested role the backend does not expose fails before any push."""
    active = {
        "chat": {"model": "openai:gpt-4o"},
        "judge": {"model": "openai:gpt-4o-mini"},
    }
    client = _StubClient([_Response(200, active)])

    with pytest.raises(ModelOverrideError) as excinfo:
        apply_run_models(
            client,
            _BASE_URL,
            _USER_ID,
            _CHAT_ID,
            {"narrator": "openai:gpt-4o"},
            env={},
        )

    error = excinfo.value
    assert error.role == "narrator"
    message = str(error)
    assert "unknown role" in message
    assert "chat" in message
    assert "judge" in message
    assert [call[0] for call in client.calls] == ["GET"]


def test_provider_credential_is_pushed_for_the_run_user() -> None:
    """The provider secret is stored at ``/credentials/<user_id>`` verbatim."""
    env = _key_env(_PROVIDER, "sk-secret")
    active = {"chat": {"model": "openai:gpt-4o"}}
    client = _StubClient(
        [
            _Response(200, active),
            _Response(200, {}),
            _Response(200, {}),
            _Response(200, active),
        ]
    )

    apply_run_models(
        client,
        _BASE_URL,
        _USER_ID,
        _CHAT_ID,
        {"chat": "openai:gpt-4o"},
        env=env,
    )

    expected = (
        "PUT",
        _CREDENTIAL_URL,
        {"provider": _PROVIDER, "endpoint": "", "secret": "sk-secret"},
    )
    assert expected in client.calls
    assert [call[0] for call in client.calls] == ["GET", "PUT", "POST", "GET"]


def test_absent_environment_key_skips_the_provider() -> None:
    """A provider with no key in ``env`` is skipped, never pushed empty."""
    active = {"chat": {"model": "openai:gpt-4o"}}
    client = _StubClient(
        [
            _Response(200, active),
            _Response(200, {}),
            _Response(200, active),
        ]
    )

    apply_run_models(
        client,
        _BASE_URL,
        _USER_ID,
        _CHAT_ID,
        {"chat": "openai:gpt-4o"},
        env={},
    )

    assert [call[0] for call in client.calls] == ["GET", "POST", "GET"]
    assert not [call for call in client.calls if call[0] == "PUT"]


def test_one_credential_is_pushed_per_provider() -> None:
    """One provider key serving two roles is pushed exactly once."""
    env = _key_env(_PROVIDER, "sk-secret")
    active = {
        "chat": {"model": "openai:gpt-4o"},
        "judge": {"model": "openai:gpt-4o-mini"},
    }
    client = _StubClient(
        [
            _Response(200, active),
            _Response(200, {}),
            _Response(200, {}),
            _Response(200, {}),
            _Response(200, active),
        ]
    )

    apply_run_models(
        client,
        _BASE_URL,
        _USER_ID,
        _CHAT_ID,
        {"chat": "openai:gpt-4o", "judge": "openai:gpt-4o-mini"},
        env=env,
    )

    assert [call[0] for call in client.calls] == [
        "GET",
        "PUT",
        "POST",
        "POST",
        "GET",
    ]
    pushes = [call for call in client.calls if call[0] == "PUT"]
    assert len(pushes) == 1
    assert pushes[0][2]["provider"] == _PROVIDER


def test_rejected_override_propagates_status_and_detail() -> None:
    """A 403 on the override POST surfaces as a typed, detailed error."""
    active = {"chat": {"model": "openai:gpt-4o"}}
    client = _StubClient(
        [
            _Response(200, active),
            _Response(
                403,
                {"detail": "model not permitted"},
                text='{"detail": "model not permitted"}',
            ),
        ]
    )

    with pytest.raises(ModelOverrideError) as excinfo:
        apply_run_models(
            client,
            _BASE_URL,
            _USER_ID,
            _CHAT_ID,
            {"chat": "openai:gpt-4o"},
            env={},
        )

    error = excinfo.value
    assert error.status == 403
    assert error.detail == "model not permitted"
    assert error.role == "chat"
    assert "403" in str(error)
    assert "model not permitted" in str(error)


def test_returned_map_is_the_full_active_map() -> None:
    """The resolution reports every active role, requested or not."""
    first = {"chat": {"model": "openai:gpt-4o"}}
    resolved_active = {
        "chat": {"model": "openai:gpt-4o"},
        "judge": {"model": "openai:gpt-4o-mini"},
        "summarise": {"model": "local:model"},
    }
    client = _StubClient(
        [
            _Response(200, first),
            _Response(200, {}),
            _Response(200, resolved_active),
        ]
    )

    resolved = apply_run_models(
        client,
        _BASE_URL,
        _USER_ID,
        _CHAT_ID,
        {"chat": "openai:gpt-4o"},
        env={},
    )

    assert resolved == {
        "chat": "openai:gpt-4o",
        "judge": "openai:gpt-4o-mini",
        "summarise": "local:model",
    }


def test_empty_models_make_no_request() -> None:
    """An empty override mapping short-circuits without touching the backend."""
    client = _StubClient([])

    resolved = apply_run_models(client, _BASE_URL, _USER_ID, _CHAT_ID, {}, env={})

    assert resolved == {}
    assert client.calls == []
