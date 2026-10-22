---
status: "accepted"
date: 2026-10-12
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# Terminal UI: a Textual client with a single active session

## Context and Problem Statement

Klea already has a full browser client (NiceGUI, `klea web`) that consumes
the graph's `/query/stream` SSE endpoint, and the terminal client is a
lightweight `input()`-based REPL (`utils_pkg/klea_utils/ui/tui/repl.py`) that
was always a temporary stand-in for testing simple queries.  ADR-0026 planned
a real terminal UI as an SSE consumer, and `devdocs/backlog.md` records it as
"build the real TUI as a new module in `klea_utils/ui/tui`, following the
web-UI pattern".

The REPL renders only progress text and the final answer; it ignores the
`inspect`, `state`, `usage`, `tool` and `context` events, has no panes, no
HITL/permission forms beyond a blocking `input()` prompt, and no session
management.  A real TUI should be close to the web client in capability while
respecting the terminal's constraints.

Two decisions are structural and expensive to change later: (1) which TUI
toolkit and how the shared/ app-specific code is split, and (2) the session
and run model.  For (2) the concrete question is whether one terminal should
manage many sessions (like the web page) or one active session at a time
(like the coding-agent CLIs), and how much concurrent-run machinery the
frontend must own.

## Decision Drivers

* **Parity, not identity**: the TUI should surface the same stream events and
  controls as the web (transcript, inspect/status panes, mode/access/model
  selectors, HITL and permission forms, session commands), but the transcript
  is plain-text-first, so web widgets do not transfer.
* **Reuse**: the graph, the API/SSE transport and the session-command
  framework are frontend-agnostic and must not be forked; the shared chat
  state and stream reducer should have one source of truth (ADR-0031 keeps
  `klea_utils` a components/helpers library).
* **Client-server (ADR-0026)**: the TUI is an in-process SSE consumer of the
  same `klea-agent-serve` / `klea-rag-serve`; it must not import the
  orchestrator or own graph state.
* **The server is the concurrency authority (ADR-0043)**: one active run per
  thread, but different threads run concurrently; the web page's per-chat run
  registry exists only because one page can show several chats that each run.
* **Actual parallel usage**: the maintainer runs parallel sessions as separate
  terminal processes under a multiplexer (tmux/byobu), not as several sessions
  in one UI.  A frontend registry for concurrent in-process runs would be
  unused complexity.
* **Reversibility**: the session/run model is hard to change later, so the
  design should keep the cheap upgrade path (to multi-session) open.
* **No server-side auth**: `user_id` is an opaque client-supplied token
  (`sessions.py`), so identity must stay client-local and never be inferred
  from the server (leakage on shared deployments).

## Considered Options

* **A. Extend the `input()` REPL** (no full-screen TUI).
* **B. Textual TUI with a persistent multi-session sidebar** (web parity).
* **C. Textual TUI, one active session plus a summonable picker (chosen).**
* **D. Strict one-session-per-instance** (no in-TUI switch; CLI resume only).
* **E. A non-Textual full-screen toolkit** (prompt_toolkit / urwid / curses).

## Decision Outcome

Chosen option: "**C. Textual TUI, one active session plus a summonable
picker**", because it matches how terminal coding agents are actually used
(one session in the main loop, switch/resume on demand) and matches the
maintainer's multiplexer workflow, while keeping the frontend's run model to
the single case the terminal needs.  Textual is the toolkit (over E) because
its async model fits the existing async SSE generator, it renders Rich
renderables (Markdown, syntax-highlighted diffs) directly, it has a first-class
in-process test pilot, and it is the de-facto standard for modern Python TUIs.

Concrete decisions:

