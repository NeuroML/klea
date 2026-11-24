# Running the evaluation harness

Status: implemented.  This note is the operational companion to
`system/eval-harness-implementation.md` (the plan and decision table) and to
`system/agent-evaluation-harness.md` (the rationale).  It covers only how to
run `klea-eval` against a live Klea server: prerequisites, the case suite
format, the run procedure, how to read the stored results and how to
troubleshoot a failed run.  The format itself is ADR-0056, isolation
ADR-0057, metrics ADR-0058, storage ADR-0059 and orchestration ADR-0060.

Public user documentation lives in `docs/guides/run-an-evaluation.rst` (the
quickstart) and `docs/cli/klea-eval.rst` (the command reference); this note
remains the internal authoring and operations reference.

Scope: the harness exists to evaluate Klea only.  `klea_eval` is the Klea-specific evaluation harness: it exists to evaluate Klea and is not a general-purpose evaluation harness or benchmarking platform.  Every suite, fixture and check targets Klea's own agent or RAG server.

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
* Model overrides: `run` sets all three roles (`chat`, `plan` and
  `tool_picker`) with `--model` / `-m ROLE=MODEL`; the model name carries its
  provider prefix, for example `opencode:deepseek-v4.1-flash`.  The credential
  is read from the harness process environment using the `<PROVIDER>_API_KEY`
  convention, so the `opencode` provider needs `OPENCODE_API_KEY`.
* `--timeout` must be raised above its 60 s default for suites whose turns run
  long agent paths.
* `--workspace` / `-w` should point at a directory outside the repository, so
  the run artefacts do not pollute the working tree.

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
  * the message text in `content`, which is required and must be a non-empty
    string.  The loader allows exactly these turn keys: `turn_number`, `role`,
    `content`, `expected_outputs`, `tool_expectations`.  Any other key -- for
    example a turn that puts its text in `input` instead of `content` -- is a
    validation error: `validate` reports `<source>:<line>: turn <n> has
    unknown field 'input' (allowed: ...)` and exits 2.  A missing, empty or
    non-string `content` is rejected the same way.
* `rubrics` -- optional; per-case or per-turn grading criteria layered on top
  of task success.  See ADR-0056 for the exact keys.

Optional per-case `fixtures` are resolved relative to the directory of the
JSONL file that owns the case (decision D4), so a suite can carry its own
fixture tree.  Each case's turns run with the run's own `workspace/`
directory as the agent's working directory (`<run dir>/workspace/`), and a
case's execution-check `command` runs there too.  Declared fixtures are
copied into `<run dir>/workspace/fixtures`, so both the agent and the command
find them under `workspace/fixtures`.

Example (`suites/smoke.jsonl`):

```json
{"case_id": "smoke-001", "schema_version": "1.0", "turns": [{"turn_number": 1, "role": "user", "content": "Summarise the NeuroML repository layout."}]}
```

