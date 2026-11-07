# Running the evaluation harness

Status: implemented.  This note is the operational companion to
`system/eval-harness-implementation.md` (the plan and decision table) and to
`system/agent-evaluation-harness.md` (the rationale).  It covers only how to
run `klea-eval` against a live Klea server: prerequisites, the case suite
format, the run procedure, how to read the stored results and how to
troubleshoot a failed run.  The format itself is ADR-0056, isolation
ADR-0057, metrics ADR-0058, storage ADR-0059 and orchestration ADR-0060.

## 1. Prerequisites

* A Python environment with the package installed (`uv pip install -e
eval_pkg`), so the `klea-eval` console script (pointed at
`klea_eval.ui.cli:app` by `setup.cfg`) is on `PATH`.
* A running server that exposes the SSE chat endpoint.  The harness drives
  the same `/query/stream` route the web and TUI clients consume (ADR-0026):

  | Server | Entry point | Default address |
  | --- | --- | --- |
  | Agent | `klea-serve` | `127.0.0.1:8006` |
  | RAG | `klea-rag-serve` | `127.0.0.1:8005` |

  Both register only `/query`, `/query/stream` and `/query/cancel`
  (`agent_pkg/klea_agent/api/chat.py`, `rag_pkg/klea_rag/api/chat.py`).
* The base URL is passed with `--base-url` / `-u`, or read from
  `$KLEA_EVAL_BASE_URL`.  There is no default: `run` exits with code 2 and
  `error: no base URL configured; ...` when neither is supplied.
* The endpoint path is passed with `--endpoint`.  It defaults to
  `/query/stream` -- the route above, which is also
  `klea_eval.runners.http.DEFAULT_ENDPOINT`.  Pass the flag for any other
  surface.

```console
klea-serve                       # 127.0.0.1:8006, the harness target
klea-rag-serve                   # 127.0.0.1:8005
export KLEA_EVAL_BASE_URL=http://127.0.0.1:8006
```

## 2. The case suite format (ADR-0056)

A suite is a JSONL file: one JSON object per line, one case per line.  The
fields the loader relies on:

* `case_id` -- required; identifies the case in the run index, in the stored
  artefacts and in the per-case `pass`/`fail` line the CLI prints.
* `schema_version` -- required; pins the case layout so a later change cannot
  silently reinterpret an old file.  It is a string, and the only value the
  loader accepts is `"1.0"` (ADR-0056); a non-string or any other value is a
  validation error, not a warning.
* `turns` -- required; the ordered list of turns one case sends to the agent.
  A turn carries:
  * `turn_number` -- 1-based and matching its position in `turns` (the first
    turn is `1`); a mismatch is a validation error.
  * `role` -- forwarded verbatim as the request `role`.
  * the message text in `content` -- never in `input`.  `content` is the only
    key the loader keeps for a turn body, so a turn that sets `input` instead
    is not a schema error: `validate` accepts it (`1 cases valid`), the turn
    loads with an empty `content`, and it only fails at run time with
    `turn <n> carries no input text`.
* `rubrics` -- optional; per-case or per-turn grading criteria layered on top
  of task success.  See ADR-0056 for the exact keys.

Optional per-case `fixtures` are resolved relative to the directory of the
JSONL file that owns the case (decision D4), so a suite can carry its own
fixture tree.

Example (`suites/smoke.jsonl`):

```json
{"case_id": "smoke-001", "schema_version": "1.0", "turns": [{"turn_number": 1, "role": "user", "content": "Summarise the NeuroML repository layout."}]}
```

## 3. Validate before running

`validate` loads the suite without touching the network.  It prints
`<source>: <n> cases valid`, or the first fault as
`<source>:<line>: <message>` (ADR-0056) and exits 2:

```console
klea-eval validate suites/smoke.jsonl
```

Run this first: it is the cheapest way to catch a bad `turn_number` or an
unknown `schema_version`, both of which would otherwise fail mid-suite.  It
does *not* catch a misplaced `input` key: `validate` accepts such a turn and
still prints `... cases valid`, and the fault only surfaces at run time as
`turn <n> carries no input text` (see section 8).

## 4. Run a suite

```console
klea-eval run suites/smoke.jsonl \
    --base-url http://127.0.0.1:8006 \
    --workspace runs/smoke \
    --harness-seed 0 \
    --max-attempts 3
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--base-url`, `-u` | `$KLEA_EVAL_BASE_URL` | Server base URL; required. |
| `--endpoint` | `/query/stream` | Path appended to `--base-url`. |
| `--workspace`, `-w` | `.` | Directory the run artefacts are written into. |
| `--timeout` | `60.0` | Per-request timeout, in seconds. |
| `--harness-seed`, `-s` | `0` | Seed for the deterministic harness. |
| `--max-attempts` | `3` | Attempts per case before it is recorded as failed. |
| `--overwrite` | off | Replace an existing run's `run_id` instead of failing. |

`run` executes the suite end to end (P6); configuration and validation
faults are reported on stderr and exit 2.  Otherwise each case prints one
line, `<case_id>: pass` or `<case_id>: fail`, and the command exits with the
suite's `exit_code`.

### The `run_id` is deterministic, so a rerun skips unless forced

A case's `run_id` is derived from its identity and the harness seed, not from
the wall clock (ADR-0059; the P4 success criterion is a `run_id` that stays
stable across reruns).  Consequences:

