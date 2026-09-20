# Manual end-to-end agent tests

Opt-in smoke suite that runs the real `klea` CLI end to end (CLI -> spawned
API server -> graph -> model -> bundled tools) against a throwaway workspace.
It is for **observing behaviour**, not for CI: the default pytest run and
`scripts/run_tests.sh` never collect it (`testpaths = ["tests"]`), and each
scenario is a single `klea cli --single-query` invocation.

## Running

```bash
# from agent_pkg/, or use the wrapper from anywhere:
agent_pkg/e2e/run.sh                 # all scenarios
agent_pkg/e2e/run.sh --collect-only -q
agent_pkg/e2e/run.sh -k create_file  # one scenario (pytest -k)
agent_pkg/e2e/run.sh -m e2e_tools    # a feature group (pytest -m)
agent_pkg/e2e/run.sh -k "not missing_file"
```

`run.sh` sets the model env vars (`opencode-go:mimo-v2.5` by default) and,
if needed, `OPENAI_API_KEY` from `pass API-keys/opencode.ai`. Override any of
them by exporting them first. The suite skips itself when the CLI, a model, or
the API key is missing.

Each CLI run is capped by `KLEA_E2E_TIMEOUT` (default 600 s, exported by
`run.sh`); set it lower/higher to override the per-task `timeout` without
editing `tasks.py`.

## How it is isolated

- Each scenario runs in `$KLEA_E2E_WORKDIR/<id>` (default
  `/tmp/opencode/klea-e2e/<id>`), recreated at the start of the run.
- The CLI spawns its own server on an ephemeral port with the workspace as its
  working directory, so the bundled file tools are bounded to the scratch
  directory. The agent never sees real files, and the destructive/failure
  scenarios are safe.
- The scratch base is refused if it points at `$HOME` or the current directory.

## Selecting and deselecting

Standard pytest options work because each task is a parametrized case id:

- id: `-k <id>` (e.g. `-k create_file`)
- feature marker: `-m e2e_<tag>` (`e2e_chat`, `e2e_tools`, `e2e_reasoning`,
  `e2e_review`, `e2e_failure`, `e2e_picker`, `e2e_persistence`)
- list: `--collect-only -q`
- stop early: `-x`

## What you get

For every scenario the report prints the query, exit code, the full CLI
transcript, the files left in the workspace, and the server log directory
(`~/.local/share/klea-agent/`). The workspace persists after the run so you can
inspect it directly.

Most scenarios have no assertions; a few carry deterministic side-effect
checks (for example `create_file` must produce `hello.txt`). Scenarios whose
outcome is informational set `expect = "any"` in `tasks.py`.

## Adding a scenario

Add an `E2ETask` to `tasks.py` with an `id`, `query` and `tags`, plus an
optional `setup` (seed files) and `check` (assert side effects). Use a new tag
only after registering its `e2e_<tag>` marker in `agent_pkg/pyproject.toml`.
