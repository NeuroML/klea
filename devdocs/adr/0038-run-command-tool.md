---
status: "accepted"
date: 2026-09-14
decision-makers: Ankur Sinha
consulted: ""
informed: ""
---

# General shell command tool (`run_command`): full-mode only, advisory path checking

## Context and Problem Statement

The general (operational) agent path should answer questions about its own
environment and workspace (working directory, host, date) and support coding
tasks (builds, tests, scripts).  Klea's bundled tools today only fetch web
content and read/list/download files; there is no command execution, so such
requests end `unplannable` (ADR-0035), and the evaluation harness cannot run
its single-tool and multi-step coding tasks
(`devdocs/system/agent-evaluation-harness.md`).

Executing arbitrary commands is inherently powerful.  Klea's existing path
gate (ADR-0007: `checkpaths` -> `check_path_access`) confines *declared path
arguments*, but a shell command can ignore its working directory and touch
any path, process, or host the server process can reach.  The MCP
`ToolAnnotations`-driven access level (ADR-0037) can withhold the tool from
read-only runs.  What can Klea provide, and what can it honestly guarantee?

## Decision Drivers

* Capability: environment facts and coding tasks need command execution.
* Least privilege: a read-only run must never be able to execute commands
  (ADR-0037).
* Honest limits: a path argument is a convenience, not confinement; OS
  sandboxing is the only hard boundary (ADR-0007).
* Non-halting errors: failures must become `isError` results the model can
  adapt to (ADR-0003), never exceptions that abort the graph.
* Bounded: a timeout and an output cap so a hung or verbose command cannot
  stall or flood the graph.
* Consistency: per-tool timeouts (as in `web_fetch`/`download_file`),
  docstring-first descriptions, and `ToolInfo` annotations.
* Research audience: commands (workflow runs, simulations) can legitimately
  take longer than ten minutes, so the timeout ceiling must be overridable.

## Considered Options

* **Command form:** shell string (platform shell) vs argv list
  (`create_subprocess_exec`).
* **Path argument:** checked optional `working_directory` vs an advisory
  `paths: list[str]` vs no path argument.
* **Environment:** inherit the server environment vs strip a secret denylist
  vs a minimal environment.
* **Timeout ceiling:** a fixed maximum vs a configurable maximum with a
  sensible default vs no maximum.
* **Isolation:** run under an OS sandbox now vs defer the sandbox and rely on
  the access level.

## Decision Outcome

Chosen: **a shell-string command with an optional checked
`working_directory`, an inherited environment, a default timeout with a
configurable ceiling, and `destructive`/`open_world` annotations so it is
full-mode only.**  A real OS sandbox is deferred.

### Tool shape

* `command: str` -- one shell command string (pipes, `&&`, redirection), run
  with the platform shell.  Matches coding-agent usage; unconstrained by
  nature.
* `working_directory: str | None` -- optional; defaults to the project root.
  Declared under `ToolInfo.checkpaths` and checked in-tool with
  `check_path_access`, so an out-of-project working directory is denied
  (there is no interactive approval yet, ADR-0007).  This bounds the
  *starting* directory only -- it does **not** confine the command.
* `timeout_seconds: float` -- default 30 s, model-settable up to the
  effective ceiling.
* `max_output_chars: int` -- default 100 000; captured stdout/stderr are
  truncated and the result flags `truncated`.
* The command inherits the server process environment (the user's own
  environment); a command can therefore read any secret present in it.  This
  is documented rather than filtered: a denylist cannot be complete, and
  silently dropping variables would surprise more than it protects.

### Annotations and gating

`ToolInfo(tags={bundled, local, code}, checkpaths=["working_directory"],
destructive=True, open_world=True)`.  The access level (ADR-0037) therefore
excludes the tool from disclosure and rejects it at dispatch in `read_only`;
it is available only in `full`.  The bundled server is on by default for the
agent and disabled by default for the RAG (which is `read_only` anyway).

### Timeout ceiling

