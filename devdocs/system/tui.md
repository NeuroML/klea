# Terminal UI: architecture and component contract

Status: in progress (ADR-0050).  This note is the companion to ADR-0050 and
governs the shared Textual client in `utils_pkg/klea_utils/ui/tui/` and the
per-app entries.  The TUI is an SSE consumer of the same `/query/stream`
endpoint the web client uses, so the event payloads are unchanged
(`system/streams.md`); this note covers only how the terminal frontend is
built and what it must preserve.  The shared `App`/`Screen` two-panel
skeleton, the thin per-app entries, the single run worker (basic
`progress`/`complete` streaming, turn-status states, and Stop/Retry), the
MIME-aware transcript, the inspector view, the status pane, session commands,
the HITL review/input and permission modals, pending-interrupt
re-presentation, the `resolve_user_id` identity seam,
`--continue`/`--resume`, the session picker (rename/delete, via `Ctrl+S` and
`/sessions`), the per-role model dialog (`Ctrl+M` / `/model`), its provider
credentials dialog, the missing-model/API-key send gating, the app-slot status
extras, and the agent operating-mode, tool-access and project-root selectors
have landed;
the composition/slots are finalized with an I8 guard test, and the public
`docs/components/tui.rst` and `system/tui-testing.md` document the frontend.

## Overview

The TUI is a full-screen Textual application launched **in-process** by a new
`tui` subcommand (unlike the NiceGUI web client, which runs as a subprocess).
It connects to a running `klea-agent-serve` / `klea-rag-serve` over HTTP/SSE
and renders **one active session at a time**, with a summonable picker to
switch or resume.  It is a thin client (ADR-0026): the server owns sessions,
checkpoints and graph state; the TUI holds a hydrated projection (`ChatData`)
that it may discard and reload at any time.

Data flow:

```mermaid
flowchart LR
    subgraph TUI["Textual app (klea_utils/ui/tui)"]
        SCREEN["Screen: two-panel split\n(chat | status)"]
        CTX["TuiContext: config, refs,\ncallbacks, slots, capabilities"]
        REDUCER["shared reducer\nui/stream_events.py"]
        STATE["shared state\nui/state.py"]
        CLIENT["shared client\nui/client.py + api/sse.py"]
    end
    SERVER["klea-serve / klea-rag-serve\n(FastAPI + SSE)"]
    APP["App entry\n(klea_agent / klea_rag)"]

    APP -- "compose + slots" --> TUI
    CTX --- SCREEN
    CLIENT -- "POST /query/stream" --> SERVER
    SERVER -- "progress/inspect/state/usage/tool/\ncontext/interrupt/complete/error" --> CLIENT
    CLIENT --> REDUCER --> STATE --> SCREEN
```

## Layout

The screen is a horizontal split into exactly two full-height panels, like the
web page's chat column plus its right drawer.  The chat panel is the wider of
the two (a 3:1 split), so the status panel stays a narrow sidebar:

* **Chat panel** (left, full height): a view toggle between the transcript and
  the inspector, the turn-status line, the input, and the keybinding hints.
* **Status panel** (right, full height): the app title and server line, the
  operating-mode / tool-access selectors, the resolved models, the streamed
  state sections, and token usage.  Everything up to (and including) the token
  usage is fixed, so it is always visible; only the state sections below (the
  plan etc.) scroll.

Because the status panel spans the full height, there is no separate
full-width header or footer bar; the title sits at the top of the status panel
and the key hints at the foot of the chat panel.  The two panels are
distinguished by slightly different background colours rather than border
lines.

```
+---------------------------------------------+----------------------+
| chat panel                                  | status panel         |
|                                             |  title: <app> | <srv>|
| ------------------------------------------- |                      |
|  [ transcript | inspect ]  <- view toggle   |  mode / access       |
|  ...messages...                             |  models              |
|                                             |  state sections      |
| ------------------------------------------- |  token usage         |
|  turn status: spinner | error+Retry | ...   |                      |
|  input (> ...)                   [Send/Stop]|                      |
|  keys: Enter send | Ctrl+C stop | Ctrl+S ...|                      |
+---------------------------------------------+----------------------+
```

## Component contract

Shared widgets live in `klea_utils/ui/tui/` with no app imports (invariant
I8); each app composes its own screen from them, mirroring the NiceGUI
`components/` split (ADR-0031).

