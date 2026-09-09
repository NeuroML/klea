#!/usr/bin/env python3
"""
Tests for shared MCP tool implementations.

File: utils_pkg/tests/test_bundled_tools.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import json
import logging
import os
import sys
import time

import httpx
import klea_utils.api.utils as api_utils
import pytest
from klea_utils.mcp.tool_impls import read_file as read_file_module
from klea_utils.mcp.tool_impls import web_fetch as web_fetch_module
from klea_utils.mcp.tool_impls.download_file import (
    download_file,
    download_file_to_cache,
    download_files,
)
from klea_utils.mcp.tool_impls.list_files import list_files
from klea_utils.mcp.tool_impls.read_file import read_file
from klea_utils.mcp.tool_impls.web_fetch import web_fetch

logger = logging.getLogger(__name__)


class _FakeResponse:
    """Minimal httpx-like response used to drive :func:`web_fetch`."""

    def __init__(
        self,
        body: str | bytes,
        status: int = 200,
        content_type: str = "text/html",
    ):
        self.status_code = status
        self.headers = {"content-type": content_type}
        self._body = body.encode("utf-8") if isinstance(body, str) else body

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "http://example.com")
            response = httpx.Response(
                self.status_code, request=request, content=self._body
            )
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )

    @property
    def is_success(self) -> bool:
        return 200 <= self.status_code < 300

    @property
    def text(self) -> str:
        return self._body.decode("utf-8", errors="replace")

    @property
    def content(self) -> bytes:
        return self._body

    async def aiter_bytes(self):
        yield self._body


class _FakeStreamCM:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error

    async def __aenter__(self):
        if self._error is not None:
            raise self._error
        return self._response

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.calls: list[tuple[str, str, dict]] = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return _FakeStreamCM(response=self._response, error=self._error)

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        if self._error is not None:
            raise self._error
        return self._response


class _FlakySession:
    """Session that fails with the given status twice, then succeeds."""

    def __init__(self, body: str = "ok after retries"):
        self._body = body
        self.attempts = 0
        self.calls: list[tuple[str, str, dict]] = []

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        self.attempts += 1
        if self.attempts < 3:
            response = _FakeResponse("down", status=503, content_type="text/plain")
        else:
            response = _FakeResponse(self._body, status=200, content_type="text/plain")
        return _FakeStreamCM(response=response)

    async def get(self, url, **kwargs):
        # Not used by the web_fetch tests this drives; satisfies SessionLike.
        raise AssertionError("_FlakySession.get is not exercised in these tests")


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch):
    # Neutralise the exponential backoff so tests do not sleep.
    monkeypatch.setattr(api_utils, "wait_random_exponential", lambda **kw: 0.0)


@pytest.fixture(autouse=True)
def _hermetic_user_agents(monkeypatch, request):
    # Pin UA resolution so web_fetch tests never hit the network or disk.
    # The UA-resolution logic itself is covered by dedicated tests below.
    if request.cls is TestResolveUserAgents:
        return
    monkeypatch.setattr(
        web_fetch_module, "_resolve_user_agents", _async_ok([_TEST_USER_AGENT])
    )
    monkeypatch.setattr(web_fetch_module, "_UA_CACHE_PATH", "unused")
    monkeypatch.setattr(web_fetch_module, "_UA_LIST", None)
    monkeypatch.setattr(web_fetch_module, "_UA_RESOLVED_AT", 0.0)


_TEST_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36"


def _async_ok(value):
    async def _fn(*args, **kwargs):
        return value

    return _fn


async def _async_fail(*args, **kwargs):
    raise httpx.ConnectError("offline")


@pytest.mark.asyncio
async def test_web_fetch_rejects_invalid_url():
    result = await web_fetch(session=_FakeSession(), url="not-a-url")
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert result["status_code"] is None
    assert "http://" in result["error"] or "https://" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_missing_session():
    result = await web_fetch(session=None, url="https://example.com")
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert result["status_code"] is None
    assert "session" in result["error"].lower()


@pytest.mark.asyncio
async def test_web_fetch_success_strips_html():
    fake = _FakeResponse(
        "<html><body><h1>Hello</h1><script>x()</script></body></html>",
        status=200,
        content_type="text/html; charset=utf-8",
    )
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="https://example.com",
    )
    logger.debug(f"{result = }")
    assert result["status_code"] == 200
    assert "Hello" in result["content"]
    assert "<h1>" not in result["content"]
    assert "x()" not in result["content"]
    assert result["truncated"] is False
    assert result["download_truncated"] is False
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_web_fetch_plain_text_passthrough():
    fake = _FakeResponse("plain body", status=200, content_type="text/plain")
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="https://example.com/raw.txt",
    )
    logger.debug(f"{result = }")
    assert result["content"] == "plain body"
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_web_fetch_truncates():
    fake = _FakeResponse("abcdefghij", status=200, content_type="text/plain")
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="https://example.com",
        max_chars=4,
    )
    logger.debug(f"{result = }")
    assert result["content"] == "abcd"
    assert result["truncated"] is True
    assert result["download_truncated"] is False
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_web_fetch_download_cap_flags_download_truncated():
    fake = _FakeResponse("abcdefghij", status=200, content_type="text/plain")
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="https://example.com",
        max_download_bytes=4,
    )
    logger.debug(f"{result = }")
    assert result["content"] == "abcd"
    assert result["truncated"] is False
    assert result["download_truncated"] is True
    assert result["error"] == ""


@pytest.mark.asyncio
async def test_web_fetch_http_error_drops_body():
    fake = _FakeResponse("missing", status=404, content_type="text/plain")
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="https://example.com/missing",
    )
    logger.debug(f"{result = }")
    assert result["status_code"] == 404
    assert result["content"] == ""
    assert "404" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_timeout():
    result = await web_fetch(
        session=_FakeSession(error=TimeoutError()),
        url="https://example.com",
        timeout=1.0,
        retries=1,
    )
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "timed out" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_client_error():
    result = await web_fetch(
        session=_FakeSession(error=httpx.ConnectError("refused")),
        url="https://example.com",
        retries=1,
    )
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "refused" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_retries_transient_5xx_then_succeeds():
    session = _FlakySession()
    result = await web_fetch(
        session=session,
        url="https://example.com/flaky",
        retries=5,
    )
    logger.debug(f"{result = }")
    assert result["status_code"] == 200
    assert result["content"] == "ok after retries"
    assert session.attempts == 3


@pytest.mark.asyncio
async def test_web_fetch_retries_exhausted():
    session = _FlakySession()
    result = await web_fetch(
        session=session,
        url="https://example.com/flaky",
        retries=2,
    )
    logger.debug(f"{result = }")
    # Two attempts, still failing with 503.
    assert session.attempts == 2
    assert result["status_code"] == 503
    assert result["content"] == ""
    assert "503" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_uses_browser_user_agent():
    fake = _FakeResponse("hi", status=200, content_type="text/plain")
    session = _FakeSession(response=fake)
    await web_fetch(session=session, url="https://example.com")
    _, _, kwargs = session.calls[0]
    ua = kwargs["headers"]["User-Agent"]
    assert ua == _TEST_USER_AGENT
    assert "Mozilla/5.0" in ua
    assert "klea" not in ua.lower()


@pytest.mark.asyncio
async def test_web_fetch_sends_accept_language():
    fake = _FakeResponse("hi", status=200, content_type="text/plain")
    session = _FakeSession(response=fake)
    await web_fetch(session=session, url="https://example.com")
    _, _, kwargs = session.calls[0]
    assert kwargs["headers"]["Accept-Language"] == "en-US,en;q=0.9"


class _ChallengeSession:
    """First stream answers a Cloudflare challenge, second one succeeds."""

    def __init__(self, body: str = "ok after challenge"):
        self._body = body
        self.calls: list[tuple[str, str, dict]] = []
        self.stream_count = 0

    def stream(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        self.stream_count += 1
        if self.stream_count == 1:
            response = _FakeResponse("challenge", status=403, content_type="text/html")
            response.headers["cf-mitigated"] = "challenge"
        else:
            response = _FakeResponse(self._body, status=200, content_type="text/plain")
        return _FakeStreamCM(response=response)

    async def get(self, url, **kwargs):
        # Not used by the web_fetch test this drives; satisfies SessionLike.
        raise AssertionError("_ChallengeSession.get is not exercised in these tests")


@pytest.mark.asyncio
async def test_web_fetch_cloudflare_challenge_retries_honest_ua():
    session = _ChallengeSession()
    result = await web_fetch(session=session, url="https://example.com")
    logger.debug(f"{result = }")
    assert session.stream_count == 2
    assert result["status_code"] == 200
    assert result["content"] == "ok after challenge"
    # First attempt used the browser UA; retry used the honest client UA.
    browser_ua = session.calls[0][2]["headers"]["User-Agent"]
    honest_ua = session.calls[1][2]["headers"]["User-Agent"]
    assert browser_ua == _TEST_USER_AGENT
    assert honest_ua == web_fetch_module._honest_user_agent()
    assert honest_ua.startswith(web_fetch_module._HONEST_UA_PREFIX)


def test_honest_user_agent_uses_package_version():
    ua = web_fetch_module._honest_user_agent()
    assert ua.startswith("klea-web-fetch/")
    # Version tag resolves to the installed klea_utils version or 'dev'.
    tag = ua.split("/", 1)[1]
    assert tag in ("dev", "0.5.0")


def test_honest_user_agent_fallback_when_no_metadata(monkeypatch):
    import importlib.metadata

    monkeypatch.setattr(web_fetch_module, "_honest_user_agent_cache", None)

    def _no_version(name):
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", _no_version)
    ua = web_fetch_module._honest_user_agent()
    assert ua == "klea-web-fetch/dev"


@pytest.mark.asyncio
async def test_web_fetch_ssrf_blocks_private_hosts():
    for url in ("http://127.0.0.1/x", "http://localhost/x", "http://169.254.169.254/x"):
        result = await web_fetch(session=_FakeSession(), url=url)
        logger.debug(f"{url = } -> {result['error'] = }")
        assert result["content"] == ""
        assert result["status_code"] is None
        assert "Blocked request to private/internal address" in result["error"]


@pytest.mark.asyncio
async def test_web_fetch_ssrf_allow_internal_flag():
    fake = _FakeResponse("hi", status=200, content_type="text/plain")
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="http://127.0.0.1/x",
        allow_internal_hosts=True,
    )
    assert result["status_code"] == 200
    assert result["content"] == "hi"


@pytest.mark.asyncio
async def test_web_fetch_ssrf_allows_public_host():
    fake = _FakeResponse("public", status=200, content_type="text/plain")
    result = await web_fetch(
        session=_FakeSession(response=fake),
        url="https://example.com",
    )
    assert result["status_code"] == 200
    assert result["content"] == "public"


class TestDownloadFile:
    """Tests of the shared download_file implementation."""

    async def test_download_file_writes_target(self, tmp_path):
        fake = _FakeResponse("file body", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        target = tmp_path / "out.txt"
        result = await download_file(
            session=session,
            url="https://example.com/f.txt",
            file_path=target,
            project_root=str(tmp_path),
        )
        assert result == target
        assert target.read_text() == "file body"

    async def test_download_file_missing_session(self, tmp_path):
        result = await download_file(
            session=None, url="https://example.com/f.txt", file_path=tmp_path / "x"
        )
        assert result is None

    async def test_download_file_http_error(self, tmp_path):
        fake = _FakeResponse("missing", status=404, content_type="text/plain")
        session = _FakeSession(response=fake)
        result = await download_file(
            session=session,
            url="https://example.com/f.txt",
            file_path=tmp_path / "x",
            project_root=str(tmp_path),
        )
        assert result is None
        assert not (tmp_path / "x").exists()

    async def test_download_file_to_cache(self, tmp_path):
        fake = _FakeResponse("cached body", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        cache_dir = tmp_path / "cache"
        result = await download_file_to_cache(
            session=session,
            url="https://example.com/f.txt",
            cache_dir=cache_dir,
            file_name="f.txt",
        )
        assert result is not None
        assert result == cache_dir / "f.txt"
        assert result.read_text() == "cached body"

    async def test_download_file_denied_outside_project(self, tmp_path):
        fake = _FakeResponse("file body", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside.txt"
        result = await download_file(
            session=session,
            url="https://example.com/f.txt",
            file_path=outside,
            project_root=str(root),
        )
        assert result is None
        assert not outside.exists()

    async def test_download_file_sends_honest_ua(self, tmp_path):
        fake = _FakeResponse("file body", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        target = tmp_path / "out.txt"
        result = await download_file(
            session=session,
            url="https://example.com/f.txt",
            file_path=target,
            project_root=str(tmp_path),
        )
        assert result == target
        assert len(session.calls) == 1
        _, _, kwargs = session.calls[0]
        ua = kwargs["headers"]["User-Agent"]
        logger.debug(f"{ua = }")
        assert ua.startswith("klea-web-fetch/")

    async def test_download_file_writes_binary(self, tmp_path):
        body = b"%PDF-1.4\n%binary content\x00\x01\x02\n"
        fake = _FakeResponse(body, status=200, content_type="application/pdf")
        session = _FakeSession(response=fake)
        target = tmp_path / "doc.pdf"
        result = await download_file(
            session=session,
            url="https://example.com/doc.pdf",
            file_path=target,
            project_root=str(tmp_path),
        )
        assert result == target
        assert target.read_bytes() == body

    async def test_download_file_ssrf_denied(self, tmp_path):
        session = _FakeSession(response=_FakeResponse("x", status=200))
        target = tmp_path / "out.txt"
        result = await download_file(
            session=session,
            url="http://127.0.0.1/secret",
            file_path=target,
            project_root=str(tmp_path),
        )
        assert result is None
        assert session.calls == []
        assert not target.exists()

    async def test_download_file_ssrf_allowed_internal(self, tmp_path):
        fake = _FakeResponse("file body", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        target = tmp_path / "out.txt"
        result = await download_file(
            session=session,
            url="http://127.0.0.1/f.txt",
            file_path=target,
            project_root=str(tmp_path),
            allow_internal_hosts=True,
        )
        assert result == target
        assert len(session.calls) == 1
        assert target.read_text() == "file body"

    async def test_download_file_to_cache_ssrf_denied(self, tmp_path):
        session = _FakeSession(response=_FakeResponse("x", status=200))
        cache_dir = tmp_path / "cache"
        result = await download_file_to_cache(
            session=session,
            url="http://127.0.0.1/secret",
            cache_dir=cache_dir,
            file_name="f.txt",
        )
        assert result is None
        assert session.calls == []


def test_list_files_rejects_dotdot():
    result = list_files(path="..")
    logger.debug(f"{result = }")
    assert result["files"] == []
    assert result["error"] != ""
    assert "outside" in result["error"].lower() or ".." in result["error"]


def test_list_files_basic(tmp_path):
    (tmp_path / "a.py").write_text("")
    (tmp_path / "b.md").write_text("")
    (tmp_path / "sub").mkdir()

    result = list_files(path=str(tmp_path), pattern="*", project_root=str(tmp_path))

    logger.debug(f"{len(result['files']) = }")
    logger.debug(f"{result['error'] = }")

    names = {f["path"].split("/")[-1] for f in result["files"]}
    assert "a.py" in names
    assert "b.md" in names
    assert "sub" in names
    assert result["error"] == ""
    assert result["truncated"] is False


def test_list_files_recursive(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "c.py").write_text("")

    result = list_files(
        path=str(tmp_path),
        pattern="*.py",
        recursive=True,
        project_root=str(tmp_path),
    )

    logger.debug(f"{len(result['files']) = }")
    logger.debug(f"{result['error'] = }")

    names = {f["path"].split("/")[-1] for f in result["files"]}
    assert "c.py" in names
    assert result["error"] == ""


def test_list_files_truncates(tmp_path):
    for i in range(5):
        (tmp_path / f"f{i}.txt").write_text("")

    result = list_files(
        path=str(tmp_path), pattern="*.txt", max_results=2, project_root=str(tmp_path)
    )

    logger.debug(f"{len(result['files']) = }")
    logger.debug(f"{result['truncated'] = }")

    assert len(result["files"]) == 2
    assert result["truncated"] is True


def test_list_files_max_depth(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "b").mkdir()
    (tmp_path / "a" / "b" / "c.py").write_text("")
    (tmp_path / "a" / "top.py").write_text("")

    result = list_files(
        path=str(tmp_path),
        pattern="*.py",
        recursive=True,
        max_depth=1,
        project_root=str(tmp_path),
    )
    names = {f["path"].split("/")[-1] for f in result["files"]}
    logger.debug(f"max_depth=1: {names = }")
    assert names == set()

    result = list_files(
        path=str(tmp_path),
        pattern="*.py",
        recursive=True,
        max_depth=2,
        project_root=str(tmp_path),
    )
    names = {f["path"].split("/")[-1] for f in result["files"]}
    logger.debug(f"max_depth=2: {names = }")
    assert "top.py" in names
    assert "c.py" not in names

    result = list_files(
        path=str(tmp_path),
        pattern="*.py",
        recursive=True,
        max_depth=3,
        project_root=str(tmp_path),
    )
    names = {f["path"].split("/")[-1] for f in result["files"]}
    logger.debug(f"max_depth=3: {names = }")
    assert "top.py" in names
    assert "c.py" in names


def test_list_files_include_directories(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "f.py").write_text("")

    result = list_files(
        path=str(tmp_path),
        pattern="*",
        include_directories=False,
        project_root=str(tmp_path),
    )
    names = {f["path"].split("/")[-1] for f in result["files"]}
    logger.debug(f"{names = }")
    assert "f.py" in names
    assert "sub" not in names


def test_list_files_include_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "f.py").write_text("")

    result = list_files(
        path=str(tmp_path),
        pattern="*",
        include_files=False,
        project_root=str(tmp_path),
    )
    names = {f["path"].split("/")[-1] for f in result["files"]}
    logger.debug(f"{names = }")
    assert "sub" in names
    assert "f.py" not in names


def test_list_files_symlink_not_recursed(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "real" / "target.py").write_text("")
    (tmp_path / "link").symlink_to(tmp_path / "real", target_is_directory=True)

    result = list_files(
        path=str(tmp_path), pattern="*", recursive=True, project_root=str(tmp_path)
    )
    logger.debug(f"{result['files'] = }")
    by_path = {f["path"].split("/")[-1]: f for f in result["files"]}
    assert by_path["link"]["type"] == "link"
    assert by_path["real"]["type"] == "directory"
    assert "target.py" in by_path  # reached via the real dir, not the link
    assert not any(str(f["path"]).endswith("link/target.py") for f in result["files"])

    result = list_files(
        path=str(tmp_path), pattern="*", include_files=False, project_root=str(tmp_path)
    )
    by_path = {f["path"].split("/")[-1]: f for f in result["files"]}
    logger.debug(f"{by_path = }")
    assert by_path["link"]["type"] == "link"
    assert by_path["real"]["type"] == "directory"
    assert "target.py" not in by_path


def test_list_files_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret"
    outside.mkdir()
    (outside / "s.txt").write_text("s")
    result = list_files(path=str(outside), pattern="*", project_root=str(root))
    logger.debug(f"{result = }")
    assert result["files"] == []
    assert "denied" in result["error"].lower()


def test_list_files_denied_absolute_escape(tmp_path):
    result = list_files(path="/etc", pattern="*", project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["files"] == []
    assert "denied" in result["error"].lower()


def _write_lines(tmp_path, name, n):
    f = tmp_path / name
    f.write_text("\n".join(f"line {i}" for i in range(1, n + 1)))
    return f


def test_read_file_paging(tmp_path):
    f = _write_lines(tmp_path, "t.txt", 10)
    result = read_file(str(f), offset=1, limit=5, project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == (
        "1: line 1\n2: line 2\n3: line 3\n4: line 4\n5: line 5"
    )
    assert result["line_start"] == 1
    assert result["line_end"] == 5
    assert result["total_lines"] == 10
    assert result["truncated"] is True
    assert result["error"] == ""

    result = read_file(str(f), offset=6, limit=5, project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == (
        "6: line 6\n7: line 7\n8: line 8\n9: line 9\n10: line 10"
    )
    assert result["line_start"] == 6
    assert result["line_end"] == 10
    assert result["truncated"] is False


def test_read_file_offset_past_eof(tmp_path):
    f = _write_lines(tmp_path, "t.txt", 3)
    result = read_file(str(f), offset=50, limit=5, project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert result["line_start"] == 50
    assert result["line_end"] == 49
    assert result["total_lines"] == 3
    assert result["truncated"] is False


def test_read_file_html_strips(tmp_path):
    f = tmp_path / "page.html"
    f.write_text(
        "<html><body><h1>Title</h1><script>x()</script><p>Body</p></body></html>"
    )
    result = read_file(str(f), project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == "1: Title\n2: Body"
    assert "<h1>" not in result["content"]
    assert result["error"] == ""


def test_read_file_char_cap(tmp_path):
    f = _write_lines(tmp_path, "t.txt", 1000)
    result = read_file(str(f), max_chars=100, project_root=str(tmp_path))
    logger.debug(f"{len(result['content']) = }")
    assert result["truncated"] is True
    assert len(result["content"]) <= 100


def test_read_file_denied_outside_project(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("s")
    result = read_file(str(outside), project_root=str(root))
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "denied" in result["error"].lower()


def test_read_file_missing_file(tmp_path):
    result = read_file(str(tmp_path / "nope.txt"), project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "not a file" in result["error"].lower()


def test_read_file_too_large(tmp_path):
    f = _write_lines(tmp_path, "t.txt", 5)
    result = read_file(str(f), max_bytes=10, project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "too large" in result["error"].lower()


def test_read_file_csv_converts(tmp_path):
    pytest.importorskip("anydoc")
    f = tmp_path / "c.csv"
    f.write_text("a,b\n1,2\n")
    result = read_file(str(f), project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert "| a | b |" in result["content"]
    assert result["error"] == ""


def test_read_file_conversion_error_in_error_field(tmp_path):
    pytest.importorskip("anydoc")
    f = tmp_path / "bad.pdf"
    f.write_bytes(b"\xff\xfe junk")
    result = read_file(str(f), project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "MalformedError" in result["error"]


def test_read_file_anydoc_not_installed(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 junk")
    monkeypatch.setitem(sys.modules, "anydoc", None)
    monkeypatch.setattr(read_file_module, "_ANYDOC_AVAILABLE", None)
    result = read_file(str(f), project_root=str(tmp_path))
    logger.debug(f"{result = }")
    assert result["content"] == ""
    assert "anydoc is not installed" in result["error"]


def test_read_file_cache_avoids_reconversion(tmp_path, monkeypatch):
    pytest.importorskip("anydoc")
    f = tmp_path / "c.csv"
    f.write_text("a,b\n1,2\n")
    calls = {"n": 0}
    original = read_file_module._to_markdown

    def counting(data, suffix):
        calls["n"] += 1
        return original(data, suffix)

    monkeypatch.setattr(read_file_module, "_to_markdown", counting)
    read_file(str(f), offset=1, limit=1, project_root=str(tmp_path))
    read_file(str(f), offset=2, limit=1, project_root=str(tmp_path))
    logger.debug(f"conversions after two paged reads = {calls['n']}")
    assert calls["n"] == 1

    f.write_text("a,b\n3,4\n")
    # Ensure a distinct mtime so the cache key changes even if the size is
    # identical.
    os.utime(f, (time.time() + 10, time.time() + 10))
    read_file(str(f), project_root=str(tmp_path))
    logger.debug(f"conversions after edit = {calls['n']}")
    assert calls["n"] == 2


class TestResolveUserAgents:
    """Tests of the UA resolution logic (offline fallback, cache, fetch)."""

    @pytest.fixture(autouse=True)
    def _reset_state(self, monkeypatch, tmp_path):
        monkeypatch.setattr(web_fetch_module, "_UA_LIST", None)
        monkeypatch.setattr(web_fetch_module, "_UA_RESOLVED_AT", 0.0)
        monkeypatch.setattr(
            web_fetch_module, "_UA_CACHE_PATH", tmp_path / "user_agents.json"
        )

    async def test_offline_no_cache_uses_hardcoded_fallback(self, monkeypatch):
        monkeypatch.setattr(web_fetch_module, "_fetch_user_agents", _async_fail)
        agents = await web_fetch_module._resolve_user_agents()
        assert agents == [web_fetch_module._FALLBACK_USER_AGENT]

    async def test_fresh_cache_used_without_fetch(self, monkeypatch):
        cached = ["Mozilla/5.0 (X11; Linux x86_64) CachedAgent/1.0"]
        path = web_fetch_module._UA_CACHE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(cached))
        fetched = False

        async def _fetch():
            nonlocal fetched
            fetched = True
            return ["never used"]

        monkeypatch.setattr(web_fetch_module, "_fetch_user_agents", _fetch)
        agents = await web_fetch_module._resolve_user_agents()
        assert agents == cached
        assert fetched is False

    async def test_fetch_success_writes_cache(self, monkeypatch):
        fetched = ["Mozilla/5.0 (X11; Linux x86_64) FreshAgent/1.0"]
        monkeypatch.setattr(web_fetch_module, "_fetch_user_agents", _async_ok(fetched))
        agents = await web_fetch_module._resolve_user_agents()
        assert agents == fetched
        path = web_fetch_module._UA_CACHE_PATH
        assert path.exists()
        assert json.loads(path.read_text()) == fetched

    async def test_stale_cache_kept_when_refresh_fails(self, monkeypatch):
        stale = ["Mozilla/5.0 (X11; Linux x86_64) StaleAgent/1.0"]
        path = web_fetch_module._UA_CACHE_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(stale))
        # Make the cache look older than the TTL.
        old_mtime = time.time() - web_fetch_module._UA_TTL_SECONDS - 10
        os.utime(path, (old_mtime, old_mtime))
        monkeypatch.setattr(web_fetch_module, "_fetch_user_agents", _async_fail)
        agents = await web_fetch_module._resolve_user_agents()
        # Stale cache is preferred over the hardcoded fallback.
        assert agents == stale


class _PerUrlSession:
    """Serves a distinct response per URL."""

    def __init__(self, responses: dict):
        self._responses = responses
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses[url]

    def stream(self, method, url, **kwargs):
        raise AssertionError("stream is not exercised in these tests")


class _TrackingSession:
    """Serves responses per URL while holding requests open, tracking the
    maximum number of concurrent in-flight requests."""

    def __init__(self, responses: dict, hold):
        self._responses = responses
        self._hold = hold
        self.in_flight = 0
        self.max_in_flight = 0
        self.calls: list[tuple[str, dict]] = []

    async def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await self._hold.wait()
            return self._responses[url]
        finally:
            self.in_flight -= 1

    def stream(self, method, url, **kwargs):
        raise AssertionError("stream is not exercised in these tests")


class TestDownloadFiles:
    """Tests of the multi-file download helper."""

    async def test_download_files_writes_multiple_files(self, tmp_path):
        fake = _FakeResponse("file body", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        target = tmp_path / "dl"
        files = [
            {"path": "a.txt", "download_url": "https://example.com/a.txt"},
            {"path": "sub/b.txt", "download_url": "https://example.com/b.txt"},
        ]
        result = await download_files(session, files, target)
        logger.debug(f"{result = }")
        assert result["error"] == ""
        assert (target / "a.txt").read_text() == "file body"
        assert (target / "sub" / "b.txt").read_text() == "file body"
        by_path = {r["path"]: r for r in result["results"]}
        assert by_path["a.txt"]["saved_to"] == str(target / "a.txt")
        assert by_path["sub/b.txt"]["saved_to"] == str(target / "sub" / "b.txt")

    async def test_download_files_continue_on_error(self, tmp_path):
        responses = {
            "https://example.com/ok.txt": _FakeResponse(
                "ok", status=200, content_type="text/plain"
            ),
            "https://example.com/bad.txt": _FakeResponse(
                "missing", status=404, content_type="text/plain"
            ),
        }
        session = _PerUrlSession(responses)
        target = tmp_path / "dl"
        files = [
            {"path": "ok.txt", "download_url": "https://example.com/ok.txt"},
            {"path": "bad.txt", "download_url": "https://example.com/bad.txt"},
        ]
        result = await download_files(session, files, target)
        logger.debug(f"{result = }")
        assert result["error"] == ""
        by_path = {r["path"]: r for r in result["results"]}
        assert "saved_to" in by_path["ok.txt"]
        assert "error" in by_path["bad.txt"]
        assert (target / "ok.txt").exists()
        assert not (target / "bad.txt").exists()

    async def test_download_files_skips_entries_missing_fields(self, tmp_path):
        fake = _FakeResponse("x", status=200, content_type="text/plain")
        session = _FakeSession(response=fake)
        target = tmp_path / "dl"
        files = [
            {"path": "ok.txt", "download_url": "https://example.com/ok.txt"},
            {"path": "no-url.txt"},
            {"download_url": "https://example.com/no-name.txt"},
        ]
        result = await download_files(session, files, target)
        logger.debug(f"{result = }")
        by_path = {r["path"]: r for r in result["results"]}
        assert by_path["ok.txt"]["saved_to"] == str(target / "ok.txt")
        assert by_path["no-url.txt"]["error"] == "missing path or download_url"
        assert by_path[""]["error"] == "missing path or download_url"

    async def test_download_files_empty_list(self, tmp_path):
        session = _FakeSession(response=_FakeResponse("x"))
        result = await download_files(session, [], tmp_path / "dl")
        logger.debug(f"{result = }")
        assert result == {"results": [], "error": ""}

    async def test_download_files_missing_session(self, tmp_path):
        files = [{"path": "a.txt", "download_url": "https://example.com/a.txt"}]
        result = await download_files(None, files, tmp_path / "dl")
        logger.debug(f"{result = }")
        assert result["error"] == ""
        assert result["results"][0]["path"] == "a.txt"
        assert result["results"][0]["error"] == "download failed"

    async def test_download_files_bounded_concurrency(self, tmp_path):
        import asyncio

        hold = asyncio.Event()
        urls = [f"https://example.com/f{i}.txt" for i in range(6)]
        files = [
            {"path": f"f{i}.txt", "download_url": url} for i, url in enumerate(urls)
        ]
        responses = {
            url: _FakeResponse(f"body {i}", status=200, content_type="text/plain")
            for i, url in enumerate(urls)
        }
        session = _TrackingSession(responses, hold)
        target = tmp_path / "dl"

        task = asyncio.create_task(
            download_files(session, files, target, max_concurrency=2)
        )
        # Let the first two downloads open and stall on the hold event.
        for _ in range(100):
            if session.max_in_flight >= 2:
                break
            await asyncio.sleep(0.01)
        assert session.max_in_flight == 2
        assert session.max_in_flight <= 2
        hold.set()
        result = await task
        assert result["error"] == ""
        assert all("saved_to" in r for r in result["results"])
        assert all((target / f"f{i}.txt").exists() for i in range(6))
