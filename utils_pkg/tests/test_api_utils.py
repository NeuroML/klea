#!/usr/bin/env python3
"""
Tests for the shared API utilities.

File: tests/test_api_utils.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import httpx
import klea_utils.api.utils as api_utils
import pytest
from klea_utils.api.utils import check_api_is_ready

logger = logging.getLogger(__name__)


class _ReadyHandler(BaseHTTPRequestHandler):
    """Always answers 200 with a ready payload, as a live server would."""

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status": "ready"}')

    def log_message(self, format: str, *args: Any) -> None:
        pass


class _Always503(BaseHTTPRequestHandler):
    """Always answers 503, like a server that is up but not yet ready."""

    count = 0

    def do_GET(self):
        type(self).count += 1
        self.send_response(503)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:
        pass


class TestCheckApiIsReady:
    """Live checks against a throwaway server on an ephemeral port.

    The retry behaviour is validated by counting requests: an ``attempts``
    bound must stop after exactly that many probes, and the time-based
    ``timeout`` bound must give up quickly even while the server keeps
    answering.
    """

    @staticmethod
    def _serve(handler_cls):
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        return server

    async def test_ready_returns_json(self):
        server = self._serve(_ReadyHandler)
        try:
            port = server.server_address[1]
            result = await check_api_is_ready(
                f"http://127.0.0.1:{port}/health/ready", attempts=3
            )
            assert result == {"status": "ready"}
        finally:
            server.shutdown()

    async def test_attempts_bound_is_honoured(self):
        _Always503.count = 0
        server = self._serve(_Always503)
        try:
            port = server.server_address[1]
            with pytest.raises(httpx.HTTPStatusError):
                await check_api_is_ready(
                    f"http://127.0.0.1:{port}/health/ready", attempts=2
                )
            # Two probes in, the attempt bound must have given up.
            assert _Always503.count == 2
            logger.debug("attempt bound stopped after %s requests", _Always503.count)
        finally:
            server.shutdown()

    async def test_timeout_bound_is_honoured(self):
        _Always503.count = 0
        server = self._serve(_Always503)
        try:
            port = server.server_address[1]
            start = time.monotonic()
            with pytest.raises(httpx.HTTPStatusError):
                await check_api_is_ready(
                    f"http://127.0.0.1:{port}/health/ready", timeout=0.1
                )
            # The time-based stop bound gives up quickly even though the
            # server keeps returning 503.
            elapsed = time.monotonic() - start
            assert elapsed < 5
            logger.debug("timeout bound gave up after %.2fs", elapsed)
        finally:
            server.shutdown()


class TestMakeRetryerHttpx:
    """Isolated tests of the httpx retryer's retry predicate."""

    @pytest.fixture(autouse=True)
    def _fast_waits(self, monkeypatch):
        # Neutralise the exponential backoff so tests do not sleep.
        monkeypatch.setattr(api_utils, "wait_random_exponential", lambda **kw: 0.0)

    def _status_error(self, status_code: int) -> httpx.HTTPStatusError:
        request = httpx.Request("GET", "http://example.com/x")
        response = httpx.Response(status_code, request=request)
        return httpx.HTTPStatusError(
            f"HTTP {status_code}", request=request, response=response
        )

    async def test_retries_transient_connect_error_then_succeeds(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls < 3:
                raise httpx.ConnectError("connection reset")
            return "ok"

        retryer = api_utils._make_retryer_httpx(attempts=5)
        result = await retryer(flaky)
        assert result == "ok"
        assert calls == 3

    async def test_retries_read_error(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadError("stream broken")
            return "ok"

        retryer = api_utils._make_retryer_httpx(attempts=5)
        result = await retryer(flaky)
        assert result == "ok"
        assert calls == 2

    async def test_retries_read_timeout(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise httpx.ReadTimeout("slow")
            return "ok"

        retryer = api_utils._make_retryer_httpx(attempts=5)
        result = await retryer(flaky)
        assert result == "ok"
        assert calls == 2

    async def test_retries_http_500(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self._status_error(500)
            return "ok"

        retryer = api_utils._make_retryer_httpx(attempts=5)
        result = await retryer(flaky)
        assert result == "ok"
        assert calls == 2

    async def test_retries_http_429(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise self._status_error(429)
            return "ok"

        retryer = api_utils._make_retryer_httpx(attempts=5)
        result = await retryer(flaky)
        assert result == "ok"
        assert calls == 2

    async def test_does_not_retry_http_404(self):
        # 404 is a client mistake; only 429 and 5xx are retried.
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            raise self._status_error(404)

        retryer = api_utils._make_retryer_httpx(attempts=5)
        with pytest.raises(httpx.HTTPStatusError):
            await retryer(flaky)
        assert calls == 1

    async def test_retries_timeout(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TimeoutError()
            return "ok"

        retryer = api_utils._make_retryer_httpx(attempts=5)
        result = await retryer(flaky)
        assert result == "ok"
        assert calls == 2

    async def test_does_not_retry_unrelated_exception(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            raise ValueError("not an http error")

        retryer = api_utils._make_retryer_httpx(attempts=5)
        with pytest.raises(ValueError):
            await retryer(flaky)
        assert calls == 1

    async def test_attempts_bound_honoured(self):
        calls = 0

        async def flaky():
            nonlocal calls
            calls += 1
            raise httpx.ConnectError("down")

        retryer = api_utils._make_retryer_httpx(attempts=3)
        with pytest.raises(httpx.ConnectError):
            await retryer(flaky)
        assert calls == 3


class TestValidateUrl:
    """HttpUrl rejects ftp/file/ws, accepts http/https."""

    def test_accepts_http(self):
        assert api_utils.validate_url("http://example.com") == "http://example.com"

    def test_accepts_https(self):
        assert (
            api_utils.validate_url("https://example.com/path?x=1")
            == "https://example.com/path?x=1"
        )

    def test_rejects_ftp(self):
        with pytest.raises(ValueError, match="not a valid HTTP"):
            api_utils.validate_url("ftp://example.com")

    def test_rejects_file(self):
        with pytest.raises(ValueError, match="not a valid HTTP"):
            api_utils.validate_url("file:///etc/passwd")

    def test_rejects_ws(self):
        with pytest.raises(ValueError, match="not a valid HTTP"):
            api_utils.validate_url("ws://example.com")

    def test_rejects_bare(self):
        with pytest.raises(ValueError, match="not a valid HTTP"):
            api_utils.validate_url("example.com")


class TestSse:
    """stream_events guards: timeout and malformed JSON."""

    def test_stream_events_uses_bounded_timeout(self, monkeypatch):
        import httpx as httpx_mod
        from klea_utils.api import sse

        captured = {}

        class FakeClient:
            def __init__(self, timeout=None):
                captured["timeout"] = timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, method, url, **kw):
                class FakeStream:
                    async def __aenter__(self):
                        class FakeResp:
                            def raise_for_status(self):
                                pass

                            async def aiter_lines(self):
                                if False:
                                    yield ""

                        return FakeResp()

                    async def __aexit__(self, *a):
                        return False

                return FakeStream()

        monkeypatch.setattr(httpx_mod, "AsyncClient", FakeClient)

        async def run():
            gen = sse.stream_events("q", "c", "http://x")
            try:
                await asyncio.wait_for(gen.__anext__(), timeout=0.5)
            except StopAsyncIteration:
                pass
            except TimeoutError:
                pass

        asyncio.run(run())
        assert captured["timeout"] is not None
        # httpx.Timeout(300) is not None and not infinite
        assert str(captured["timeout"]) != "None"

    def test_stream_events_skips_malformed_json(self, monkeypatch):

        import httpx as httpx_mod
        from klea_utils.api import sse

        class FakeResp:
            def raise_for_status(self):
                pass

            async def aiter_lines(self):
                yield 'data: {"type": "progress", "node": "a"}'
                yield "data: {not json"
                yield 'data: {"type": "complete", "message_for_user": "hi"}'

        class FakeStream:
            async def __aenter__(self):
                return FakeResp()

            async def __aexit__(self, *a):
                return False

        class FakeClient:
            def __init__(self, timeout=None):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            def stream(self, *a, **kw):
                return FakeStream()

        monkeypatch.setattr(httpx_mod, "AsyncClient", FakeClient)

        async def run():
            events = []
            async for ev in sse.stream_events("q", "c", "http://x"):
                events.append(ev)
            return events

        events = asyncio.run(run())
        assert len(events) == 2
        assert events[0]["type"] == "progress"
        assert events[1]["type"] == "complete"