| Component | Planned module | Responsibility | Web counterpart |
|-----------|----------------|----------------|-----------------|
| App + Screen | `ui/tui/app.py` | Textual `App`, keybindings, the single run worker | `components/bootstrap.py` + `page.py` |
| TuiContext | `ui/tui/context.py` | Config, element refs, cross-component callbacks, app slots, capabilities | `components/context.py` (`PageContext`) |
| Transcript | `ui/tui/components/transcript.py` | Message widgets, MIME-aware rendering | `chat_area.py` + `chat_bubble.py` |
| Input | `ui/tui/components/input.py` | Query entry, send/stop routing, `/` completion | `input_area.py` |
| Turn status | `ui/tui/components/turn_status.py` | Live heading, error+Retry, stopped, awaiting input | chat-area turn-status region |
| Status panel | `ui/tui/components/status_panel.py` | Full-height right panel: mode/access/project-root, models, state sections, token usage | `status_pane.py` |
| Inspector view | `ui/tui/components/inspector.py` | Inspect entries; a toggle view inside the chat panel | `inspector.py` |
| Session picker | `ui/tui/components/sessions.py` | `ModalScreen` list / switch / rename / delete | `chat_list.py` |
| Model dialog | `ui/tui/components/model_dialog.py` | `ModalScreen` per-role model editor (shared core in `ui/model_config.py`) | `model_dialog.py` |
| Credentials dialog | `ui/tui/components/credentials_dialog.py` | `ModalScreen` masked provider API-key editor (shared scopes) | `model_dialog.py` (credentials) |
| HITL | `ui/tui/components/hitl.py` | Review / input / permission modals | `choice.py` + status-region form |
| Commands | `ui/tui/components/commands.py` | Merged catalogue, dispatch, `/` autocomplete | `components/commands.py` |
| Shared state | `ui/state.py` | `ChatData` store + helpers | `web/nicegui/state.py` (moved) |
| Reducer | `ui/stream_events.py` | `apply_stream_event` | `web/nicegui/components/stream.py` (extracted) |
| Client | `ui/client.py` | Sessions, models, credentials, commands (`GET`/`POST`) | `web/nicegui/client.py` (moved) |

The three shared modules are extracted in Phase 1 so the web and TUI share one
source of truth for the event contract; NiceGUI-only drivers (`run_stream`,
`stop_stream`) stay in the web package.

## Event consumption

The event catalogue and payload shapes are fixed by `system/streams.md`; the
TUI must not reinterpret them.  Per event:

| Event | TUI behaviour | Web counterpart |
|-------|---------------|-----------------|
| `progress` | Update the turn-status heading in place (no full re-render) | spinner label |
| `ping` | Ignored (keeps the SSE alive) | ignored |
| `inspect` | Append an entry to the inspector view (chat-panel toggle; `format_details` for `details`) | inspection pane |
| `state` | Update a status-panel section keyed by `data.key` or node label | status pane |
| `usage` | Accumulate token totals in the status panel | token totals |
| `tool` | Append a message widget (MIME-aware, text fallback) | chat blocks |
| `token` | Not consumed (no live typing; see streams.md) | ignored |
| `context` | Store on the active chat; reconcile the status-panel mode/access/project-root slots | status pane + `context_hooks` |
| `interrupt` | Open the HITL / permission modal; stop streaming until answered | chat ask form |
| `complete` | Append the agent message; set turn status idle | chat bubble |
| `error` | Turn status error; offer Retry when `resumable` | notification |

## Run model and invariants

One run is in flight at a time and is always attached to the active session
(ADR-0050, I1/I2).  A query starts the run worker; the worker drives
`stream_events`, feeds the shared reducer, and posts a message per event so
widgets refresh on the app loop.  Stop cancels the worker and best-effort
`POST /query/cancel`.  A failure `resume` re-runs with `resume=True`; a HITL
answer/cancel re-runs with `interrupt_response` / `interrupt_cancel` (I6).
Switching sessions is refused while a run is streaming.

A query is also blocked (with a warning pointing at `Ctrl+M`) while a
**required** model or provider API key is unresolved
(`missing_required_roles` / `missing_credentials`); client commands still run,
so `/help` and the dialogs work before setup is complete.

The ADR-0050 invariants are the fitness function; this note adds the concrete
enforcement point for each:

| Invariant | Enforced by |
|-----------|-------------|
| I1 one run, attached | A single app-level run slot; the input worker is exclusive |
| I2 switch gated by run | The picker checks the run slot and refuses with a "Stop first" hint |
| I3 one reducer | The TUI imports `ui/stream_events.py`; no local event mapping |
| I4 thin client | Imports only `api/sse.py` + `ui/client.py`; rehydrate on load |
| I5 client-local identity | `resolve_user_id()` seam; local UUID sent only to loopback |
| I6 HITL is a pause | Answer/cancel goes through the same run, one action per request |
| I7 text fallback | The transcript renders `display` whenever a MIME is unrenderable |
| I8 app-agnostic shared code | No app import under `ui/tui/`; app controls via slots |

## Session model

* A keyed `{user_id}:{chat_id}` store with a single **active pointer**; the
  active session's messages and context are hydrated on create/switch/load,
  the picker lists server metadata (`GET /chat/{user_id}`), including each
  chat's first user query as a truncated preview.  The preview follows the
  highlighted chat and is shown below the Rename/Delete/Cancel buttons, so it
  does not crowd the session rows.
* Create mints a chat id (coolname slug) and `POST`s it; `--continue` resumes
  the most recently updated session and `--resume <chat_id>` loads one.
