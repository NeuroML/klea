# `/run`: user-invoked shell command (design notes)

Status: **deferred / under consideration.**  Not implemented; `/run` is a
registered stub (`implemented=False`).  These notes record the design
discussion so it can be resumed.  Decision context: ADR-0047 (session
commands), ADR-0038 (`run_command`).

## What it is

`/run <command>` runs a shell command on the server host (via the shared
`run_command` impl) and shows the output.  The user invokes it directly -- the
model does not choose to run it -- and the execution itself needs no LLM call.
The open question is whether the output should also enter the model's context.

Note the general distinction (see `session-commands.md`): commands are
**user-invoked**, never model-chosen; that says nothing about whether a
command uses an LLM.  `/run` happens to need no model call, but a workflow
command such as `/compact` or `/init` does.

## Why it is attractive

- Token saving: the normal path (ask the agent -> tool picker -> tool caller
  -> answer) costs several LLM calls; `/run` executes with none.
- Determinism and speed: the user knows exactly what runs.

## The core question: does the output enter the model's context?

Two coherent designs:

- **A. Terminal semantics (show-only).**  `/run` is the user's shell: no
  model involvement, output shown as a shell block, not added to the model
  context.
  The "agent should act on a command" case goes through the normal tool path
  (the LLM picks `run_command`; the pipeline shapes and consumes the result).
  - Pros: clean actor separation; no prompt bloat; no prompt-injection
    surface; no reinvention of result handling.
  - Cons: "run `!git diff`, then ask about it" leaves the agent blind.

- **B. Add to the conversation.**  The output enters the model's context so a
  later turn can use it (opencode's `!cmd` behavior).
  - Naive B (opencode): inject as a **tool result**.  Rejected: it frames a
    user action as the assistant's own tool call -- wrong provenance.

## Provenance is the crux

A user-run command and an LLM-invoked tool call are different events; the
model must not think it ran the user's command.  So the output must be
represented with **user provenance**, never as a `CallToolResult` (which is
agent-owned; `textualize_tool_results` renders `**Tool call succeeded:**`).

**B' (chosen design, not yet implemented):**

- `/run` executes without a model call; the user sees the output.
- The output is added to the conversation as a **user-originated** turn
  (`HumanMessage`), labeled so agency is unambiguous, for example:

  ```
  The user ran a shell command and shared the output:
  ```console
  $ <cmd>
  <bounded output>
  ```
  ```

- Single combined user turn: replace the trailing `/run ...` query message
  (appended by `InitGraphState`), not two consecutive user turns -- the raw
  `/run ...` syntax is not useful to the model.
- `persists="message"`; access-gated (`read_only` refuses, since
  `run_command` is full-only and destructive); output bounded.

## General principle this surfaces

Every piece of context needs a **producer tag** -- user, agent, or system:

- agent tool calls -> `CallToolResult` (agent-owned),
- project context -> `Discovery` (system/project-owned),
- conversation -> `messages` (user/assistant roles).

A user-run command belongs in the user channel; user-provided attachments
(`/file`, `/upload`) likewise should be labeled as user-provided wherever
they are rendered.

## Formatter

Keep the tool-result formatting features (code fence, truncation) but not the
tool-result framing.  Decision: a **dedicated** helper
`textualize_user_command(command, output, *, max_len=None)` in
`utils_pkg/klea_utils/tools.py` (a user command carries a command string and
provenance that a `CallToolResult` does not).  Rejected: a `provenance=` flag
on `textualize_tool_results` (overloads that function's contract and misuses
the MCP type).

## Display

Reuse the **tool shell block** pattern (mime `text/x-shell`), consistent with
the existing chat bubbles' "what corresponds to what" role separation:

- `GraphCommandResult` gains a `display` field (like tool calls); the command
  node emits a `tool` stream event
  (`{"type": "tool", "data": {"tools": [entry]}}`, the same shape as
  `ToolsCallerNode`).
- Header labels it as a user command (for example `$ <cmd>`).

## Framework prerequisite

`/run` needs an **async** command handler (`run_command` is async).  The
framework currently types handlers as sync (`GraphCommandHandler`).  Needed:

- `GraphCommandHandler` accepts `GraphCommandResult | Awaitable[...]`,
- `CommandNode.execute` awaits an awaitable result.

## Security notes

- `run_command` is **not a sandbox** (ADR-0038): the command can ignore the
  working directory and has the host user's authority.  `/run` is
  user-initiated (trusted input), but under B' its output is untrusted text
  entering the model context (prompt-injection surface).
- Full-access only (`read_only` refuses).

## Open questions

- A vs B' (whether the output should enter the model context at all).
- If B': `messages` (`HumanMessage`) vs a dedicated session-scoped "user
  context" channel.
- A structured provenance marker (a field) for future nodes/UI, or
  role + label only.
- `project_root` for `run_command`: the graph process cwd (matches the tool)
  for now.

## Pointers

- ADR-0047 (session-command framework), ADR-0038 (`run_command`), ADR-0040
  (stream events).
- `utils_pkg/klea_utils/mcp/tool_impls/run_command.py`,
  `utils_pkg/klea_utils/tools.py` (`textualize_tool_results`),
  `utils_pkg/klea_utils/nodes/command.py`,
  `agent_pkg/klea_agent/commands.py`.
