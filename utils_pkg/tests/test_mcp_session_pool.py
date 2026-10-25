"""Tests for the per-session bundled MCP client pool.

These tests pin the contract of
:class:`klea_utils.mcp.session_pool.SessionMCPClients`: one client per
session, reused on later lookups, least-recently-used eviction at the cap,
an idle sweep bounded by a TTL, and deterministic teardown (``aclose`` /
``aclose_all``) that stays bounded even when a child ignores its shutdown
handshake.
"""

from __future__ import annotations

import asyncio

import pytest
from klea_utils.mcp.session_pool import (
    DEFAULT_CLOSE_TIMEOUT,
    DEFAULT_IDLE_TTL,
    DEFAULT_MAX_SESSIONS,
    ENV_CLOSE_TIMEOUT,
    ENV_IDLE_TTL,
    ENV_MAX_SESSIONS,
    SessionMCPClients,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeClient:
    """A pooled-client stand-in that records whether it was closed."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.closed = False
        self.root = f"/tmp/{session_id}"
        self.lockfile: str | None = None
        self.starttime = 0.0

    async def aclose(self) -> None:
        self.closed = True


class _FailingClient(FakeClient):
    """A client whose ``aclose`` raises."""

    async def aclose(self) -> None:
        raise RuntimeError("close failed")


class _HangingClient(FakeClient):
    """A client whose ``aclose`` outlives the pool's close timeout."""

    async def aclose(self) -> None:
        await asyncio.sleep(30.0)
        self.closed = True


class _SyncCloseClient:
    """A client that only exposes a synchronous ``close()``."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _SyncStopClient:
    """A client that only exposes a synchronous ``stop()``."""

    def __init__(self) -> None:
        self.closed = False

    def stop(self) -> None:
        self.closed = True


class FakeClock:
    """A monotonic clock the tests advance by hand."""

    def __init__(self, now: float = 0.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class CreatedClients:
    """A client factory that records every client it creates."""

    def __init__(self, client_cls: type[FakeClient] = FakeClient) -> None:
        self.client_cls = client_cls
        self.clients: list[FakeClient] = []

    async def factory(self, session_id: str) -> FakeClient:
        client = self.client_cls(session_id)
        self.clients.append(client)
        return client

    def client(self, session_id: str) -> FakeClient:
        """Return the client created for *session_id*."""
        for client in self.clients:
            if client.session_id == session_id:
                return client
        raise AssertionError(f"no client was created for {session_id!r}")


async def _unused_factory(session_id: str) -> FakeClient:
    """Fail loudly if a pool creates a client it should never need."""
    raise AssertionError(f"unexpected client creation for {session_id!r}")


def make_pool(
    created: CreatedClients,
    *,
    max_sessions: int = 4,
    idle_ttl: float = 0.0,
    close_timeout: float = 5.0,
    clock: FakeClock | None = None,
) -> SessionMCPClients:
    """Build a pool over *created* with deterministic, test-only limits.

    ``idle_ttl`` defaults to ``0`` (sweeping disabled) so that only the tests
    which ask for TTL behaviour are affected by the passage of time.
    """
    return SessionMCPClients(
        created.factory,
        max_sessions=max_sessions,
        idle_ttl=idle_ttl,
        close_timeout=close_timeout,
        clock=clock if clock is not None else FakeClock(),
    )


# ---------------------------------------------------------------------------
# Creation and reuse
# ---------------------------------------------------------------------------


async def test_get_creates_a_client_once_and_reuses_it() -> None:
    """A second lookup for the same session returns the pooled client."""
    created = CreatedClients()
    pool = make_pool(created)

    first = await pool.get("a")
    again = await pool.get("a")

    assert first is again
    assert [client.session_id for client in created.clients] == ["a"]
    assert len(pool) == 1
    assert "a" in pool


async def test_get_moves_a_reused_session_to_the_recent_end() -> None:
    """A reuse makes the session the most recently used one."""
    created = CreatedClients()
    pool = make_pool(created, max_sessions=2)

    await pool.get("a")
    await pool.get("b")
    await pool.get("a")

    assert pool.session_ids() == ["b", "a"]


async def test_a_failed_creation_is_not_pooled() -> None:
    """A factory error leaves the pool empty, so a retry can create anew."""
    attempts: list[str] = []

    async def factory(session_id: str) -> FakeClient:
        attempts.append(session_id)
        if len(attempts) == 1:
            raise RuntimeError("spawn failed")
        return FakeClient(session_id)

    pool = SessionMCPClients(factory, max_sessions=2, idle_ttl=0.0, close_timeout=5.0)

    with pytest.raises(RuntimeError, match="spawn failed"):
        await pool.get("s")

    assert len(pool) == 0
    assert "s" not in pool

    client = await pool.get("s")

    assert client.session_id == "s"
    assert len(pool) == 1


async def test_concurrent_get_creates_a_single_client() -> None:
    """Two simultaneous lookups serialise, so only one child is started."""
    started: list[str] = []

    async def factory(session_id: str) -> FakeClient:
        started.append(session_id)
        await asyncio.sleep(0)
        return FakeClient(session_id)

    pool = SessionMCPClients(factory, max_sessions=2, idle_ttl=0.0, close_timeout=5.0)

    first, second = await asyncio.gather(pool.get("s"), pool.get("s"))

    assert first is second
    assert started == ["s"]
    assert len(pool) == 1


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


async def test_lru_eviction_closes_the_least_recently_used_client() -> None:
    """Creating one client over the cap evicts and closes the oldest one."""
    created = CreatedClients()
    pool = make_pool(created, max_sessions=2)

    await pool.get("a")
    await pool.get("b")
    await pool.get("c")

    assert pool.session_ids() == ["b", "c"]
    assert len(pool) == 2
    assert created.client("a").closed is True
    assert created.client("b").closed is False
    assert created.client("c").closed is False


async def test_lru_eviction_spares_a_recently_reused_client() -> None:
    """Reusing a session protects it from the next eviction."""
    created = CreatedClients()
    pool = make_pool(created, max_sessions=2)

    await pool.get("a")
    await pool.get("b")
    await pool.get("a")  # "a" is now the most recently used one.
    await pool.get("c")  # ... so the eviction takes "b".

    assert pool.session_ids() == ["a", "c"]
    assert created.client("b").closed is True
    assert created.client("a").closed is False
    assert created.client("c").closed is False


# ---------------------------------------------------------------------------
# Idle sweep
# ---------------------------------------------------------------------------


async def test_idle_sweep_closes_a_client_past_the_ttl() -> None:
    """A client unused for longer than the TTL is closed and forgotten."""
    created = CreatedClients()
    clock = FakeClock()
    pool = make_pool(created, idle_ttl=60.0, clock=clock)
    await pool.get("a")

    clock.advance(61.0)
    closed = await pool.sweep_idle()

    assert closed == ["a"]
    assert created.client("a").closed is True
    assert len(pool) == 0


async def test_idle_sweep_keeps_a_recently_used_client() -> None:
    """Only the sessions past the TTL are swept."""
    created = CreatedClients()
    clock = FakeClock()
    pool = make_pool(created, idle_ttl=60.0, clock=clock)
    await pool.get("a")
    clock.advance(30.0)
    await pool.get("b")
    clock.advance(31.0)  # "a" is 61s idle, "b" only 31s.

    closed = await pool.sweep_idle()

    assert closed == ["a"]
    assert pool.session_ids() == ["b"]
    assert created.client("b").closed is False


async def test_idle_sweep_is_disabled_by_a_zero_ttl() -> None:
    """An idle TTL of ``0`` turns the sweep off entirely."""
    created = CreatedClients()
    clock = FakeClock()
    pool = make_pool(created, idle_ttl=0.0, clock=clock)
    await pool.get("a")

    clock.advance(10_000.0)

    assert await pool.sweep_idle() == []
    assert len(pool) == 1
    assert created.client("a").closed is False


async def test_get_sweeps_idle_clients_before_looking_up() -> None:
    """A lookup sweeps first, so a stale client is closed on the way in."""
    created = CreatedClients()
    clock = FakeClock()
    pool = make_pool(created, idle_ttl=60.0, clock=clock)
    await pool.get("a")

    clock.advance(120.0)
    await pool.get("b")

    assert created.client("a").closed is True
    assert created.client("b").closed is False
    assert pool.session_ids() == ["b"]


# ---------------------------------------------------------------------------
# Closing a single client
# ---------------------------------------------------------------------------


async def test_aclose_closes_only_the_named_client() -> None:
    """``aclose`` closes and forgets exactly the requested session."""
    created = CreatedClients()
    pool = make_pool(created)
    await pool.get("a")
    await pool.get("b")

    assert await pool.aclose("a") is True

    assert created.client("a").closed is True
    assert created.client("b").closed is False
    assert pool.session_ids() == ["b"]
    assert len(pool) == 1


async def test_aclose_of_an_unpooled_session_returns_false() -> None:
    """Closing a session that was never pooled is a no-op."""
    created = CreatedClients()
    pool = make_pool(created)

    assert await pool.aclose("missing") is False

    assert len(pool) == 0
    assert created.clients == []


@pytest.mark.parametrize(
    "client_cls", [_SyncCloseClient, _SyncStopClient], ids=["close", "stop"]
)
async def test_close_falls_back_to_a_synchronous_method(client_cls) -> None:
    """A client without ``aclose`` is closed via ``close`` or ``stop``."""
    client = client_cls()

    async def factory(session_id: str) -> FakeClient:
        return client

    pool = SessionMCPClients(factory, max_sessions=1, idle_ttl=0.0, close_timeout=5.0)
    await pool.get("s")

    assert await pool.aclose("s") is True
    assert client.closed is True


# ---------------------------------------------------------------------------
# Closing every client
# ---------------------------------------------------------------------------


async def test_aclose_all_closes_every_client() -> None:
    """``aclose_all`` closes every pooled client and empties the pool."""
    created = CreatedClients()
    pool = make_pool(created)
    for session_id in ("a", "b", "c"):
        await pool.get(session_id)

    assert await pool.aclose_all() == 3

    assert all(client.closed for client in created.clients)
    assert len(pool) == 0
    assert pool.session_ids() == []


async def test_aclose_all_of_an_empty_pool_returns_zero() -> None:
    """An empty pool closes nothing, which makes shutdown idempotent."""
    created = CreatedClients()
    pool = make_pool(created)

    assert await pool.aclose_all() == 0


async def test_aclose_all_closes_the_rest_when_one_close_fails() -> None:
    """One broken client does not stop the others from closing."""
    failing = _FailingClient("bad")
    healthy = FakeClient("good")

    async def factory(session_id: str) -> FakeClient:
        return {"bad": failing, "good": healthy}[session_id]

    pool = SessionMCPClients(factory, max_sessions=2, idle_ttl=0.0, close_timeout=5.0)
    await pool.get("bad")
    await pool.get("good")

    assert await pool.aclose_all() == 2

    assert healthy.closed is True
    assert failing.closed is False
    assert len(pool) == 0


async def test_aclose_all_is_bounded_by_the_close_timeout() -> None:
    """A child that never acknowledges shutdown cannot hang the teardown."""
    hanging = _HangingClient("slow")

    async def factory(session_id: str) -> FakeClient:
        return hanging

    pool = SessionMCPClients(factory, max_sessions=1, idle_ttl=0.0, close_timeout=0.05)
    await pool.get("slow")

    # Far below the client's 30s sleep: the pool's timeout ends the wait.
    closed = await asyncio.wait_for(pool.aclose_all(), timeout=5.0)

    assert closed == 1
    assert hanging.closed is False
    assert len(pool) == 0


# ---------------------------------------------------------------------------
# Limits
# ---------------------------------------------------------------------------


def test_limits_are_read_from_the_environment(monkeypatch) -> None:
    """Unset arguments are resolved from the ``KLEA_MCP_*`` variables."""
    monkeypatch.setenv(ENV_MAX_SESSIONS, "3")
    monkeypatch.setenv(ENV_IDLE_TTL, "12.5")
    monkeypatch.setenv(ENV_CLOSE_TIMEOUT, "2.5")

    pool = SessionMCPClients(_unused_factory)

    assert pool.max_sessions == 3
    assert pool.idle_ttl == 12.5
    assert pool.close_timeout == 2.5


def test_invalid_environment_values_fall_back_to_the_defaults(monkeypatch) -> None:
    """A malformed variable is ignored rather than crashing the pool."""
    monkeypatch.setenv(ENV_MAX_SESSIONS, "many")
    monkeypatch.setenv(ENV_IDLE_TTL, "soon")
    monkeypatch.setenv(ENV_CLOSE_TIMEOUT, " ")

    pool = SessionMCPClients(_unused_factory)

    assert pool.max_sessions == DEFAULT_MAX_SESSIONS
    assert pool.idle_ttl == DEFAULT_IDLE_TTL
    assert pool.close_timeout == DEFAULT_CLOSE_TIMEOUT


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_sessions": 0}, "max_sessions"),
        ({"max_sessions": -1}, "max_sessions"),
        ({"max_sessions": 2, "idle_ttl": -1.0}, "idle_ttl"),
        ({"max_sessions": 2, "close_timeout": 0.0}, "close_timeout"),
        ({"max_sessions": 2, "close_timeout": -2.0}, "close_timeout"),
    ],
)
def test_out_of_range_limits_are_rejected(kwargs, message: str) -> None:
    """Nonsensical limits fail fast at construction time."""
    with pytest.raises(ValueError, match=message):
        SessionMCPClients(_unused_factory, **kwargs)