* `user_id` resolves through one seam (`klea_utils/ui/identity.py`):
  `--user-id` > `KLEA_USER_ID` > a persisted host-local UUID, the last only for
  a loopback server (I5).  The identity key is the server app
  (`klea-agent` / `klea-rag`), shared with the web UI, so a local session
  opened in either frontend appears in both (ADR-0052).

## Commands (ADR-0047)

The TUI reuses `klea_utils/commands/common.py`: it validates `/`-input against
the merge of its client commands and the server catalogue (`GET /commands`),
runs client commands locally (`/help`, `/new`, `/sessions`, `/model`,
`/exit`; later `/rename`, `/export`), and forwards server commands (`/mode`,
`/access`) as the query.  The `/`-autocomplete is a Textual completion/
suggestion list over the merged catalogue.  TUI capabilities start empty (no
browser file picker); local-filesystem/shell capabilities are deferred with
the session-workspace work and are not needed for parity.

`/exit` (the command form of `Ctrl+Q`) stops any active run -- cancelling the
worker and sending the best-effort `POST /query/cancel` -- and then quits, so
it is safe mid-stream.  `/new` switches to a fresh, empty chat; like session
switching, it is refused while a run is streaming.

## HITL and permissions

HITL asks render inline as panels in the chat panel (`HitlPanel` for review /
input, `PermissionPanel` for path permissions, ADR-0007), not as modal screens:
the ask is mounted below the transcript and replaces the composer (the input
row is hidden while it is active), so the transcript stays scrollable.  A
review ask offers approve / request changes / cancel, an input ask one field
per question, and a permission ask allow-once / allow-session / deny per
requested path; the key-hints line shows the ask's actions.  Decisions are
shown in the transcript via
`klea_utils/api/hitl.py:render_interrupt_response` so live and reloaded text
cannot drift.  A pending interrupt is re-presented on load/switch from
`GET /chat/{user_id}/{chat_id}/context` (`pending_interrupt`), matching the web
(ADR-0046).

## App composition

`klea_agent/ui/tui/` composes the shared screen and registers the operating
mode and tool-access selector slots (ADR-0030/0037); `klea_rag/ui/tui/`
registers none.  Both entries are thin (`run_agent_tui` / `run_rag_tui`,
mirroring `run_agent_web`) and pass only the title/prefix/app-name and page
builder.  The registered controls are `Callable[[TuiContext], Widget | list]`
renderers in `TuiContext.status_extras`; `StatusPanel` mounts them above the
streamed state, so a state refresh never disturbs them, and rebuilds them on a
`context` change (so a control follows the checkpointed state).  The agent's
`status_ui.mode_access_status_extra` (a compact one-line ``mode | access``
display, with full access in the theme's error colour) and
`project_root_ui.project_root_status_extra` (the project root) are read-only
displays with no buttons; `mode_ui` / `access_ui` hold the mode/access
vocabulary and the helper that resolves the chat's effective value.  A control
that must run a query posts a shared `SubmitQuery` message
(`ui/tui/events.py`), which the app routes through
`submit` (commands and the setup gating still apply).

## Rendering and non-goals

* Markdown renders through Rich; diffs (`text/x-diff`, `text/x-patch`) and
  code (`text/x-<lang>`) render with syntax highlighting.  MIME selection
  mirrors the web's `chat_bubble` rules.  As in the web UI, a **user** message
  is shown verbatim (no Markdown decoration) so literal underscores, `#`,
  line breaks and pasted code are preserved; only agent/system messages render
  as Markdown.
* **Non-goals / known divergence**: no inline media (text fallback, ADR-0040);
  HTML tables/alerts degrade to plain renderables; no live token typing
  (`token` stays unconsumed, streams.md); no always-visible session sidebar.

## Testing

Textual's in-process `run_test()` pilot drives the real app against a canned
fake backend, mirroring the web UI tests (`system/web-ui-testing.md`); the
shared state and reducer have framework-agnostic unit tests.  The harness
(fake backend, fixtures, gotchas) is described in `system/tui-testing.md`.

## Pointers

* Decision: `adr/0050-textual-tui.md`.
* Events and payloads: `system/streams.md`, `system/api-sse-sequence.md`
  (ADR-0040, ADR-0013).
* Reused contracts: `api/sse.py` (SSE client), `commands/common.py`
  (ADR-0047), `api/hitl.py` (`render_interrupt_response`, ADR-0046),
  `ui/inspect_format.py` (`format_details`).
* Related: ADR-0026 (client-server), ADR-0031 (apps own UI composition),
  ADR-0043 (single active run per thread), ADR-0007 (path permissions),
  ADR-0030/0037 (mode / access).
* Code: `utils_pkg/klea_utils/ui/{state,stream_events,client}.py`,
  `utils_pkg/klea_utils/ui/tui/`, `agent_pkg/klea_agent/ui/tui/`,
  `rag_pkg/klea_rag/ui/tui/`.
* Public docs: `docs/components/tui.rst`; tests: `system/tui-testing.md`.
* Web counterparts: `ui/web/nicegui/{state.py,client.py,components/*}`.
