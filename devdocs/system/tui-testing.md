# TUI testing: in-process Textual `run_test()` pilot

Status: implemented.  Covers the terminal (Textual) frontend tests.  The
harness drives the real shared app in-process with Textual's `run_test()`
pilot against a canned fake backend, so component and app behaviour can be
asserted without a server or a model.

## What is tested where

| Layer | Driver | Location | Needs |
|-------|--------|----------|-------|
| Pure helpers | pytest | `utils_pkg/tests/ui/tui/test_components.py` | -- |
| Shared app | `App.run_test()` pilot | `utils_pkg/tests/ui/tui/test_app.py` | fake backend |
| Identity seam | pytest | `utils_pkg/tests/ui/tui/test_identity.py` | -- |
| Invariant I8 | pytest (AST scan) | `utils_pkg/tests/ui/tui/test_isolation.py` | -- |
| Agent slots | `App.run_test()` pilot / pytest | `agent_pkg/tests/test_tui_*.py` | fake backend (slots) |
| RAG entry | pytest | `rag_pkg/tests/test_tui_page.py` | -- |

The app tests assert what a user sees and what the app sends; the backend /
protocol tests elsewhere assert the server's responses.  A passing app test
means the app consumes a given event/payload correctly, not that the backend
produces it.

The tests run from within a package (`cd utils_pkg && pytest`), under
`asyncio_mode = "auto"`; they run under pytest-xdist (`-n auto`) as part of
the default suite.  The global chat store is process-global, so each worker
is isolated by the process boundary and each test by the autouse
`_isolate_chat_store` fixture.

## Harness

`utils_pkg/tests/ui/tui/conftest.py` provides:

* `FakeBackend` -- canned data for the app's calls and a scriptable
  `stream_events` async generator (its `scripts` list yields one run per call;
  a default `complete` is used when exhausted).  Fields: `chats`,
  `messages`, `chat_context`, `model_info`, `catalogue_providers`,
  `catalogue_models`, `credentials`, plus recorders (`calls`,
  `renames`, `deletes`, `model_overrides`, `model_clears`,
  `credentials_set`, `credentials_cleared`).
* the `fake_backend` fixture -- patches the client seams onto the fake.  Note
  the **import-time binding**: the shared helpers bind their fetchers at
  import, so the fixture patches both `klea_utils.api.sse.*` /
  `klea_utils.ui.client.*` and the *component modules'* bound names
  (`klea_utils.ui.model_config.*`, and
  `klea_utils.ui.tui.components.{model_dialog,credentials_dialog}.*`).
  Patching only the source module would not rebind the imported names.
* the `make_app` fixture -- a factory building a
  `KleaTuiApp(TuiContext(...))`; parameters `chat_id`, `user_id`,
  `resume_chat_id`, `continue_session`, `status_extras`.
* the autouse `_isolate_chat_store` fixture -- snapshots and restores the
  global `klea_utils.ui.state.chats` store around each test.

## Writing a test

```python
async def test_example(fake_backend, make_app):
    fake_backend.scripts = [[{"type": "complete", "message_for_user": "hi"}]]
    app = make_app()
    async with app.run_test() as pilot:
        await pilot.pause()
        await app.workers.wait_for_complete()
        await _submit(pilot, app, "hello")  # types + Enter
        await _settle(pilot, app)  # pause + wait workers + pause
```

* `make_app()` builds the app; opening it with `async with app.run_test()`
  mounts the screen and runs `on_mount` (catalogue load, chat hydration).
* `_submit(pilot, app, text)` sets `#query-input` and presses Enter;
  `_settle(pilot, app)` pauses, waits for all workers, and pauses again -
  needed after anything that starts a worker (`@work`).
* `_chat()` returns the active test chat's data dict (`chats[":test-chat"]`).
* Set `fake_backend.scripts` before sending to script the run's events; assert
  what the app sent via `fake_backend.calls`.

Widgets are found by id: `app.screen.query_one("#query-input", Input)`,
`app.screen.query_one("#model-save", Button).press()`, etc.  Modal screens are
checked with `isinstance(app.screen, SessionPicker)`.

## Gotchas

* **Never `await` a `@work` method** -- it returns a `Worker`, not an
  awaitable (this raised `TypeError: object Worker can't be used in 'await'
  expression`).  Call it and let the worker run, or `self.run_worker(...)`;
  only a plain `async def` is awaited.
* A method that starts a worker and needs its result should either be a plain
  `async def` called from a worker, or fire-and-forget the worker.
* Refused operations (switch/model dialog while streaming, a blocked send)
  leave the transcript unchanged and only post a notification; assert on
  `fake_backend.calls == []` rather than on the transcript.
* A widget method may shadow a Textual base attribute (e.g. a helper named
  `_id` collides with `Widget._id`); prefer unambiguous names.

## Pointers

* Shared widgets: `klea_utils/ui/tui/`; app slots:
  `agent_pkg/klea_agent/ui/tui/`, `rag_pkg/klea_rag/ui/tui/`.
* Harness: `utils_pkg/tests/ui/tui/conftest.py`; the web counterpart is
  `system/web-ui-testing.md`.
* Contract: `system/tui.md` (ADR-0050 invariants); event payloads:
  `system/streams.md`.
* Textual testing API: `textual.app.App.run_test`, `textual.pilot.Pilot`.
