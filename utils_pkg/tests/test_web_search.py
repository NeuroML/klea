#!/usr/bin/env python3
"""
Tests for the web-search provider transport and adapters.

File: utils_pkg/tests/test_web_search.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import httpx
import klea_utils.api.utils as api_utils
import pytest
from klea_utils.mcp.tool_impls.search.base import SearchProviderError
from klea_utils.mcp.tool_impls.search.providers import (
    _iter_sse_payloads,
    _mcp_call,
    _user_agent,
)


class _FakeResponse:
    """Minimal httpx-like response used to drive the transport."""

    def __init__(
        self,
        text: str = "",
        status: int = 200,
        content_type: str = "application/json",
    ):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self.text = text

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("POST", "http://example.com")
            response = httpx.Response(
                self.status_code, request=request, content=self.text.encode()
            )
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


class _FakePostSession:
    """Session-like object capturing POST calls and returning a canned reply."""

    def __init__(self, response: _FakeResponse | None = None, error=None):
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str, dict]] = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        if self._error is not None:
            raise self._error
        return self._response


class _FlakyPostSession:
    """Fails with a transient status twice, then succeeds."""

    def __init__(self, response: _FakeResponse):
        self._response = response
        self.attempts = 0
        self.calls: list[tuple[str, str, dict]] = []

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        self.attempts += 1
        if self.attempts < 3:
            return _FakeResponse("down", status=503)
        return self._response


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch):
    # Neutralise exponential backoff so retry tests do not sleep.
    monkeypatch.setattr(api_utils, "wait_random_exponential", lambda **kw: 0.0)


RESULT = {"content": [{"type": "text", "text": "hello"}]}


def test_user_agent_is_versioned():
    ua = _user_agent()
    assert ua.startswith("klea-web-search/")


def test_iter_sse_payloads_skips_junk():
    text = (
        ": keep-alive\n"
        "event: message\n"
        'data: {"ok": 1}\n'
        "\n"
        "data: [DONE]\n"
        "data: not-json\n"
    )
    assert _iter_sse_payloads(text) == [{"ok": 1}]


async def test_mcp_call_parses_json_result():
    session = _FakePostSession(
        _FakeResponse(text='{"jsonrpc":"2.0","id":1,"result":{"content":[]}}')
    )
    result = await _mcp_call(
        session, "https://svc.example/mcp", "web_search", {"q": "x"}
    )
    assert result == {"content": []}
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://svc.example/mcp"
    assert kwargs["json"]["method"] == "tools/call"
    assert kwargs["json"]["params"] == {"name": "web_search", "arguments": {"q": "x"}}


async def test_mcp_call_parses_sse_result():
    body = (
        "event: message\n"
        'data: {"jsonrpc":"2.0","id":1,"result":{"content":[{"type":"text","text":"hi"}]}}\n'
        "\n"
    )
    session = _FakePostSession(
        _FakeResponse(text=body, content_type="text/event-stream")
    )
    result = await _mcp_call(session, "https://svc.example/mcp", "web_search", {})
    assert result["content"][0]["text"] == "hi"


async def test_mcp_call_sets_honest_user_agent_and_accept():
    session = _FakePostSession(_FakeResponse(text='{"result":{"content":[]}}'))
    await _mcp_call(session, "https://svc.example/mcp", "web_search", {})
    headers = session.calls[0][2]["headers"]
    assert headers["User-Agent"].startswith("klea-web-search/")
    assert "text/event-stream" in headers["Accept"]


async def test_mcp_call_merges_provider_headers():
    session = _FakePostSession(_FakeResponse(text='{"result":{"content":[]}}'))
    await _mcp_call(
        session,
        "https://svc.example/mcp",
        "web_search",
        {},
        headers={"X-Tavily-Access-Mode": "keyless"},
    )
    headers = session.calls[0][2]["headers"]
    assert headers["X-Tavily-Access-Mode"] == "keyless"
    assert headers["User-Agent"].startswith("klea-web-search/")


async def test_mcp_call_raises_on_error_payload():
    session = _FakePostSession(
        _FakeResponse(
            text='{"jsonrpc":"2.0","id":1,"error":{"code":-32000,"message":"boom"}}'
        )
    )
    with pytest.raises(SearchProviderError):
        await _mcp_call(session, "https://svc.example/mcp", "web_search", {})


async def test_mcp_call_raises_on_http_error():
    session = _FakePostSession(_FakeResponse(text="forbidden", status=403))
    with pytest.raises(SearchProviderError):
        await _mcp_call(session, "https://svc.example/mcp", "web_search", {})


async def test_mcp_call_raises_without_session():
    with pytest.raises(SearchProviderError):
        await _mcp_call(None, "https://svc.example/mcp", "web_search", {})


async def test_mcp_call_raises_on_invalid_json():
    session = _FakePostSession(_FakeResponse(text="not json at all"))
    with pytest.raises(SearchProviderError):
        await _mcp_call(session, "https://svc.example/mcp", "web_search", {})


async def test_mcp_call_retries_transient_status():
    session = _FlakyPostSession(_FakeResponse(text='{"result":{"content":[]}}'))
    result = await _mcp_call(session, "https://svc.example/mcp", "web_search", {})
    assert result == {"content": []}
    assert session.attempts == 3
