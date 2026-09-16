---
status: "accepted"
date: 2026-09-16
decision-makers: Ankur Sinha
consulted: "opencode sources, Claude Code tool reference, aider edit formats and benchmarks, Cline system prompt"
informed: ""
---

# File editing tools: whole-file write and exact search/replace

## Context and Problem Statement

The general agent path (ADR-0035) can inspect the workspace through the
bundled tools (`read_file`, `list_files`, `grep`, `find_files`,
`run_command`) but cannot modify files.  A coding agent needs to create and
edit files.  ADR-0034 fixes the emission mechanism for every tool: arguments
ride the prompt-injected, structured ``ToolCallsSchema``, and edits are never
parsed from free-text model responses.  Within that constraint the open
question is *which editing tools to offer and how they represent changes*:
whole-file rewrite, exact search/replace, and/or a patch format.  The
supporting research note ``devdocs/system/edit-format-strategy.md`` recorded
an initial strategy; this ADR promotes it to a decision and records the
accompanying policies (read-before-edit, atomicity, error recovery) that the
note left open.

Question: what file-modification tool surface should the bundled tools
server expose, and with what safety and recovery behaviour?

Scope: the tools' representation and safety, not the permission model
(ADR-0007/ADR-0037) or the agent's control flow (ADR-0035).

## Decision Drivers

* Reliability across the models Klea runs, including small ones (CI uses
  ``qwen3:0.6b``): the format must be one models already emit well.
* Token and latency cost: editing one line must not require resending a
  whole large file.
* Safety: a wrong edit silently corrupts a file, so wrong-region edits must
  be unlikely and visible.
* Single emission mechanism (ADR-0034): no second, free-text edit format.
* Provider portability: no dependency on provider-native tool calling.
* Access control: editing is destructive and must be full-mode only
  (ADR-0037) and path-bounded (ADR-0007).
* Implementation cost: no new runtime dependency; deterministic and
  testable behaviour.

## Considered Options

* A. Whole-file write only (``write``).
* B. Exact search/replace only (``edit``).
* C. Whole-file write plus exact search/replace (``write_file``,
  ``edit_file``) -- chosen.
* D. Patch format (``apply_patch``, unified diff).

Two sub-decisions are also recorded:

* Read-before-edit: require a prior read of the file, or not.
* Replacer recovery: exact-only, a small deterministic set, or fuzzy
  matching.

## Decision Outcome

Chosen option: **C**, because whole-file write is the simplest and most
reliable format and the only way to create files, while exact search/replace
is token-efficient for small changes in existing or large files.  Offering
both with model-guided selection follows Cline and both reference agents and
avoids the trade-off of either single-tool design.  A patch format (D) is
deferred: it is a second, harder representation (hunk headers, line counts,
context), measured worse in aider's benchmarks, and would fork the
single-emission model.

Both tools come from the bundled server
(``klea_utils.mcp.server.bundled_tools``):

* ``write_file(path, content)`` -- create or overwrite a whole file.
* ``edit_file(path, old_string, new_string, replace_all=False)`` -- replace
  an exact span of an existing file.

### edit_file matching and recovery