The shipped `samples/hello_world.jsonl` is the smallest runnable example of the
format plus an execution check.  One prompt turn asks the agent to write
`hello world` to `hello.txt` in the workspace, and a case-authored `command`
(an argv list, no shell string) with a 30 s `command_timeout` is graded on exit
status alone.  The command reads `hello.txt` from the run's `workspace/`, which
is its working directory, and exits 0 only when the file holds exactly that
text.  Because the command's output is discarded, the graded result has to be
a file the check itself can read: there is no `expected_outputs` entry, the
`command_exit` verdict carries the decision.  Against an empty workspace the
same case fails cleanly (the missing file makes the command exit non-zero), so
it passes only when the artefact was really produced.
```

## 3. Validate before running

`validate` loads the suite without touching the network.  It prints
`<source>: <n> cases valid`, or the first fault as
`<source>:<line>: <message>` (ADR-0056) and exits 2:

```console
klea-eval validate suites/smoke.jsonl
```

Run this first: it is the cheapest way to catch a bad `turn_number`, an
unknown `schema_version` or a misplaced `input` key, all of which would
otherwise fail mid-suite.  A turn that carries `input` instead of a non-empty
`content` is rejected here with `turn <n> has unknown field 'input'
(allowed: ...)` and exit 2.

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
| `--model`, `-m` | none | Override a model role, `ROLE=MODEL`; repeatable. |

`run` executes the suite end to end (P6); configuration and validation
faults are reported on stderr and exit 2.  Otherwise each case prints one
line, `<case_id>: pass` or `<case_id>: fail`, and the command exits with the
suite's `exit_code`:

* `0`: every case passed.
* `1`: a case failed its rubrics.
* `2`: a configuration or validation fault, raised before any case ran.
* `4`: a completed case declared rubrics but recorded no grade
  (`EXIT_GRADE_MISSING`), a different outcome from the `1` of a real case
  failure.

A real case failure outranks a grade-missing case, so a run that has both
exits `1`.

The pipeline reads a case's stored metrics before the run envelope is
appended, so the exit-code decision could previously see a case that had
recorded a failing grade as grade-missing and exit `4`.  `_run_one_case` now
re-reads the stored run (`metrics.load_run`) after both writes, so the
returned `CaseRun` agrees with the run on disk: a recorded failing grade
exits `1`, and `4` stays reserved for a case that declared an expectation and
recorded no grade at all.  The contract above was already correct; the code
now matches it.

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

## 5. Per-run model selection

`run` can point the target app's model roles at other models for one run: pass
`--model` / `-m` once per role, as `ROLE=MODEL`.  A case's own
`run_config.model_overrides` still applies, and its value wins over the flag
for the same role:

```console
klea-eval run suites/smoke.jsonl \
    --base-url http://127.0.0.1:8006 \
    --workspace runs/smoke \
    -m chat=openai:gpt-4o \
    -m eval=openai:gpt-4o-mini