* **Toolkit and layout**: shared Textual widgets live in
  `utils_pkg/klea_utils/ui/tui/components/`, mirroring the NiceGUI
  `components/` split (ADR-0031).  Each app composes its own screen and slots
  app-specific controls (the agent's operating-mode / tool-access selectors)
  via a context object analogous to `PageContext`.
* **Entry point**: a new `tui` subcommand in `make_client_app`
  (`klea_utils/ui/cli.py`), launched **in-process** (Textual is a library,
  unlike the NiceGUI `python app.py` subprocess).  It is guarded by a new
  `tui` extra (`textual`) via `require_extra`, added to the `full` extra so CI
  installs it.  The temporary `cli` REPL was removed once the TUI reached
  parity (2026-10-22); bare `klea` / `klea-rag` now launches the TUI.
* **Session model**: one **active** session rendered in the main loop.  A
  summonable picker (`Ctrl+S` / `/sessions`) switches, resumes, renames and
  deletes sessions; `--continue` resumes the most recently updated session and
  `--resume <chat_id>` loads a specific one.
* **Run model**: at most **one** in-flight run, always attached to the active
  session.  Stop cancels it (local task cancel plus `/query/cancel`).
  Switching sessions is **blocked while streaming** (the user stops first),
  mirroring the `while_streaming: block` command semantics (ADR-0047).  No
  per-chat run registry and no background repaint gating.
* **State**: keep a keyed `{user_id}:{chat_id}` session store with a **single
  active pointer**, rather than a bare single-chat dict, so a later move to
  concurrent multi-session is additive (add a run registry) rather than a
  rewrite.  The active session's messages/context are hydrated on switch; the
  picker lists only the server's metadata (`GET /chat/{user_id}`).
* **Shared extraction**: the framework-agnostic pieces currently inside the
  NiceGUI package move to `klea_utils/ui/`: the chat state (`ChatData`,
  `ensure_chat`, `resolve_chat_choice`, ...) to `ui/state.py`, the
  `apply_stream_event` reducer to `ui/stream_events.py`, and the server API
  client to `ui/client.py`.  The web modules re-point at these (one source of
  truth for the event contract); NiceGUI-only drivers (`run_stream`) stay in
  the web package.
* **Identity**: the TUI resolves `user_id` through one seam,
  `resolve_user_id()`, ordered `--user-id` > `KLEA_USER_ID` > a persisted
  TUI-local UUID.  Sharing a `user_id` with the web (so sessions appear in
  both) is a separate, deferred design (see More Information); the TUI only
  owns the seam.
* **Terminal rendering**: Rich renderables for Markdown, diffs and code; MIME
  selection mirrors the web's `chat_bubble` rules.  Inline media
  (`image/*`, `audio/*`) stays the text fallback, as on the web (ADR-0040).

### Invariants

These must hold in every phase; a change that breaks one needs a new ADR (or
an update to this one), not a phase-local workaround.  They are the design's
fitness function and are asserted by the tests listed under Confirmation where
testable.

* **I1 - One run, attached.** At most one graph run (SSE stream) is in flight
  per TUI process, and it always belongs to the active session.  The TUI never
  keeps a background run for a non-active session.
* **I2 - Switch is gated by the run.** The active session cannot change while a
  run is streaming; the switch is refused with guidance to Stop until the run
  completes, errors or is cancelled.
* **I3 - One reducer.** The TUI maps stream events to chat state only through
  the shared reducer (`klea_utils/ui/stream_events.py`) and the shared state
  (`klea_utils/ui/state.py`); it must not re-implement event-to-state mapping
  (the web and TUI share this contract, per ADR-0040/ADR-0031).
* **I4 - Thin client, server is the authority.** The TUI talks to the backend
  only over HTTP/SSE (`api/sse.py` and the client helpers).  It never imports
  the orchestrator/graph, and its session/transcript state is a projection that
  can be discarded and rehydrated from the server (`GET /chat/{user_id}`, ...).
* **I5 - Identity is client-local and explicit.** `user_id` is resolved only
  through the `resolve_user_id()` seam (`--user-id` > `KLEA_USER_ID` >
  persisted local UUID).  It is never inferred from the server, and a
  machine-local identity is sent only to a loopback server unless the user
  explicitly overrides it.
* **I6 - HITL is a pause, not a new turn.** An interrupt is answered or
  cancelled through the same run (`interrupt_response` / `interrupt_cancel`,
  at most one action per request); it is never handled by resending the query
  (ADR-0046).
* **I7 - Everything renders as text.** Every chat-renderable element has a
  text fallback and is shown even when its MIME cannot be rendered; inline
  media degrades to text rather than disappearing (ADR-0040).
* **I8 - Shared code is app-agnostic.** Widgets under `klea_utils/ui/tui/`
  import no app package; per-app controls (mode/access selectors) enter only
  through documented slots (ADR-0031).

### Consequences

* Good, because it reuses the graph, SSE transport, session-command framework
  and (after extraction) the chat state/reducer, so the event contract has one
  source of truth.
* Good, because the frontend owns no concurrency beyond a single attached run,
  so the TUI is materially simpler than the web page while still supporting
  the real parallel workflow (separate processes under a multiplexer).
* Good, because the keyed store with a single active pointer keeps the
  multi-session upgrade path cheap if it is ever needed.
* Good, because Textual's `run_test()` pilot and the pure reducer are both
  testable in-process, following the web UI testing approach.
* Bad, because the TUI is not feature-for-feature with the web: switching away
  from a running session requires stopping it first, and there is no
  always-visible session sidebar.
* Bad, because terminal rendering cannot match the web's HTML (tables, alerts,
  inline media); these degrade to plain Rich renderables.
* Bad, because the Phase 1 extraction touches many web imports (a mechanical
  but broad rename); the existing web test suite is the safety net.
* Bad, because `textual` becomes an optional dependency that CI must install
  and keep compatible.

### Confirmation

* `klea tui --help` / `klea-rag tui --help` exit 0 (heavy imports deferred, per
  the CLI conventions in `AGENTS.md`).