The default ceiling is 600 s (mirroring opencode's bash tool) and is
overridden by the process environment variable
`KLEA_RUN_COMMAND_MAX_TIMEOUT` (seconds).  Requesting a timeout above the
effective ceiling returns a clear non-halting error naming the ceiling and
the variable.  It is a process environment variable (like `KLEA_LOG_LEVEL`),
not an env-file/JSON-config field, because it is an operational bound rather
than application configuration; a config field can be added later if needed.

### Execution

* `asyncio.create_subprocess_shell(command, cwd=resolved,
  stdin=DEVNULL, stdout=PIPE, stderr=PIPE, start_new_session=True)` so the
  tool never blocks the MCP event loop, cannot read interactive input, and
  runs in its own process group.
* On timeout the process *group* is signalled (SIGTERM, then SIGKILL after a
  short grace), so children do not leak.
* The result is a dict `{command, working_directory, returncode, stdout,
  stderr, truncated, error}`.  `error` is set only when the command never
  produced a result: a timeout, a denied working directory, a spawn failure,
  or a rejected argument.  A **non-zero exit is a normal command result**
  (amended 2026-09-18): many tools signal conditions with exit codes (for
  example `diff` returns 1 when files differ, `grep` returns 1 on no match,
  a failing test returns non-zero), so `error` stays empty and the caller
  judges `returncode`/`stdout`/`stderr`.  This keeps MCP `isError` (via
  `to_result`, ADR-0003) meaning "the tool call failed", not "the command
  reported a non-zero status".

### Root guard

Every Klea-authored tool is wrapped at registration
(`klea_utils.mcp.registry.register_tools`) to refuse execution when the
server process runs as root (effective uid 0), returning the standard
non-halting error result; a startup warning is logged when a Klea server or
app runs as root.  The override is the process environment variable
`KLEA_ALLOW_ROOT_TOOLS` (truthy: `1`/`true`/`yes`/`on`).  This covers the
bundled tools and the NeuroML server (both use `register_tools`), so it is
not limited to `run_command`: as root, even the path-confined file tools can
read/write beyond what their OS permissions would otherwise allow.

This is defense-in-depth for accidental root deployments (containers default
to root), not a sandbox or a security boundary.  Third-party MCP servers do
not use `register_tools` and run with whatever privileges the operator gives
them (ADR-0007 trust model); the guard is the most Klea can do for the tools
it authors.

### Deferred: real isolation

This tool is **not** a sandbox.  The path check is advisory, the command
inherits the environment, and it has host-user filesystem/process/network
authority (`open_world`).  Running it under container / bubblewrap / chroot
(and/or generalizing the `mcp_pkg` sandbox abstraction into `klea_utils`) is
the only way to confine an untrusted command, and the interactive
allow/deny/ask approval loop (ADR-0007 deferred half) is the missing consent
step.  Both are separate follow-ups.

### Consequences

* Good, because environment and coding tasks become answerable via the task
  path instead of ending `unplannable`.
* Good, because command execution is impossible in `read_only` runs by
  construction (annotation-driven), and failures are non-halting.
* Good, because a hung or verbose command is bounded by the timeout and
  output cap, and the ceiling can be raised for long research workflows.
* Good, because Klea-authored tools refuse to run as root by default, so an
  accidental root deployment (containers) is caught rather than silently
  granting full privileges.
* Bad, because it is a powerful, unconfined capability in `full` mode; only
  OS isolation (deferred) can confine an untrusted command.
* Bad, because the inherited environment can expose secrets to a command,
  and the `working_directory` check can be bypassed (`cd /`, absolute paths).
* Bad, because the root guard only covers Klea-authored tools; third-party
  MCP servers run with their own privileges (operator's responsibility).
* Neutral, because the model can request a longer timeout within the ceiling
  (mirroring opencode's "retry with a larger timeout").

### Confirmation

* Unit tests: success, non-zero exit -> normal result (returncode set, error
  empty, output preserved), working directory denied /
  inside / default / not-a-directory, timeout kills the process group, output
  truncation, stdin closed, and the `KLEA_RUN_COMMAND_MAX_TIMEOUT` override
  (including non-finite values).
* Root guard tests (`test_mcp_privilege.py`, `test_mcp_registry.py`): helpers,
  refusal as root, override allows, signature/schema preservation, and the
  registration warning.
* Annotation/tag assertions in `test_bundled_server.py`; a `read_only`
  disclosure/dispatch exclusion test (ADR-0037).
* Lint/type: `ruff` and `ty` clean; `pytest -m "not localonly"`.

## More Information

* Related: ADR-0003 (`isError` contract), ADR-0007 (path permissions and the
  deferred interactive half), ADR-0035 (general path; `run_command` was the
  deferred env-facts tool), ADR-0037 (access levels; full-mode gating),
  `devdocs/system/mcp-permissions.md`,
  `devdocs/system/agent-evaluation-harness.md`.
* Reference implementation: opencode `packages/core/src/tool/bash.ts`
  (`DEFAULT_TIMEOUT_MS=120000`, `MAX_TIMEOUT_MS=600000`,
  `MAX_CAPTURE_BYTES=1 MiB`, `stdin: ignore`, `detached`, external-workdir
  approval, advisory command-argument path scan).
* Code loci: `utils_pkg/klea_utils/mcp/tool_impls/run_command.py`,
  `utils_pkg/klea_utils/mcp/server/bundled_tools.py`,
  `utils_pkg/klea_utils/mcp/privilege.py`,
  `utils_pkg/klea_utils/mcp/registry.py` (root guard),
  `utils_pkg/klea_utils/graph/base.py` (startup warning).
* Status: accepted 2026-09-14.