``edit_file`` reads and rewrites the file in one call and enforces three
checks (mirroring Claude Code's Edit), in this order:

1. match: ``old_string`` must occur in the file's current content;
2. uniqueness: it must occur exactly once, unless ``replace_all`` is set;
3. proportional match: the matched span must not be much larger than
   ``old_string`` (guard against a loose replacer selecting a large region).

Further details:
* ``old_string`` may not be empty, and ``old_string`` must differ from
  ``new_string``.
* When exact matching fails, a bounded replacer chain is tried in order:
  line-trimmed, block-anchor (first/last-line anchors plus
  :class:`difflib.SequenceMatcher` middle-line similarity), whitespace-normalised, indentation-flexible, and
  context-aware.  Every candidate must still satisfy the uniqueness and
  proportional-match checks, and the replacer that matched is logged.
* Line endings (CRLF/LF) are normalised for matching and re-applied on
  write; a UTF-8 BOM is preserved.

### Other policy decisions

* **No tool-level read-before-edit gate.** Unlike Claude Code, ``edit_file``
  does not refuse to run when the file was not read in the session; the MCP
  tools are stateless, and the exact-match plus uniqueness checks already
  reject the dangerous case (a stale ``old_string`` applied to different
  content).  Requiring a prior read would also block legitimate edits to a
  file produced earlier in the same run.  Claude Code's read-before-edit is
  itself a tool-level gate backed by conversation-scoped read tracking, not a
  read forced by its loop.
* **Reading is intrinsic to planning, not a gate.** To construct a valid
  ``old_string`` the model must know the file's current content, so the plan
  is expected to include an inspect step (``read_file`` with
  ``line_numbers=false``, or ``grep``) before an ``edit_file`` step, using
  that step's output as the observation to copy from.  If the model skips it
  or works from stale text, the edit fails the match check with a non-halting
  error and the plan is replanned.  No planner-prompt rule is added; the tool
  docstrings and the exact-match backstop carry it, and a recurring failure
  would be handled with a prompt nudge then.
* **Deferred: read tracking, staleness hashing, and interactive edit
  approval.** These belong to the permission work (ADR-0007 roadmap).  When
  added, the natural mechanism for Klea is a graph-level gate rather than a
  tool-level one: the run already holds per-session state, and
  ``ToolsCallerNode`` observes every dispatched call and result, so it can
  record inspected files and reject an edit to an unread or stale path.
  ``ArtefactSchema`` (``agent_pkg/klea_agent/schemas.py``; an id/type/content/
  metadata record with an mtime field, already in the state but not yet
  written to) is a plausible home for that record.
* **Atomic writes.** Content is written to a temporary file in the target
  directory and ``os.replace``d into place; the target's mode is preserved.
  A failed or rejected edit leaves the file untouched.
* **``read_file`` gains a ``line_numbers`` option.** The default numbered
  output (``1: line``) would leak prefixes into ``old_string``; the option
  lets the model obtain raw text to copy.
* **Annotations and access.** Both tools are ``destructive=true``, declare
  ``checkpaths=["path"]``, carry tags ``{bundled, local, files}``, and are
  therefore full-mode only (ADR-0037); the RAG (fixed ``read_only``) never
  sees them.
* **Binary and size guards.** Editing refuses non-regular files, binary
  files (NUL byte), and files above the shared size cap.

### Deferred (not planned now)

* ``apply_patch``-style multi-file atomic patches, and multi-edit in a
  single call.
* Read/staleness tracking and interactive edit approval, via the graph-level
  gate described above (inspection records in ``ArtefactSchema``, checked by
  ``ToolsCallerNode``).
* Per-model edit-format auto-selection.
* Post-edit LSP diagnostics and auto-formatting (opencode ships both).

### Consequences

* Good, because the two formats are model-familiar and need no new emission
  path, so ADR-0034 holds.
* Good, because small changes do not resend whole files, and ``write_file``
  provides the create-file capability that search/replace cannot.
* Good, because deterministic replacers recover from whitespace/indentation
  drift without a dependency, and atomic writes plus EOL/BOM preservation
  avoid corruption and spurious whole-file diffs.
* Good, because every fuzzy match is logged and bounded by the uniqueness and
  proportional-match checks.
* Bad, because whole-file write is expensive for large files and can clobber
  unrelated changes; mitigated by docstring guidance to prefer ``edit_file``.
* Bad, because fuzzy replacers can still match the wrong region; this risk is
  accepted deliberately and bounded, not eliminated.
* Bad, because with no read/staleness guard an external change between the
  read and the write is not detected beyond the exact-match requirement;
  acceptable while the agent is the single writer, revisitable.
* Neutral, because the replacer similarity thresholds are heuristics that may
  need tuning once evaluation data exists.

### Confirmation

* Code: ``klea_utils/mcp/tool_impls/file_ops.py``,
  ``write_file.py``, ``edit_file.py``, ``edit_replacers.py``; the wrappers in
  ``klea_utils/mcp/server/bundled_tools.py``; the ``read_file``
  ``line_numbers`` option.
* Tests: ``test_file_ops.py``, ``test_write_file.py``, ``test_edit_file.py``,
  ``test_edit_replacers.py``, plus ``test_bundled_server.py`` for the
  annotations/checkpaths contract, and ``test_grep.py``-style dispatch tests
  for the ``read_file`` option.
* Fitness function: file modification is reachable only through these tools,
  which take structured arguments; no free-text edit parsing exists anywhere
  (grep for a second edit path in review).

## Pros and Cons of the Options

### A. Whole-file write only

* Good, because it is the simplest format to implement and the most reliable
  for a model to produce; the model writes code as it would in an answer.
* Good, because it creates new files, which search/replace cannot.
* Bad, because every edit resends the whole file, wasting tokens and latency
  on large files.
* Bad, because an overwrite can silently discard unrelated content if the
  model's copy is stale or truncated.

### B. Exact search/replace only

* Good, because only changed regions are emitted, so it is cheap and fast for
  targeted changes.
* Bad, because it cannot create files.
* Bad, because exact matching is brittle for small models (whitespace,
  indentation, escaping), which is the weak spot the replacer chain exists to
  mitigate.
* Bad, because a non-unique match risks editing the wrong occurrence.

### C. Whole-file write plus exact search/replace (chosen)

* Good, because it combines create/whole-file simplicity with cheap targeted
  edits, and lets the model choose per change.
* Good, because it matches the production reference designs (opencode,
  Claude Code, Cline).
* Good, because each format's weakness is the other's strength.
* Bad, because two tools must be documented, tested, and selected between;
  mitigated by docstring guidance.
* Neutral, because model-guided selection (rather than hard per-model rules)
  leaves some selection quality to the model.

### D. Patch format (apply_patch, unified diff)

* Good, because a patch can carry multi-file, multi-hunk changes atomically.
* Good, because it emits only changed regions, like search/replace.
* Bad, because the model must produce correct hunk headers, line counts, and
  context, which is a harder target; aider's benchmark found diff formats
  less reliable than whole or search/replace.
* Bad, because it is a second edit representation to parse, validate, and
  recover from, at odds with the single-emission effort behind ADR-0034.
* Neutral, because opencode does ship ``apply_patch`` for GPT-family models;
  if evaluation shows a need, it can be added as an additional tool later
  without disturbing this decision.

## Pros and Cons of the Sub-Decisions

### Read-before-edit: no tool gate, read intrinsic to planning (chosen)

* Good, because it does not block legitimate edits to a file the model
  produced earlier in the run, or content it already has in context.
* Good, because the exact-match plus uniqueness checks turn a skipped read or
  a stale ``old_string`` into a recoverable, non-halting error, so the plan
  can be replanned with an inspect step.
* Neutral, because the plan contains a read/inspect step in practice anyway:
  the model cannot construct a valid ``old_string`` without the current
  content.
* Bad, because an external change between the read and the write is not
  detected beyond the match check; bounded by the single-writer assumption.

### Read-before-edit: tool-level gate enforced (deferred)

* Good, because it guarantees the model edited content it actually saw; this
  is the Claude Code behaviour, where the Edit tool refuses until the file has
  been read in the conversation.
* Bad, because Klea's tools are stateless MCP tools in a separate process, so
  a read record kept there is process-global and would conflate concurrent
  sessions; per-run graph state is the better home.
* Bad, because it would block legitimate unread-file edits (content known
  from a prior write, or already present as an observation).

### Replacer recovery: small set plus fuzzy anchors (chosen)

* Good, because it deterministically fixes the common failure modes
  (indentation, whitespace, line endings) and adds two bounded fuzzy matchers
  for mid-block drift.
* Bad, because the fuzzy matchers can select the wrong region; bounded by
  uniqueness and the proportional-match guard.

### Replacer recovery: exact-only or full chain

* Exact-only is predictable but fails on trivial whitespace differences.
* The full opencode chain adds escape-normalised and trimmed-boundary
  matchers (cheap but niche); deferred until evaluation shows a need.

## More Information

* Related decisions: ADR-0007 (path permissions), ADR-0034 (tool-calling
  mechanism), ADR-0035 (general agent path), ADR-0037 (tool access levels),
  ADR-0038 (run_command).
* Evidence and detailed discussion: ``devdocs/system/edit-format-strategy.md``
  (aider benchmarks and edit formats, Cline and opencode specifics).
* References: opencode ``tool/edit.ts`` (replacer chain),
  ``tool/write.ts``, ``tool/apply_patch.ts``; Claude Code Edit/Write tool
  reference; aider edit formats and benchmarks; Cline system prompt
  fundamentals.