```

The flag is repeatable; a malformed pair (no `=MODEL`, an empty role or an
empty model) is a configuration fault and exits 2.  The overrides are applied
once per run, after its directory is prepared and before its first turn.

### Roles are discovered, never hardcoded

The roles the flag accepts are whatever the target app exposes in its
`models/active` map (`chat`, `plan`, `execute`, `eval`, ... depending on the
deployment), named generically rather than fixed by the harness.  The harness reads that map once, before the first case runs, and rejects a
role it does not find, so a typo aborts the whole run with exit code 2 and
names the unknown role and the roles the backend exposes (`unknown role
'<role>' (the backend exposes: ...)`); no run directory or index line is
produced, instead of silently leaving the suite on the wrong models.

Evaluation scoring itself is deterministic and has no judge role (ADR-0061):
grades come from machine checks alone, so no `judge` override is needed.

### Credentials come from the environment

An override needs a key for the provider of its model, taken from the
environment of the harness process:

| Provider | Environment variable |
| --- | --- |
| `openai` | `OPENAI_API_KEY` |
| `anthropic` | `ANTHROPIC_API_KEY` |
| `mistralai` | `MISTRAL_API_KEY` |
| `huggingface` | `HF_TOKEN` |
| `ollama` | none: a local runtime is keyless |

Most other providers follow the same convention, `<PROVIDER>_API_KEY` (upper
case, for example `OPENROUTER_API_KEY` for `openrouter:`).  One secret is
pushed per provider, even when several roles share it, and only to the run's
own user scope in the backend's credential store: it is never printed, echoed
in an error or recorded in the run's artefacts.  A provider with no key in the
environment is skipped, and the run fails if the backend then reports that the
role still requires a credential.

### A locked role cannot be overridden from the harness

Some deployments lock a role in their configuration.  Overriding one answers
403 with `The '<role>' model is locked`, and the case fails before its first
turn.  The lock is a server-side setting: change it in the app's environment
or config, restart the server, and rerun.

### The resolved map is recorded

The map the backend reports after the overrides are applied is stored as
`resolved_models` in the run's `run.json` and in the run-level envelope record
of its `metrics.jsonl`, so the stored run (and any comparison of runs) reports
which model each role actually used, not only which one was requested.

## 6. What one turn sends, and what must come back

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

## 7. Where the results go

* Artefacts are written under `--workspace`; the default location is
  `$KLEA_EVAL_WORKSPACE`, otherwise `~/.local/share/klea-eval` (D3).  Keep it
  outside the source tree so runs do not pollute the repository.
* Each case runs in its own directory, removed on success and on failure
  (ADR-0057).  What survives a successful (or skipped) run is the stored
  result record (ADR-0059), which is what the inspection commands read back.
  A *failed* run leaves no stored record: its directory is discarded, `runs`
  lists nothing for it, `show <run_id>` exits 2, and `prune` reports
  `kept 0 runs`.

## 8. Inspecting a run

```console
klea-eval runs                       # the run index, one line per run
klea-eval runs --case-id smoke-001 --status completed
klea-eval show <run_id>              # <run_id>: <case_id> <status> pass|fail
klea-eval summary --k 3              # suite_score and pass_at_k
klea-eval prune --keep 20            # archive beyond the window (ADR-0059)
klea-eval prune --keep 20 --purge    # delete instead of archiving
klea-eval prune --keep 20 --dry-run  # report the plan, touch nothing
klea-eval report results.json -o report.md
```

`show`, `runs`, `prune` and `summary` accept `--workspace` / `-w` and fall
back to `$KLEA_EVAL_WORKSPACE`.

`prune` archives by default: a run beyond the per-case window loses only its
`workspace/` tree, while its `run.json`, `metrics.jsonl`, `COMPLETE` marker and
index line survive and an `ARCHIVED` marker is written beside them.  An
archived run stays listed by `runs`, stays readable by `show` and still counts
towards `summary`.  Pass `--purge` to delete the whole run directory and drop
its index line instead, or `--dry-run` to report what would happen without
touching the disk.

`report` reads the results index and, additionally, every published run
directory under `runs/` that carries no index line: each such directory is
listed as its own `<run_id>: <case_id> <status> ... index=missing` line and
counted in the report's `unindexed` summary counter.  This is where a failed
run from an earlier invocation shows up, because a failed run never appends an
index line.  A complete-but-unindexed directory is marked `drift`, a complete
and archived one `archived`, and any other residue (a directory without the
`COMPLETE` marker) is labelled from its readable `run.json`, or as `case=?`,
`status=incomplete` and `label=unknown` when that cannot be read.  Staging and
sentinel directories (`.tmp`, `.superseded`, `.overwrite` and hidden names) are
ignored.  The index itself stays completed-only: the report adds no line to it.

## 9. Troubleshooting

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
  `"1.0"`, a `turn_number` that does not match the turn's 1-based position, or
  a turn with an unknown key such as `input` (`turn <n> has unknown field
  'input' (allowed: ...)`); move the text to `content`.
* **`turn <n> carries no input text` at run time.**  `validate` already rejects
  a turn that carries an unknown `input` key, so this should be unreachable
  from a suite file; the runner keeps the check as defence in depth for a
  payload built programmatically.  The fix is the same: move the text to a
  non-empty `content`.
* **A rerun reports `skipped` (or you want to force it).**  The deterministic
  `run_id` matched a complete stored run, so the case was skipped and its
  stored result kept.  Pass `--overwrite` to clear and republish that run
  directory, or change `--harness-seed` / `--workspace` to keep both runs.
  See section 4.
* **`unknown role '<role>' (the backend exposes: ...)`.**  A `--model` role
  (or a case `run_config.model_overrides` key) is not one the target app
  exposes, so the harness aborted the whole run before the first case, with
  exit code 2 and no run directory or index line produced.  The per-case
  failure that publishes nothing is now only a fallback for when this
  pre-flight check is skipped.  Pass a role the app lists in its
  `models/active` map (section 5).
* **A 403 carrying `The '<role>' model is locked`.**  The deployment locks that
  role, so the override was refused and the case failed before its first turn.
  Clear the lock in the app's environment or config, restart the server and
  rerun (section 5).

## 10. Known divergences

* `--endpoint` used to default to `/v1/chat/completions`, which neither Klea
  server serves and the runner cannot parse.  It now defaults to
  `/query/stream`, matching both servers and
  `klea_eval.runners.http.DEFAULT_ENDPOINT`.  Third-party targets are still
  reachable with an explicit `--endpoint`.
* `run`'s `--timeout` default is 60 s, while
  `klea_eval.runners.http.DEFAULT_TIMEOUT_SECONDS` is 300 s.  The CLI value
  wins; raise `--timeout` for suites whose turns run long agent paths.  This
  mismatch is recorded here but not changed by the endpoint fix.