* `ruff`, `ty`, and `make html` are clean; the existing web (NiceGUI) test
  suite still passes after the shared extraction.
* Textual `run_test()` pilot tests drive send, streaming, Stop, HITL and the
  session picker against a fake backend (see `system/tui-testing.md`).
* A live smoke test connects the TUI to a running `klea-serve` /
  `klea-rag-serve` and completes a turn.
* Invariants are asserted where testable: I1/I2 and I6 by the pilot tests
  (a switch during a run is refused; one run at a time); I3 by the shared
  reducer unit tests; I4/I8 by import checks (the shared TUI modules and the
  app entries import no orchestrator/app package across the boundary); I5 by
  identity-resolution tests; I7 by the MIME/fallback render tests.

## Pros and Cons of the Options

### A. Extend the `input()` REPL

* Good, because it is the smallest change and already exists.
* Bad, because `input()` cannot render panes, forms or live status; it cannot
  reach web parity and would be rewritten anyway.

### B. Textual TUI with a persistent multi-session sidebar

Web parity: a sidebar listing sessions, switch/create/delete, concurrent
per-chat runs with a run registry.

* Good, because it is closest to the web page and lets one terminal manage
  several running sessions.
* Bad, because it needs per-chat run registries, background repaint gating and
  a sidebar that is cramped in a terminal, for a workflow the maintainer does
  not use.

### C. Textual TUI, one active session plus a summonable picker (chosen)

* Good, because it matches the industry-standard terminal-agent UX and the
  multiplexer workflow, and keeps the run model to one attached run.
* Neutral, because switching mid-run is blocked (a deliberate simplification).
* Bad, because it is not feature-for-feature with the web (no always-visible
  sidebar, no switching away from a running session).

### D. Strict one-session-per-instance

* Good, because it is the simplest possible model (no picker at all).
* Bad, because there is no way to switch or browse history without restarting
  the TUI with CLI flags.

### E. A non-Textual full-screen toolkit (prompt_toolkit / urwid / curses)

* Good, because prompt_toolkit is already an indirect dependency in some
  environments and curses is stdlib.
* Bad, because none gives an async widget framework, Rich rendering and an
  in-process test pilot together; the app-specific work would be larger.

## More Information

* Planned architecture and component contract: `system/tui.md` (companion to
  this ADR); terminal-rendering non-goals are recorded there.
* Code pointers: `utils_pkg/klea_utils/ui/cli.py` (`make_client_app`, where the
  `tui` subcommand is added), `utils_pkg/klea_utils/ui/tui/` (shared widgets),
  `utils_pkg/klea_utils/ui/{state,stream_events,client}.py` (extracted shared
  pieces), `utils_pkg/klea_utils/api/sse.py` (SSE client, reused as-is),
  `utils_pkg/klea_utils/commands/common.py` (session-command framework),
  `agent_pkg/klea_agent/ui/tui/` and `rag_pkg/klea_rag/ui/tui/` (app entries).
* Related: ADR-0026 (client-server, TUI as SSE consumer), ADR-0031 (apps own
  UI composition), ADR-0047 (session commands), ADR-0046 (HITL interrupt/
  resume), ADR-0007 (path permissions), ADR-0040/ADR-0013 (stream contract),
  ADR-0043 (single active run per thread).
* Deferred: sharing a `user_id` between the web and TUI (so sessions are
  visible in both) is left to a separate design recorded in the backlog.  The
  server has no auth and treats `user_id` as an opaque token, so a naive
  "most recently used" identity would leak across users on a shared
  deployment; the intended direction is a single host-local identity file
  with a localhost-only guard and first-run adoption.

## Addendum (2026-10-13): multi-session tabs re-examined

Revisited after other terminal agents shipped multi-session tabs.  The
decision stands: the Klea TUI keeps one active session plus the summonable
picker, not a tab strip.  The reason is structural, not stylistic.

In those tools a session run executes server-side and is observed over a
single global event stream, so a tab holds no run connection and background
sessions are cheap.  In Klea the run *is* its SSE response: `chat_core`'s
`stream_response` drives the graph inside the HTTP task and a client
disconnect cancels the run.  Background sessions would therefore require the
frontend to hold one stream per running session - a per-chat run registry,
the machinery this ADR rejected (I1/I2) - or the deferred decoupled-run /
event-buffer work (`devdocs/backlog.md`, "Durable / resumable SSE stream").
Without one of those, a tab that is not active cannot stay live or answer
HITL, so tabs would be a degraded experience rather than a feature.

The need tabs would serve is already covered: the picker switches within one
process, the web client is natively multi-session, and multiple terminal
instances (under a multiplexer) give parallel sessions.  The keyed store with
a single active pointer keeps the multi-session upgrade additive if it is
ever wanted.
