# Web UI testing: in-process NiceGUI user simulation

Status: implemented.  Covers the web (NiceGUI) frontend tests.  The harness
runs the real page in-process with NiceGUI's user simulation and a canned
fake backend, so component and page behaviour can be asserted without a
browser, a server, or a model.

## What is tested where

| Layer | Driver | Location | Needs |
|-------|--------|----------|-------|
| Shared components | `nicegui.testing.User` (in-process) | `utils_pkg/tests/ui/web/` | fake backend |
| Agent page | `User` | `agent_pkg/tests/ui/web/` | fake backend |
| RAG page | `User` | `rag_pkg/tests/ui/web/` | fake backend |
| Backend / protocol | pytest | `*/tests/test_*.py` (e.g. `test_chat_core.py`, `test_stream_events.py`) | -- |

The UI tests assert what a user sees and what the page sends; the backend
tests assert the server's responses.  These are deliberately separate: a
passing UI test means the page consumes a given event/payload correctly, not
that the backend produces it.

Not covered here:

* **End-to-end agent behaviour** (multi-turn, HITL, tool use, metrics) is an
  API/orchestrator concern; the planned `eval_pkg` scenario harness owns it
  (`agent-evaluation-harness.md`).  The interactive `agent_pkg/e2e/` suite
  stays a CLI smoke runner.
* **Browser-level tests** (`nicegui.testing.Screen`, Selenium) are not used;
  they are reserved for behaviour that only a real browser can exercise.

The UI tests run under pytest-xdist (`-n auto`) as part of each package's
default suite; no marker or serial handling is needed.

## Harness

Each package has a `tests/ui/web/conftest.py` providing:

* `FakeBackend` -- canned responses for the frontend's calls
  (`/health/ready`, sessions/messages/context, `models/active`, catalogue,
  credentials) and a scriptable `POST /query/stream` (its `stream_events`
  list becomes the SSE body).  Every request is recorded in
  `FakeBackend.requests`; `FakeBackend.stream_bodies()` returns the parsed
  `/query/stream` bodies.
* the `fake_backend` fixture -- patches `httpx.AsyncClient` so every
  frontend call routes through the fake transport.  This single seam covers
  `client.py`, `api/sse.py` and `api/utils.py`.
* an app fixture (`utils_user` / `agent_user` / `rag_user`) -- drives the
  real page composition with `nicegui.testing.user_simulation`.

## Writing a test

```python
async def test_example(agent_user):
    await agent_user.open("/")
    await agent_user.should_see("Start a conversation")
    agent_user.find("Scientific").click()
```

* `await user.open("/")` builds the page; the health probe and chat
  hydration run afterwards as background tasks.
* `await user.should_see("text")` / `should_not_see(...)` assert visible
  content (they retry a few times; pass `retries=` for slower background
  work).
* `user.find("text").click()` / `.type(...)` / `.trigger("event")` interact
  (these are synchronous).
* Set `fake_backend.stream_events` before sending a message to script the
  run's events.

## Pointers

* Components: `klea_utils/ui/web/nicegui/components/`.
* Page composition: `agent_pkg/klea_agent/ui/web/page.py`,
  `rag_pkg/klea_rag/ui/web/page.py`.
* Event contract: `streams.md`; request lifecycle: `api-sse-sequence.md`.
* NiceGUI testing API: `nicegui.testing` (`user_simulation`, `User`).
