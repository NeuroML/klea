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
the HITL review/input, permission and state-edit panels, pending-interrupt
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
    SERVER -- "progress/inspect/state/usage/tool/notice/\ncontext/interrupt/complete/error" --> CLIENT
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
  state sections, and token usage (input, output, and cached reads when
  present).  Everything up to (and including) the token usage is fixed, so it
  is always visible; only the state sections below (the plan etc.) scroll.
  State sections persist across turns rather than being cleared: starting a
  turn drops only the transient tool-round section, so the plan and other
  state stay visible until the streamed `state` events replace them.

The panes update in place rather than re-rendering wholesale.  The status
panel reconciles its sections by key, so only a section whose content changed
is re-mounted and an unchanged one keeps its mounted widgets; the inspector
appends each new entry to the tail (a chat switch or an untrustworthy mounted
state falls back to a full rebuild), so already-mounted entries keep their
collapsed/expanded state.  Neither pane scrolls on its own: an update follows
the tail only when the reader was already at the bottom (within one line of
`max_scroll_y`), and otherwise the previous offset is restored, so a section
dropped above the viewport or a newly appended entry never yanks a
scrolled-up view.

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
|  keys: Ctrl+C copy | Ctrl+Shift+C stop      |                      |
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
| HITL | `ui/tui/components/hitl.py` + `state_edit.py` | Review / input / permission / state-edit panels | `choice.py` + status-region form |
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
| `notice` | Append a muted progress line (full content; collapsed until expanded) | collapsible transcript note |
| `token` | Not consumed (no live typing; see streams.md) | ignored |
| `context` | Store on the active chat; reconcile the status-panel mode/access/project-root slots | status pane + `context_hooks` |
| `interrupt` | Open the HITL / permission / state-edit panel; stop streaming until answered | chat ask form |
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
requested path; the key-hints line shows the ask's actions.  A permission ask
shows the full target path (middle-truncated only for pathological values),
the session approval directory when it differs, and the tool-call context.
Input asks may also carry plan-history sections (when the automated replan
budget is exhausted), rendered above the questions.  Decisions are shown in
the transcript via
`klea_utils/api/hitl.py:render_interrupt_response` so live and reloaded text
cannot drift.  A pending interrupt is re-presented on load/switch from
`GET /chat/{user_id}/{chat_id}/context` (`pending_interrupt`), matching the web
(ADR-0046).

## HITL dialogs

The review, input and permission asks share one visual grammar, so a long ask
stays readable while the transcript keeps scrolling beneath it and the panel
does not change height under the reader (ADR-0046, ADR-0007).

* **Section containers.** Every logical part of an ask -- the summary, the plan
  history, the questions, the requested paths -- is one section container with
  its own heading and a blank line before the next, not a run of loose labels.
  A section owns its rows, so the panel can be rebuilt section by section
  without reflowing the rest, and a `Rule` separates the fixed chrome (heading,
  actions) from the scrolled body.
* **Plan history is presentation, not selection.** When the automated replan
  budget is exhausted, the input ask renders the earlier plans above its
  questions as collapsibles labelled `Option 1`, `Option 2`, and so on in
  order.  The first is expanded and the rest collapsed, so the reader sees the
  current plan in place and can open an earlier one for comparison.  These
  collapsibles are history only: expanding one never chooses it, resubmits it
  or changes what the ask will submit -- the answer still comes from the
  question fields below, so a stray expand/collapse cannot alter the run.
* **Empty-feedback guard.** An action that requires feedback (request changes,
  or a question marked as needing text) never submits an empty value: it moves
  focus to the field and shows the short hint there instead of closing the
  panel or posting a blank answer, so the reader keeps their place and the
  guard reads the same in every ask.
* **Permission blocks.** A permission ask groups its requests one block per
  requested path: each block carries a numbered title (`1. <path>`, `2. ...`),
  the tool-call context beneath it, and the muted hint/coverage text -- the
  session approval directory when it differs, the allow-once / allow-session /
  deny note -- in the `notice` role, so coverage reads as guidance rather than
  as part of the path.  A `Rule` closes each block and separates it from the
  next, so a multi-path ask is scanned one request at a time and the numbering
  never drifts from the decision rows.

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

## Client context

The TUI forwards a per-run client context with every query (and retry/HITL
resume): its working directory, its full environment, and its Python
interpreter.  Defaults are `os.getcwd()`, `os.environ`, and `sys.executable`;
`--cwd`, repeatable `--env KEY=VALUE`, and `--interpreter` override them, and
`--no-client-context` disables forwarding.  The server accepts it from a
loopback peer only; a non-loopback server is skipped with a note.  The context
is runtime data: it is never persisted, and a changed context recreates that
session's bundled MCP client.  See `session-mcp-context.md` and ADR-0055.

