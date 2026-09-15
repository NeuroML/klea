# AGENTS.md -- Klea monorepo

**NOTE:** `CLAUDE.md` is a symlink to this file (`CLAUDE.md -> AGENTS.md`).
Editing or writing to `CLAUDE.md` will overwrite `AGENTS.md` -- always edit
`AGENTS.md` directly.

**IMPORTANT: Read this entire file at the start of every session.** It contains
workflow, git conventions, session-log guidelines, and CLI patterns that must be
followed. Do not proceed until you have read every section below.

Multi-package Python project (setuptools + `setup.cfg`). Each `*_pkg/` is a
separate installable; `agent_pkg` and `rag_pkg` depend on `utils_pkg`.

## Workflow

This repo uses an incremental, review-driven workflow.  After each step of a
plan:
1. Apply the change.
2. Run the relevant verification (lint, typecheck, test, --help).
3. Stop and present the diff for review.
4. Wait for feedback before proceeding to the next step.

If the review requires changes, address them and loop back to step 2.  Only
move to the next step after explicit approval.

Verification in step 2 covers:
- Lint + format: `ruff check . --fix` and `ruff format .` (in the affected package)
- Type check: `ty` (from repo root)
- If a CLI entry point was modified: `<cli-name> --help` to confirm it starts
- If tests exist for the changed code: `pytest -v <test-path>`

### Constraints

- Only touch files directly relevant to the current micro-step (no drive-by refactoring).
- Do not modify existing tests to force a pass unless the review explicitly requests a test update.

## Git conventions

- `git add --intent-to-add <new-file>` so new files appear in `git diff`.
- Never stage/commit without explicit user approval.
- Never run destructive Git commands (`git reset --hard`, `git clean -f`, stash drops) without confirmation.
- Show `git diff --stat` first, then full diff before committing so scope is clear at a glance.
- Conventional commit messages with issue numbers when applicable.

## Packages at a glance

| Dir | Package name | CLI entry |
|-----|-------------|-----------|
| `utils_pkg/` | `klea_utils` | -- (shared lib) |
| `agent_pkg/` | `klea_agent` | `klea`, `klea-serve` |
| `rag_pkg/` | `klea_rag` | `klea-rag`, `klea-rag-serve` |
| `mcp_pkg/` | `neuroml_mcp` | `nml-mcp` |

`klea_agent` is the main application: a general-purpose research agent
(coding, workflows, analysis, hypothesis generation).  `klea_rag` is
primarily consumed by `klea_agent` (as a retrieval/RAG service).

Each has its own `AGENTS.md` with architecture details -- refer to those for
package-specific commands, node layout, and conventions.

## Commands

Use `uv` for all package operations; do not use `pip` when `uv` is
available. To detect whether an existing venv is uv-managed, look for a
`uv = <version>` key in `<venv>/pyvenv.cfg` (uv writes it on `uv venv`;
`python -m venv` does not).

```bash
# Dev install (editable, all packages)
uv pip install -r requirements-dev.txt

# Run all tests across packages (from repo root)
bash scripts/run_tests.sh        # pytest -v -n auto in each *_pkg/tests

# Single package test
# NOTE: pytest must be run from within a package directory, never from the
# repo root. Each package's pyproject.toml sets asyncio_mode="auto" (and
# mcp_pkg adds -n 1); a root-level run skips that config, so async tests and
# fixtures error out. Only bash scripts/run_tests.sh is meant to be run from
# the repo root.
cd mcp_pkg && pytest -v          # uses -n 1 (mcp tools are asyncio)
cd utils_pkg && pytest -v

# Run only tests that do NOT need an LLM (from within a package)
cd utils_pkg && pytest -m "not localonly"

# Lint + format
ruff check . --fix
ruff format .
ruff check . --select I --fix    # import sorting

# Type check
ty

# Docs build
uv pip install -r requirements-docs.txt   # installs sphinx + furo + sphinxcontrib-typer
cd docs && make html                       # builds to docs/_build/html

# NOTE: the docs build needs sphinxcontrib-typer, which is only present if
# requirements-docs.txt is installed.  Without it, `make html` falls back to
# the system sphinx (which lacks the extension) and the `.. typer::` CLI
# reference pages fail to build.

# Pre-commit (CI gate)
pre-commit run --all-files
```

## CI flow