* Rerunning the same suite with the same `--harness-seed` targets the same
  `run_id` and the same stored record.
* Without `--overwrite` that is not an error: the run already exists and is
  complete, so the case is logged as `already complete, skipping it` and
  recorded as `skipped` with the stored metrics; its directory is left
  untouched.
* With `--overwrite` the existing run directory is cleared and the case is
  republished under that `run_id`.
* To keep both runs instead, change the seed (`--harness-seed`) or use a fresh
  `--workspace`.

This is also why the inspection commands (`runs`, `show`) can be relied on to
read back exactly the record the run wrote: the identity is stable, so a
`show <run_id>` after a rerun-with-`--overwrite` is the same slot, not a
duplicate.

## 5. What one turn sends, and what must come back

The runner (`klea_eval.runners.http`) posts one `POST <base-url>/<endpoint>`
per turn and buffers the SSE response:

* an event whose type is `complete` supplies the turn text from
  `message_for_user`;
* `usage` events are summed into the token counters;
* `tool` events are collected, and an entry flagged `is_error` (directly or
  under `meta`) increments `tool_error_count`;
* `ping` events are dropped;
* an `error` event, an `interrupt` event, or a stream that ends without a
  `complete` event is a hard failure for that turn -- no partial text is
  scored.

That contract is why `--endpoint` points at a Klea server and not at an
OpenAI-style `/v1/chat/completions`: such a stream ends with `data: [DONE]`
and carries no `complete` event, so it cannot satisfy the parser.

## 6. Where the results go

* Artefacts are written under `--workspace`; the default location is
  `$KLEA_EVAL_WORKSPACE`, otherwise `~/.local/share/klea-eval` (D3).  Keep it
  outside the source tree so runs do not pollute the repository.
* Each case runs in its own directory, removed on success and on failure
  (ADR-0057).  What survives a successful (or skipped) run is the stored
  result record (ADR-0059), which is what the inspection commands read back.
  A *failed* run leaves no stored record: its directory is discarded, `runs`
  lists nothing for it, `show <run_id>` exits 2, and `prune` reports
  `kept 0 runs`.

## 7. Inspecting a run

```console
klea-eval runs                       # the run index, one line per run
klea-eval runs --case-id smoke-001 --status completed
klea-eval show <run_id>              # <run_id>: <case_id> <status> pass|fail
klea-eval summary --k 3              # suite_score and pass_at_k
klea-eval prune --keep 20            # ADR-0059 retention
klea-eval report results.json -o report.md
```

`show`, `runs`, `prune` and `summary` accept `--workspace` / `-w` and fall
back to `$KLEA_EVAL_WORKSPACE`.

## 8. Troubleshooting

Work down this list when a run does not behave as expected.

* **`error: no base URL configured; ...` (exit 2).**  Neither `--base-url`
  nor `$KLEA_EVAL_BASE_URL` was set.  Export the variable or pass `-u`.
* **Connection refused / `endpoint request failed`.**  No server is
  listening on the base URL.  Start `klea-serve` (8006) or `klea-rag-serve`
  (8005) and confirm the scheme, host and port match `--base-url`; a server
  on 8005 answers the agent suite only if it is the RAG app, and vice versa.
* **`endpoint answered 404` / wrong-endpoint failures.**  The path appended to
  the base URL does not exist on that server.  The Klea servers only serve
  `/query/stream`; a leftover `--endpoint /v1/chat/completions` (or other
  OpenAI-style path) will 404.  Drop the flag to get the default.
* **`stream ended without a complete event` / `endpoint reported an error`.**
  The server answered but the turn failed inside the graph, or the target is
  not the Klea SSE contract at all.  Check the server log; re-query the same
  case through the TUI or web client to see the underlying error event.
* **`endpoint timed out after 60.0s`.**  The CLI `--timeout` (60 s) is
  shorter than the runner default (`DEFAULT_TIMEOUT_SECONDS`, 300 s) and long
  agent paths exceed it.  Raise `--timeout` for such suites.
* **A validation fault from `validate` / `run` (exit 2).**  The message is
  `<source>:<line>: <message>`.  Common causes: `schema_version` other than
  `"1.0"`, or a `turn_number` that does not match the turn's 1-based position.
* **`turn <n> carries no input text` at run time.**  The turn puts its message
  in `input` instead of `content`.  `validate` does not flag this (the turn
  loads with an empty `content`); the fault only surfaces when the run builds
  the request, so move the text to `content`.
* **A rerun reports `skipped` (or you want to force it).**  The deterministic
  `run_id` matched a complete stored run, so the case was skipped and its
  stored result kept.  Pass `--overwrite` to clear and republish that run
  directory, or change `--harness-seed` / `--workspace` to keep both runs.
  See section 4.

## 9. Known divergences

* `--endpoint` used to default to `/v1/chat/completions`, which neither Klea
  server serves and the runner cannot parse.  It now defaults to
  `/query/stream`, matching both servers and
  `klea_eval.runners.http.DEFAULT_ENDPOINT`.  Third-party targets are still
  reachable with an explicit `--endpoint`.
* `run`'s `--timeout` default is 60 s, while
  `klea_eval.runners.http.DEFAULT_TIMEOUT_SECONDS` is 300 s.  The CLI value
  wins; raise `--timeout` for suites whose turns run long agent paths.  This
  mismatch is recorded here but not changed by the endpoint fix.