## Rendering and non-goals

* Markdown renders through Rich; diffs (`text/x-diff`, `text/x-patch`) and
  code (`text/x-<lang>`) render with syntax highlighting.  MIME selection
  mirrors the web's `chat_bubble` rules.  As in the web UI, a **user** message
  is shown verbatim (no Markdown decoration) so literal underscores, `#`,
  line breaks and pasted code are preserved; only agent/system messages render
  as Markdown.
* **Selection/copy**: free-standing text widgets use `SelectableStatic`
  (`components/selectable.py`).  It keeps the Rich Markdown/Syntax/Group
  renderables instead of switching to Textual's native `Markdown` widget: one
  MIME-aware render path, a pure `render_message`, and no fenced-code or
  tool-header rewrite.  The subclass adds the two pieces the generic Rich path
  lacks (`render_line` cell offsets and `get_selection` extraction); see its
  class docstring for the full decision record.  In the transcript a plain
  click still expands/collapses a long message; a drag selects text and
  suppresses the toggle (`MessageBlock.on_click` checks the screen selection).
  Ctrl+C copies the selection; Ctrl+Shift+C stops a run, with F12 as the
  fallback (Ctrl+X is TextArea's cut).  Rich selections are tinted by
  `SelectableStatic` itself (Textual ignores selection styling for
  `RichVisual`), and blank lines are padded so a drag can end there.  Copying
  uses OSC 52 plus a best-effort system clipboard tool (`wl-copy`, `xclip`,
  `xsel`, `pbcopy`, `clip`) and shows a brief confirmation notification.  In a
  remote/SSH session only OSC 52 or the terminal's own selection reaches the
  local clipboard.
* **Non-goals / known divergence**: no inline media (text fallback, ADR-0040);
  HTML tables/alerts degrade to plain renderables; no live token typing
  (`token` stays unconsumed, streams.md); no always-visible session sidebar.

## Glyphs

Every status marker the TUI draws comes from one table,
`ui/tui/glyphs.py` (`GlyphSet`), so a frame never mixes Unicode and ASCII
characters and a single environment variable switches the whole interface:

| Field | Unicode | ASCII | Drawn as |
|-------|---------|-------|----------|
| `success` | `\u2714` | `[ok]` | a completed step / passing check; a clean tool block's header |
| `failure` | `\u2718` | `[x]` | a failed step; an errored tool block's header |
| `pending` | `\u22ef` | `...` | an in-flight or not-yet-run step; a muted notice line |
| `agent` | `\u25c6` | `*` | the bullet preceding an agent turn |
| `prompt` | `\u276f` | `>` | the input prompt caret |
| `separator` | `\u2502` | `|` | the vertical rule between panes / rows |
| `overflow` | `\u2026` | `~` | the truncation marker for clipped text |

Unicode is the default.  `get_glyphs(stream=sys.stdout)` picks the ASCII
table from the following checks, evaluated in this order:

1. `KLEA_TUI_ASCII` is truthy (`1`, `true`, `yes`, `on`; case-insensitive,
   surrounding whitespace ignored) -- the explicit override, so it wins over
   every heuristic below.
2. The stream is not a TTY (`isatty()` is false, or the stream has no
   `isatty`): redirected output, a pipe or a CI log capture, so ASCII.  This
   is checked before the encoding and `TERM` heuristics, because redirected
   output is the case most likely to mangle or mis-measure glyphs.
3. The stream's `encoding` is not UTF-8/UTF-16: a known non-UTF-8 locale, so
   ASCII.
4. `TERM` is `dumb` or `unknown`, so ASCII.
5. Otherwise Unicode.

The encoding step distinguishes a *known* encoding from an *unknown* one.  A
stream with no `encoding` attribute counts as unknown: it is treated as
Unicode-capable only when the stream is still attached to an interactive
terminal (step 2 already let it through), and as ASCII otherwise, since a
redirected stream that declares no encoding is the unreadable case.  Anything
ambiguous therefore degrades to ASCII, which keeps the TUI readable over a
pipe, in redirected output and CI log capture, on `TERM=dumb`, and under a
non-UTF-8 locale.  The transcript resolves the table once at component init
(`Transcript.__init__`) and passes that `GlyphSet` down to every message block,
so a whole session shares one table.

## Role tints

Colours are roles, not literals.  `ui/tui/theme.py` maps each role to a Textual
semantic color token, so a theme change re-colours the interface consistently:

| Role | Token | Meaning |
|------|-------|---------|
| `user` | `$text` | the human's own input echo: a passive, already-sent block |
| `path_approval` | `$text` | the inline prompt asking the human to approve a path |
| `agent` | `$surface` | agent turn text: the raised base layer of the screen |
| `tool_success` | `$success` | a tool call or check that completed cleanly |
| `tool_error` | `$error` | a tool call that failed or was rejected |
| `notice` | `$warning` | non-fatal warnings and advisory banners |
| `composer` | `$surface` | the input composer / prompt area |

The `user` role no longer uses `$panel` or `$accent`.  `$panel` sits too close
to the screen background, so the human's own echo was easy to miss and hard to
read; `$accent`, in the default Textual themes, belongs to the same warm family
as `$warning`, so the user block read as a notice.  `$text` is the neutral,
high-contrast foreground that every Textual theme guarantees against
`$background`/`$surface`, so the echo stays legible on its `$primary`-tinted
bubble and reads as a passive, already-sent block.  It is also the token
furthest from the warm notice hue, so `user` and `notice` are told apart by
three independent cues rather than hue alone: a neutral bold foreground
(`$text`) versus a warm advisory one (`$warning`), the tinted `$primary 15%`
bubble versus the notice's plain background, and the role label/gutter in the
transcript.  `path_approval` shares `$text` so the inline approval prompt
stands out from the surrounding content without reading as an error; its
emphasis comes from its label and interactive chrome, not from a separate hue.
`notice` stays on `$warning`, which is where advisory text semantically belongs.

`get_role_tints()` returns a defensive copy so a widget can annotate or filter
its own view without mutating the process-wide defaults.  The transcript does
not hand these tokens to Rich: it resolves each `$token` through
`app.get_css_variables()` once per rebuild and passes the resulting colours to
`render_message`, which falls back to plain bold for a role the active theme
leaves uncoloured.

## Buttons

Every button in a dialog, the HITL asks and the command surfaces is built by
one factory, `action_button` in `ui/tui/components/buttons.py`, so the same
action looks and behaves the same in every panel.  The factory takes the label
text, the id the owning panel routes back to itself, and three optional knobs:

* `variant` -- the Textual semantic variant, chosen by meaning, never by
  colour (see below).
* `flat` -- defaults to `True`, so a button renders as flat, bordered chrome
  instead of a filled block.  These buttons sit inside the chat panel, the ask
  panels and the status panel rather than in a toolbar, and a filled block at
  that size competes with the transcript for attention; the flat form keeps the
action reachable without making it the loudest thing on screen.  `flat=False`
is the exception for a lone call-to-action that should dominate its row.
* `compact` -- shrinks the padding and the label so that three buttons fit on
  one row (the review ask's approve / request changes / cancel).  Without it
the third button wraps, which changes the panel height and shifts the
transcript under the reader.

Variant is a statement about consequence, not about emphasis:

* `default` for the ordinary cases -- Cancel, dismiss, close and the neutral
  secondary action of a panel.  Cancel is never coloured: `default` says "this
  is safe and reversible", so it stays visually quiet and can be reached
  without reading the label first.
* `error` only for a destructive confirm -- the button that deletes a session,
  discards an edit or otherwise destroys state the server cannot restore.  It
  is reserved for exactly that: spending `error` on a validation failure or a
  plain "no" trains the eye to ignore the one colour that must mean danger.
* `primary` for the affirmative action that commits the panel's change
  (approve, save), the single button in a row allowed to draw the eye.

## Theme persistence

The selected theme is a sticky local preference: it survives restarts and is
shared by every TUI session started by the same user on the machine.  It is a
client-side setting only -- it is never sent to the server and carries no
session or identity data.

* **Location.** The preference lives in `tui_settings.json` inside the
  platform-specific user config directory resolved by `platformdirs`
  (`user_config_dir`), i.e. the ordinary per-OS location for user-level state
  (`~/.config/...` on Linux, `~/Library/Application Support/...` on macOS,
  `%APPDATA%\...` on Windows).  The file holds UI preferences only (today just
  the theme name), so a corrupt or hand-edited copy can never affect server
  state.
* **Load.** `load_theme_preference()` runs from the shared `App.__init__`, so
  the stored theme is applied before the first screen is composed and the TUI
  never flashes the default theme first.  It reads the settings file, checks
  the stored name against the themes the active Textual build provides, and
  sets `App.theme`; a missing file, unreadable JSON, or an unknown theme name
  leaves the default theme in place instead of raising, so a broken settings
  file cannot block start-up.
* **Save.** `watch_theme` is the reactive watcher on `App.theme`: every change
  -- from the theme selector or any other control -- rewrites the settings
  file with the new name once, so the choice is persisted immediately rather
  than at exit (a crash or `Ctrl+Q` therefore never loses it).  The write is
  best-effort and non-fatal; a failure is reported and the session continues
  with the theme it already applied.

The theme name is the only key the TUI persists; run state, sessions and
checkpoints stay on the server (I4).

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
