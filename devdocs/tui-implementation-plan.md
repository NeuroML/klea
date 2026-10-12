# TUI implementation plan (working checklist)

Status: in progress.  Resume file for the Textual TUI work.  The design is
settled in ADR-0050 and the architecture/component contract in
`system/tui.md`; this file is the step-by-step execution checklist with
status, so a new session can pick up where the last left off.

## Ground rules (from `AGENTS.md`)

Work in small, atomic, review-gated steps.  After every step:

1. Apply the single change.
2. Verify: `ruff check` + `ruff format` (affected package), `ty check` (repo
   root), plus tests / `--help` / `make html` as applicable.
3. Present `git diff --stat` then the full diff; STOP for review.
4. Commit only on explicit approval (conventional message), then continue.

Keep devdocs in sync in the same step as the code they describe
(code -> devdoc map in `AGENTS.md`).  ASCII-only text.  Do not touch
unrelated files (ruff can fix pre-existing lint in other files: revert
those).

## Locked design decisions (ADR-0050)

* Toolkit: Textual; shared widgets in `klea_utils/ui/tui/`, per-app
  composition (`klea_agent`, `klea_rag`) via a context object (like
  `PageContext`).
* Entry point: a new `tui` subcommand in `make_client_app`
  (`klea_utils/ui/cli.py`), launched in-process; guarded by a `tui` extra
  (`textual`) via `require_extra`, added to `full`.  The temporary `cli`
  REPL stays until the TUI reaches parity (Phase 6.4).
* Session model: one active session in the main loop + a summonable picker
  (`Ctrl+S` / `/sessions`); `--continue` / `--resume <chat_id>`.  Switching
  is blocked while streaming.
* Run model: one in-flight run, always attached to the active session; keyed
  `{user_id}:{chat_id}` store with a single active pointer.
* Identity: `resolve_user_id()` seam: `--user-id` > `KLEA_USER_ID` >
  persisted TUI-local UUID.  Web/TUI shared identity is deferred (backlog).
* Layout: a two-panel horizontal split, both full height -- left chat panel
  (title, transcript/inspect toggle, turn status, input, keys), right status
  panel (mode/access, models, state sections, token usage).

Invariants I1-I8 are in ADR-0050 and restated in `system/tui.md`.

## Status

* Phase 0 (design docs): DONE.  ADR-0050, `system/tui.md`, README/backlog/
  AGENTS.md housekeeping.
* Phase 1 (shared extraction): DONE.
  - `ui/state.py` (moved from `web/nicegui/state.py`) - 6801517c.
  - `ui/stream_events.py` (`apply_stream_event`) - 848ca0e4.
  - `ui/client.py` (moved from `web/nicegui/client.py`) - 99cbac4c.
  - Verified: `ruff` clean, `ty` 27 pre-existing (none new), utils_pkg and
    agent_pkg web tests green.
* Phase 2+: see the checklist below.  Next step is **2.1**.

## Checklist

Each item ends at a review + commit gate.  Mark `[x]` when committed.

### Phase 2 - skeleton + streaming

- [ ] 2.1 Add `[tui]` extra (`textual`) to `utils_pkg/setup.cfg` and include
  it in `[full]`; add a `require_extra("tui", "textual")` guard and a `tui`
  subcommand in `make_client_app` launching a stub.  Verify `klea tui --help`
  and `klea-rag tui --help` exit 0.
- [ ] 2.2 Shared Textual `App`/`Screen` skeleton in `klea_utils/ui/tui/`
  (two-panel layout: full-height chat panel + full-height status panel) plus
  thin `klea_agent`/`klea_rag` TUI entry modules (`run_*_tui`).  Verify launch
  against a running server.
- [ ] 2.3 Wire streaming: submit -> `@work` worker -> `stream_events` -> feed
  the shared reducer -> render `progress` + `complete`.
- [ ] 2.4 Turn status (spinner / error+Retry / stopped / awaiting-input),
  Stop (keybinding + `request_cancel`), httpx error handling.

### Phase 3 - rendering, panes, commands

- [ ] 3.1 MIME-aware transcript rendering (Markdown via `rich.markdown`,
  diff/code via `rich.syntax.Syntax`, tool blocks, headers).
- [ ] 3.2 Inspector view (inspect events, `format_details`, per-query
  sections) as a chat-panel toggle.
- [ ] 3.3 Status panel (full-height right): state sections (keyed), token
  usage, context events.
- [ ] 3.4 Session commands: parse against the merged catalogue (client +
  `GET /commands`), local dispatch + `/help`, server commands forwarded,
  `/` autocomplete.  Update `system/session-commands.md` if the contract
  changes.

### Phase 4 - HITL

- [ ] 4.1 HITL review/input `ModalScreen`; transcript rows via
  `render_interrupt_response`; resume/cancel through the same run.
- [ ] 4.2 Permission `ModalScreen` (per-path once/session/deny) + resume.
- [ ] 4.3 Re-present a pending interrupt on load/switch (hydrate from
  `GET /chat/{user_id}/{chat_id}/context` `pending_interrupt`).

### Phase 5 - sessions + selectors

- [ ] 5.1 `resolve_user_id()` seam (`--user-id` > `KLEA_USER_ID` > persisted
  TUI-local UUID) + one-active-session state (keyed store + active pointer).
- [ ] 5.2 `--continue` / `--resume <chat_id>` + summonable session picker
  (`Ctrl+S` / `/sessions`) to switch/rename/delete; block switching while
  streaming.
- [ ] 5.3 Model/credential dialog (`fetch_catalogue_*`, set/clear overrides
  and credentials, missing-credential gating).
- [ ] 5.4 Agent mode/access selector slots in the status panel.

### Phase 6 - composition, tests, docs

- [ ] 6.1 Finalize agent/rag TUI composition + slots; no app-specific code in
  `klea_utils/ui/tui`.
- [ ] 6.2 Tests: shared state/reducer unit tests (post-extraction) + Textual
  `run_test()` pilot suite with a fake backend; add
  `system/tui-testing.md`.
- [ ] 6.3 Public docs: `docs/components/tui.rst` + toctree + `CHANGELOG.md`;
  devdocs sync (`system/streams.md` TUI consumer, ADR-0013/0026 stale
  "Textual planned" wording).  Verify `make html` exit 0.
- [ ] 6.4 Deprecate/drop the terminal REPL `cli` once parity is confirmed;
  update backlog + docs.

## Pointers

* `devdocs/adr/0050-textual-tui.md` (decision + invariants).
* `devdocs/system/tui.md` (architecture, component contract, event matrix).
* Reused: `klea_utils/api/sse.py`, `klea_utils/commands/common.py`,
  `klea_utils/ui/{state,stream_events,client}.py`,
  `klea_utils/api/hitl.py`, `klea_utils/ui/inspect_format.py`.
* Web counterparts to mirror: `klea_utils/ui/web/nicegui/`.
