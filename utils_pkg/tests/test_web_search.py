#!/usr/bin/env python3
"""
Tests for the web-search provider transport and adapters.

File: utils_pkg/tests/test_web_search.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json

import httpx
import klea_utils.api.utils as api_utils
import pytest
from klea_utils.mcp.tool_impls import web_search as web_search_module
from klea_utils.mcp.tool_impls.search.base import SERVICE_ORDER, SearchResult
from klea_utils.mcp.tool_impls.search.brave import BraveProvider
from klea_utils.mcp.tool_impls.search.errors import SearchProviderError
from klea_utils.mcp.tool_impls.search.exa import ExaProvider
from klea_utils.mcp.tool_impls.search.firecrawl import FirecrawlProvider
from klea_utils.mcp.tool_impls.search.hosted import MAX_SNIPPET_CHARS
from klea_utils.mcp.tool_impls.search.parallel import ParallelProvider
from klea_utils.mcp.tool_impls.search.resolver import WebSearchResolver
from klea_utils.mcp.tool_impls.search.serper import SerperProvider
from klea_utils.mcp.tool_impls.search.tavily import TavilyProvider
from klea_utils.mcp.tool_impls.search.transport import (
    _iter_sse_payloads,
    _mcp_call,
    _user_agent,
)
from klea_utils.mcp.tool_impls.web_search import (
    DEFAULT_MAX_RESULTS,
    web_search,
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

    def json(self):
        return json.loads(self.text)


class _FakePostSession:
    """Session-like object capturing GET/POST calls and returning a reply."""

    def __init__(self, response: _FakeResponse | None = None, error=None):
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if self._error is not None:
            raise self._error
        return self._response

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

    async def get(self, url, **kwargs):
        # Not used by the retry tests this drives; satisfies SearchSession.
        raise AssertionError("_FlakyPostSession.get is not exercised")

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


def _mcp_body(text: str) -> str:
    """Wrap *text* as the JSON-RPC result of a tools/call MCP response."""
    return json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": text}]},
        }
    )


def test_hosted_provider_names_match_service_order():
    assert SERVICE_ORDER == ("tavily", "exa", "parallel", "firecrawl")
    for cls in (TavilyProvider, ExaProvider, ParallelProvider, FirecrawlProvider):
        assert cls().is_available() is True


async def test_tavily_keyless_parses_results(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    payload = json.dumps(
        {
            "results": [
                {"url": "https://a", "title": "A", "content": "snip", "score": 1.5}
            ]
        }
    )
    session = _FakePostSession(_FakeResponse(text=_mcp_body(payload)))
    results = await TavilyProvider().search(session, "q", 5)
    assert len(results) == 1
    assert (results[0].url, results[0].title, results[0].snippet) == (
        "https://a",
        "A",
        "snip",
    )
    assert results[0].score == 1.5
    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["X-Tavily-Access-Mode"] == "keyless"
    assert "Authorization" not in kwargs["headers"]
    assert kwargs["json"]["params"] == {
        "name": "tavily_search",
        "arguments": {"query": "q", "max_results": 5},
    }


async def test_tavily_keyed_uses_bearer(monkeypatch):
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-secret")
    session = _FakePostSession(_FakeResponse(text=_mcp_body('{"results": []}')))
    await TavilyProvider().search(session, "q", 3)
    headers = session.calls[0][2]["headers"]
    assert headers["Authorization"] == "Bearer tvly-secret"
    assert "X-Tavily-Access-Mode" not in headers


async def test_exa_keyless_parses_text_blocks(monkeypatch):
    monkeypatch.delenv("EXA_API_KEY", raising=False)
    text = (
        "Title: First\nURL: https://one\nPublished: 2024-01-02\nAuthor: A\n"
        "Highlights:\nExcerpt one\n\n"
        "Title: Second\nURL: https://two\nPublished: N/A\nAuthor: B\n"
        "Highlights:\nExcerpt two"
    )
    session = _FakePostSession(_FakeResponse(text=_mcp_body(text)))
    results = await ExaProvider().search(session, "q", 2)
    assert [r.url for r in results] == ["https://one", "https://two"]
    assert results[0].title == "First"
    assert results[0].published == "2024-01-02"
    assert "Excerpt one" in results[0].snippet
    assert results[1].published is None
    assert "x-api-key" not in session.calls[0][2]["headers"]
    assert session.calls[0][2]["json"]["params"]["arguments"] == {
        "query": "q",
        "numResults": 2,
        "type": "auto",
    }


async def test_exa_keyed_uses_api_key_header(monkeypatch):
    monkeypatch.setenv("EXA_API_KEY", "exa-secret")
    body = _mcp_body("Title: T\nURL: https://u\nPublished: N/A\nHighlights:\nx")
    session = _FakePostSession(_FakeResponse(text=body))
    await ExaProvider().search(session, "q", 1)
    assert session.calls[0][2]["headers"]["x-api-key"] == "exa-secret"


async def test_parallel_keyed_parses_excerpts(monkeypatch):
    monkeypatch.setenv("PARALLEL_API_KEY", "par-secret")
    payload = json.dumps(
        {
            "results": [
                {
                    "url": "https://p",
                    "title": "P",
                    "publish_date": "2025-01-01",
                    "excerpts": ["e1", "e2"],
                }
            ]
        }
    )
    session = _FakePostSession(_FakeResponse(text=_mcp_body(payload)))
    results = await ParallelProvider().search(session, "q", 3)
    assert results[0].url == "https://p"
    assert results[0].snippet == "e1\ne2"
    assert results[0].published == "2025-01-01"
    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["Authorization"] == "Bearer par-secret"
    assert kwargs["json"]["params"]["arguments"] == {
        "objective": "q",
        "search_queries": ["q"],
    }


async def test_parallel_keyless_has_no_auth(monkeypatch):
    monkeypatch.delenv("PARALLEL_API_KEY", raising=False)
    session = _FakePostSession(_FakeResponse(text=_mcp_body('{"results": []}')))
    await ParallelProvider().search(session, "q", 3)
    assert "Authorization" not in session.calls[0][2]["headers"]


async def test_firecrawl_parses_and_keyless(monkeypatch):
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    payload = json.dumps(
        {
            "success": True,
            "data": {"web": [{"url": "https://f", "title": "F", "description": "d"}]},
        }
    )
    session = _FakePostSession(_FakeResponse(text=_mcp_body(payload)))
    results = await FirecrawlProvider().search(session, "q", 4)
    assert (results[0].url, results[0].snippet) == ("https://f", "d")
    _, _, kwargs = session.calls[0]
    assert kwargs["json"]["params"]["arguments"] == {"query": "q", "limit": 4}
    assert "Authorization" not in kwargs["headers"]


async def test_firecrawl_keyed_uses_bearer(monkeypatch):
    monkeypatch.setenv("FIRECRAWL_API_KEY", "fc-secret")
    session = _FakePostSession(_FakeResponse(text=_mcp_body('{"data": {"web": []}}')))
    await FirecrawlProvider().search(session, "q", 4)
    assert session.calls[0][2]["headers"]["Authorization"] == "Bearer fc-secret"


async def test_snippet_is_capped(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    payload = json.dumps({"results": [{"url": "https://a", "content": "x" * 5000}]})
    session = _FakePostSession(_FakeResponse(text=_mcp_body(payload)))
    results = await TavilyProvider().search(session, "q", 1)
    assert len(results[0].snippet) == MAX_SNIPPET_CHARS


async def test_non_json_provider_content_raises(monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    session = _FakePostSession(_FakeResponse(text=_mcp_body("not json")))
    with pytest.raises(SearchProviderError):
        await TavilyProvider().search(session, "q", 1)


def test_brave_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    assert BraveProvider().is_available() is False


async def test_brave_parses_and_sends_token(monkeypatch):
    monkeypatch.setenv("BRAVE_API_KEY", "brv-secret")
    payload = json.dumps(
        {
            "web": {
                "results": [
                    {
                        "title": "B",
                        "url": "https://b",
                        "description": "brave snippet",
                        "page_age": "2024-05-01",
                    }
                ]
            }
        }
    )
    session = _FakePostSession(_FakeResponse(text=payload))
    provider = BraveProvider()
    assert provider.is_available() is True
    results = await provider.search(session, "q", 3)
    assert (results[0].title, results[0].url, results[0].snippet) == (
        "B",
        "https://b",
        "brave snippet",
    )
    assert results[0].published == "2024-05-01"
    method, url, kwargs = session.calls[0]
    assert method == "GET"
    assert url == "https://api.search.brave.com/res/v1/web/search"
    assert kwargs["params"] == {"q": "q", "count": 3}
    assert kwargs["headers"]["X-Subscription-Token"] == "brv-secret"


def test_serper_unavailable_without_key(monkeypatch):
    monkeypatch.delenv("SERPER_API_KEY", raising=False)
    assert SerperProvider().is_available() is False


async def test_serper_parses_and_sends_key(monkeypatch):
    monkeypatch.setenv("SERPER_API_KEY", "serper-secret")
    payload = json.dumps(
        {
            "organic": [
                {
                    "title": "S",
                    "link": "https://s",
                    "snippet": "serper snippet",
                    "date": "May 1, 2024",
                }
            ]
        }
    )
    session = _FakePostSession(_FakeResponse(text=payload))
    provider = SerperProvider()
    assert provider.is_available() is True
    results = await provider.search(session, "q", 5)
    assert (results[0].title, results[0].url, results[0].snippet) == (
        "S",
        "https://s",
        "serper snippet",
    )
    assert results[0].published == "May 1, 2024"
    method, url, kwargs = session.calls[0]
    assert method == "POST"
    assert url == "https://google.serper.dev/search"
    assert kwargs["json"] == {"q": "q", "num": 5}
    assert kwargs["headers"]["X-API-KEY"] == "serper-secret"


class _StubProvider:
    """Minimal SearchProvider for resolver tests."""

    def __init__(
        self,
        name: str,
        results: list[SearchResult] | None = None,
        error: Exception | None = None,
    ):
        self.name = name
        self._results = results or []
        self._error = error
        self.calls = 0

    def is_available(self) -> bool:
        return True

    async def search(self, session, query, max_results, timeout):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._results


def _clear_search_keys(monkeypatch):
    for var in (
        "TAVILY_API_KEY",
        "EXA_API_KEY",
        "PARALLEL_API_KEY",
        "FIRECRAWL_API_KEY",
        "BRAVE_API_KEY",
        "SERPER_API_KEY",
    ):
        monkeypatch.delenv(var, raising=False)


def test_resolver_default_pool_is_keyless_only(monkeypatch):
    _clear_search_keys(monkeypatch)
    resolver = WebSearchResolver()
    assert [p.name for p in resolver.available_providers()] == [
        "tavily",
        "exa",
        "parallel",
        "firecrawl",
    ]


def test_resolver_appends_keyed_provider_when_key_present(monkeypatch):
    _clear_search_keys(monkeypatch)
    monkeypatch.setenv("BRAVE_API_KEY", "brv")
    resolver = WebSearchResolver()
    assert [p.name for p in resolver.available_providers()] == [
        "tavily",
        "exa",
        "parallel",
        "firecrawl",
        "brave",
    ]


async def test_resolver_falls_back_on_provider_error():
    bad = _StubProvider("bad", error=SearchProviderError("boom"))
    good = _StubProvider("good", results=[SearchResult(title="T", url="https://u")])
    resolver = WebSearchResolver(providers=[bad, good])
    out = await resolver.search(_FakePostSession(), "q", 5)
    assert out["provider"] == "good"
    assert out["error"] == ""
    assert out["results"][0]["url"] == "https://u"
    assert (bad.calls, good.calls) == (1, 1)


async def test_resolver_falls_back_on_empty_result():
    empty = _StubProvider("empty", results=[])
    good = _StubProvider("good", results=[SearchResult(url="https://u")])
    resolver = WebSearchResolver(providers=[empty, good])
    out = await resolver.search(_FakePostSession(), "q", 5)
    assert out["provider"] == "good"
    assert empty.calls == 1


async def test_resolver_all_providers_fail():
    first = _StubProvider("a", error=SearchProviderError("a down"))
    second = _StubProvider("b", error=SearchProviderError("b down"))
    resolver = WebSearchResolver(providers=[first, second])
    out = await resolver.search(_FakePostSession(), "q", 5)
    assert out["provider"] == ""
    assert out["results"] == []
    assert "No results" in out["error"]
    assert "a down" in out["error"] and "b down" in out["error"]


async def test_resolver_empty_query_is_error():
    resolver = WebSearchResolver(providers=[_StubProvider("x")])
    out = await resolver.search(_FakePostSession(), "   ", 5)
    assert out["error"] == "Empty search query."


async def test_resolver_no_available_providers():

    class _Unavailable:
        name = "nope"

        def is_available(self) -> bool:
            return False

        async def search(self, *a, **k):  # pragma: no cover
            raise AssertionError("should not be called")

    resolver = WebSearchResolver(providers=[_Unavailable()])
    out = await resolver.search(_FakePostSession(), "q", 5)
    assert out["error"] == "No search provider is available."


async def test_resolver_restricts_to_named_providers():
    first = _StubProvider("a", results=[SearchResult(url="https://a")])
    second = _StubProvider("b", results=[SearchResult(url="https://b")])
    resolver = WebSearchResolver(providers=[first, second])
    out = await resolver.search(_FakePostSession(), "q", 5, providers=["b"])
    assert out["provider"] == "b"
    assert first.calls == 0
    assert second.calls == 1


class _FakeResolver:
    """Records calls and returns a canned response (for tool-impl tests)."""

    def __init__(self):
        self.calls = []

    async def search(self, session, query, max_results, providers=None):
        self.calls.append((session, query, max_results, providers))
        return {
            "query": query,
            "provider": "fake",
            "results": [{"url": "https://x", "title": "X", "snippet": ""}],
            "error": "",
        }


async def test_web_search_delegates_to_resolver(monkeypatch):
    fake = _FakeResolver()
    monkeypatch.setattr(web_search_module, "WebSearchResolver", lambda: fake)
    out = await web_search(None, "hello", max_results=3, providers=["tavily"])
    assert out["provider"] == "fake"
    assert out["results"][0]["url"] == "https://x"
    assert fake.calls[0] == (None, "hello", 3, ["tavily"])


async def test_web_search_uses_default_max_results(monkeypatch):
    fake = _FakeResolver()
    monkeypatch.setattr(web_search_module, "WebSearchResolver", lambda: fake)
    await web_search(None, "hello")
    assert fake.calls[0][2] == DEFAULT_MAX_RESULTS
    assert fake.calls[0][3] is None


async def test_web_search_empty_query_errors():
    out = await web_search(None, "   ")
    assert out["provider"] == ""
    assert out["error"] == "Empty search query."


async def test_web_search_no_session_all_providers_fail(monkeypatch):
    _clear_search_keys(monkeypatch)
    out = await web_search(None, "hello")
    assert out["provider"] == ""
    assert out["results"] == []
    assert out["error"]