`.github/workflows/ci.yml` (pushes/PRs to main/development/*test*/**feat*/**fix*):
`uv pip install -r ./requirements.txt` -> `ollama pull qwen3:0.6b bge-m3` ->
`bash scripts/run_tests.sh` -> `ruff check . --exit-zero`

`.github/workflows/ruff.yml`: changed-files lint on PRs.

## Config & env loading

Both `KleaAgent` and `RAG` orchestrators load configuration via:
1. An env file (`k=v` format, path from `KLEA_AGENT_ENV_FILE` / `KLEA_RAG_ENV_FILE` or default `klea_agent.env` / `rag.env`)
2. A JSON config file selected by a *profile*: `--profile <name>` loads
   `<name>.json` from the current directory or the per-app config
   directory (platformdirs, honoring `XDG_CONFIG_HOME`).  The
   `KLEA_AGENT_APP_CONFIG_FILE` / `KLEA_RAG_APP_CONFIG_FILE` env var (or
   the same key in the env file) still picks the file when `--profile` is
   not given; `--profile template` scaffolds a new config into the CWD.

`ty.toml` adds `extra-paths` for all four packages so type-checking resolves
cross-package imports.

## Testing quirks

- Tests marked `localonly` require an LLM (and, for some, docling/HF model
  downloads).  They are NOT filtered out of the suite: `scripts/run_tests.sh`
  and CI run them against the models CI pulls (ollama `qwen3:0.6b` + `bge-m3`),
  and they self-skip only when the connection to the model backend fails.  Use
  `pytest -m "not localonly"` locally for a quick run without an LLM.
- `utils_pkg/tests/test_stores_retrieval.py` reads `STORES_TEST_CONFIG` env var (default `stores-tests.json`).
- MCP tests are asyncio + single-process; do **not** run with `-n auto` (uses `addopts = -n 1` in `pyproject.toml`).
- All packages ignore `F403` and `F405` in ruff.

## Key references

Copyright format: `# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>`
(`mcp_pkg` additionally requires `#!/usr/bin/env python3` shebang on every `.py` file.)

MCP tool auto-discovery: any function ending `_tool` is registered.

MCP server support + tool description guidance (docstring-first convention,
length/style, reusable template): `docs/concepts/mcp.rst`.

`BaseLangGraph` lives at `utils_pkg/klea_utils/graph/base.py` -- shared
setup -> MCP client -> vector stores -> compile graph template method.

Vector stores use URI-style paths: `chroma:/path/to/dir`, `qdrant:http://...`,
`pgvector:postgresql://...`.

Internal development notes live in `devdocs/` (see `devdocs/README.md`):
architecture and component contracts in `devdocs/system/` (Mermaid diagrams
and data-flow notes, e.g. `system/store-create.md`), Architecture Decision
Records in `devdocs/adr/` in MADR format numbered as `NNNN-<slug>.md`
(e.g. `adr/0001-chunk-workers.md` -- see `devdocs/adr-template.md`),
plus design research and TODO context.  Keep work-in-progress notes out of
the public `docs/` site; update `docs/` and `CHANGELOG.md` only when the
work is implemented.

## Session continuity

`.agents/YYYY-MM-DD-HHMM.md` logs previous work, where HHMM is the time the
session started (24h) -- separate files per session avoid merge conflicts when
multiple sessions run the same day (see `.agents/Readme.md` for template).
Read previous logs at session start; write one at session end.

Every log must record the authoring agent **and the model in use** (e.g.
`**Authoring agent:** opencode`, `**Model:** deepseek-v4-flash`).  This keeps
model attribution accurate when comparing outcomes across sessions.

Keep logs high-level -- decisions, architecture changes, outcomes only.
Git log has the step-by-step edits. Omit routine work.

## Versioning

- Version is tracked in each package's ``setup.cfg`` (``version`` field).
- ``klea_utils`` and ``klea_rag`` are published to PyPI; ``klea_agent`` and
  ``neuroml_mcp`` are not yet published.
- Pre-1.0 (0.x.y) releases: bump minor for new features, patch for bug fixes.
- When cutting a release:
  1. ``git tag v<version>`` and ``git push --tags``
  2. The OIDC trusted publisher workflow builds and publishes to PyPI
- After a release, bump to the next dev version in ``setup.cfg``.
- ``CHANGELOG.md`` is kept at the repo root, covering all packages.
  Entries are concise (one short bullet per user-visible change, no
  implementation detail), grouped as ``Breaking changes`` (first) /
  ``Added`` / ``Changed`` / ``Fixed`` / ``Dependencies``.

## File conventions

- Use ASCII-only text in code and documentation. No unicode dashes, arrows,
  ellipsis, or emoticons.  Generated data files (e.g.
  `metadata-map.template.json`, `.klea-cache/`) are exempt: they are UTF-8
  and may legitimately contain accented characters.
- Preserve existing comments (TODOs, FIXMEs, notes, etc.) -- never remove or
  edit comments that are unrelated to the immediate change being made.
- Custom error classes: cross-package errors live in `klea_utils/errors.py`;
  subpackage-specific errors go in a `<subpackage>/errors.py` module (e.g.
  `klea_utils/mcp/errors.py`), not defined inline in tool modules.

## HTTP conventions

- Prefer httpx for all HTTP client code. aiohttp has been removed across the
  repo and must not be reintroduced.
- Use the shared session helpers in `klea_utils` (`klea_utils/api/utils.py`
  `_make_retryer_httpx`, `klea_utils/mcp/lifespan.py`) rather than rolling
  per-module retry/backoff logic.
- Shared MCP tool implementations live in `klea_utils/mcp/tool_impls/`; apps wrap
  them into FastMCP tools and pass their httpx session via the lifespan
  context (key `http_session`, see `klea_utils.mcp.lifespan`). Tool tests use
  httpx-shaped fakes implementing the `SessionLike` protocol
  (`stream`/`get`).
- Shared MCP servers live in `klea_utils/mcp/server/`: the bundled tools
  server (`bundled.py` FastMCP instance + `bundled_tools.py` wrappers) is
  auto-launched by apps as a stdio subprocess (`python -m
  klea_utils.mcp.server.bundled`) and also exposed standalone via the
  `klea-mcp` CLI.  `BaseLangGraph._bundled_server_config()` builds its
  (config-filterable via tags) stdio entry; the effect/intent signal is
  carried by standard MCP tool annotations, not tags.

## Permissions conventions

- Every tool that reads or writes the filesystem must gate its path
  arguments through `klea_utils.mcp.tool_impls.permission.check_path_access`
  with an explicit boundary (`project_root`, default cwd) and return a
  clear, non-halting error on denial.  Self-contained helpers with their
  own containment (e.g. `download_file_to_cache`) use that as the
  boundary.
- The in-tool check is author-side: it does not protect against third-party
  MCP servers.  Full discussion and options: `devdocs/system/mcp-permissions.md`.

## CLI conventions

- Heavy imports (orchestrators, vector store backends, LLM libraries) must be
  deferred inside the function body of Typer commands, not at module level.
  Otherwise `--help` forces eager import of the entire dependency chain.
- Every deferred import must have a comment explaining *why* it is lazy, so the
  pattern is self-documenting for future maintainers.

## Logging conventions

- Add a module-level `logger = logging.getLogger(__name__)` to every new
  module, and log at appropriate levels at key points (function entry, result
  summaries, warnings, errors) so failures can be traced.  Do not write code
  without logging.
- Use `f"{variable = }"` (Python 3.8+ f-string debug syntax) when logging
  variable values, one variable per line:
  ```python
  self.logger.debug(f"{current_chat = }\n{model_info = }")
  ```
- This avoids manual label strings and keeps the variable name in the log output.
- Every Klea CLI defaults to `INFO` console logging (progress on stdout,
  warnings/errors on stderr).  Expose a `--debug` flag and honor the
  `KLEA_LOG_LEVEL` env var via `klea_utils.plogging.resolve_log_level`
  (`--debug` > env var > default `INFO`); `enable_debug_logging()` sets
  the env var so `--debug` propagates to spawned child processes.
  `setup_root_logger` defaults its stderr handler to `INFO`; the
  rotating log file always captures `DEBUG`.  When adding a new CLI,
  wire its `setup_root_logger` call through `resolve_log_level` and add
  the shared `--debug` option.

## DO NOT SPECULATE OR GUESS

When the behavior of code is unclear, **do not propose speculative fixes or guess
at root causes**. Instead:

1. **Read the source** — look at the actual library code (LangGraph, LangChain,
   etc.) to understand how it works.
2. **Write a minimal test** — a 30-line script can confirm or rule out a
   hypothesis in minutes. Do this before proposing multi-file changes.
3. **Use debug logging** — add targeted log statements at key points instead of
   guessing what data looks like.
4. **Collect empirical evidence** — run the test, read the output, then reason
   from evidence.

Speculation wastes time. Every incorrect guess compounds into more guesswork.
If you cannot determine the answer from the source or a test, say so and ask.
