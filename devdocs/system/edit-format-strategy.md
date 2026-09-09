# Edit-format strategy for coding tools

Status: strategy note, not an ADR.  Input to the Stage 1 bundled-tool
design (``shell``/``edit``/``write``/``glob``/``grep``).  Promotable to
an ADR later if it grows to govern more than tool design (e.g. a
per-model format auto-selection mechanism).

## Context

The agent edits files through tool signatures: ADR-0034 fixes prompt
injection + structured emission, so edits are emitted as
``ToolCallsSchema`` arguments and are never parsed from free response
text.  Within that constraint the open question was which *tool
shapes* to offer for file modification: whole-file rewrite,
search/replace, or both.

Evidence base: aider's edit formats and benchmarks (whole, diff
SEARCH/REPLACE, udiff; plain-text formats beat function-calling for
edits; format choice is empirically model-dependent -- aider selects
per model family), Cline's production tool set (``write_to_file`` +
``replace_in_file``, model-guided), opencode's ``edit`` implementation
(uniqueness enforcement plus a chain of fallback replacers).

## The two tools

* ``write`` (whole-file): the model returns the complete updated file.
  Most reliable format -- simplest cognitive load, the model writes
  code as it would in a chat answer.  Cost: the whole file crosses the
  wire even for one-line changes, so it is expensive and slow for
  large files.
* ``edit`` (search/replace): ``filePath``, ``oldString``,
  ``newString``, ``replaceAll``.  Token-efficient -- only changed
  regions are emitted.  Failure modes: non-unique matches and model
  eliding ("# ... rest unchanged" laziness).  Mitigations: uniqueness
  enforcement unless ``replaceAll``, and fallback matchers tried in
  order (exact, line-trimmed, block-anchor with middle-line
  similarity, whitespace-normalised, indentation-flexible -- the
  opencode replacer chain) before the call is refused.

## Selection guidance

Both tools are offered; the prompt steers the model: prefer ``edit``
for existing/larger files, ``write`` for new or small files.  This is
Cline's approach and follows directly from aider's finding that the
reliable format is model-dependent -- we offer both and let the model
choose rather than hard-coding per model.

## Rejected alternatives

* Aider-style text-response edit parsing (SEARCH/REPLACE blocks in the
  free text response): would introduce a second emission mechanism
  alongside the structured one -- the dual-output-shape complexity
  already rejected for the router.  See ADR-0034.
* Single tool only: whole-file only wastes tokens and latency on large
  files; search/replace only risks the small-model weak spot below.

## Small-model considerations

The risky compound is code inside JSON string arguments (escaping plus
adherence).  Mitigations, in order: prompt guidance ("prefer write for
small files"); grammar-constrained structured output where the server
supports it (vLLM ``structured_outputs``, TGI guidance --
provider-conditional bonus per ADR-0034); the structured-output
fallback machinery on parse failure.

## Future candidates (not planned now)

* ``apply_patch``-style multi-file atomic patch tool (opencode ships
  it for GPT-family models).
* Per-model format auto-selection if evaluation shows a model
  consistently mangles one of the two formats.

## References

* ``devdocs/adr/0034-tool-calling-mechanism.md`` (governing emission
  mechanism).
* aider: https://aider.chat/docs/more/edit-formats.html,
  https://aider.chat/docs/benchmarks.html.
* Cline system prompt fundamentals (https://cline.ghost.io/system-prompt/).
* opencode: ``packages/opencode/src/tool/edit.ts`` (fallback
  replacers), ``tool/apply_patch.ts``, ``tool/write.ts``.
