# File tools: contract and edge-case matrix

Status: implemented.  Covers the Klea-authored (bundled) filesystem tools and
the primitives they share.  Companion to `mcp-permissions.md` (the path
boundary) and `docs/developer-guides/writing-mcp-tools.rst` (tool authoring).

## Scope

The bundled filesystem tools -- `read_file`, `write_file`, `edit_file`,
`list_files`, `find_files`, `grep`, `run_command`, `download_file`,
`sqlite_query` -- share two primitives:

* `klea_utils/mcp/tool_impls/file_ops.py` -- text decoding/encoding, binary
  detection, BOM and newline helpers, atomic writes.
* The **client-side path gate** (`klea_utils/mcp/dispatch.py` +
  `path_detect.py`), not the tools: the tools no longer check the boundary
  themselves (ADR-0007 update 2026-10-08).  `permission.py` retains the
  boundary helpers used by that gate.

The bundled web tools that do not touch the filesystem (`web_fetch`,
`search_papers`) are out of scope here; their network safety is covered in
`mcp-permissions.md`.

The bundled store-search tool (`search_stores`,
`klea_utils/mcp/tool_impls/stores.py`) is also out of scope: it is a retrieval
tool, not a filesystem tool.  It reads the deployment's configured vector and
BM25 stores through their own URIs (Chroma folders, Qdrant/PGVector URLs,
pickled BM25 corpora) and takes no path argument, so it is **not path-gated** by
the client-side project-root gate and declares no `checkpaths`; its only inputs
are a query, a domain, a mode and tuning knobs.  Its classification is covered
in `mcp-permissions.md`.

Recording the contract here keeps the definition in one place (the tools were
originally written without an enumerated edge-case contract, which let
`read_file` misreport truncated reads).

## Shared text contract

* **Encoding**: UTF-8, strict.  Invalid UTF-8 is refused by `read_file` /
  `edit_file` / `write_file` (no replacement-character garbage).  `grep`
  decodes leniently (`errors="replace"`) -- it is a search tool and matches
  ripgrep's byte-oriented behaviour; this is the one deliberate asymmetry.
* **BOM**: a leading UTF-8 BOM is stripped on read (`file_ops.split_bom`) and
  preserved on write.
* **Binary**: a NUL byte marks a file as binary (`file_ops.is_binary`);
  `read_file` refuses it, `grep` skips it, and `edit_file`/`write_file` refuse
  it via `file_ops.read_whole_text`.
* **Lines**: `file_ops.split_lines` -- lines end at `\n` after normalising
  CRLF and lone CR; a single trailing newline is not an extra line; form feeds,
  vertical tabs, `\x1c`-`\x1e` and Unicode separators are **not** line breaks.
  This matches editors and `wc -l`.  `read_file` and `grep` use it.
* **Write fidelity**: the dominant newline convention and file mode are
  detected and preserved; writes are atomic (`os.replace`).

## `read_file` paging contract

* `offset` (1-indexed line), `limit` (lines; default 500),
  `char_offset` (0-indexed character within the start line, default 0).
  A negative `offset` counts from the end (`-1` is the last line), see
  "Reading from the end" below.  `limit=None` removes only the line cap; the
  server character budget still applies, so a `None` read can still be
  truncated with `next_offset`.
* The character budget is **server-owned** (`_MAX_CHARS`, ~20k), not an input
  to the tool, so a large file is read by paging rather than in one call.  The
  effective value is reported as `char_budget`.
* A page normally ends on a line boundary.  A single line longer than the
  budget is paged by characters.
* A truncated read is a **success** (`error` empty) carrying `truncated`,
  `truncation_reason`, `next_offset`, `next_char_offset` and a `note` telling
  the caller how to continue.  `truncation_reason` is `char_budget`,
  `line_limit` or `scan_cap` when truncated, and empty otherwise.  It is not an
  MCP error.
* Error results (`isError: true`): missing / not-a-file (with `nearby` +
  `note`), binary, invalid UTF-8, and unreadable (a symlink loop reports as
  not-a-file).  Path-permission denials come from the client-side gate before
  the tool runs, not from `read_file`.
* Plain text (and raw `.csv`/`.tsv`) is read **lazily**: the pager consumes a
  line iterator from a chunked reader (incremental UTF-8 decoding, newline
  normalisation across chunk boundaries), so memory is O(page) rather than
  O(file).  Conversion formats (HTML/anydoc) produce the whole converted text
  (cached) and are paged from it.
* `max_bytes` (default 100 MB) is a per-call **scan cap**: the reader stops
  after reading/decoding that many bytes in one call.  It is **not** a
  file-size gate -- files larger than `max_bytes` are paged like any other,
  and a page that cannot be completed within the cap is returned truncated
  with a note.
* `total_lines` is exact when the read reaches the relevant end: EOF for a
  forward read, or the file start for a tail read.  A streamed page that stops
  early does not scan the rest of the file, so the field is `null`.
* A **large-file truncation** carries a `note` recommending `grep` to locate
  the region and then reading it with `offset`/`limit`; a forward page also
  suggests a negative offset for reading the end directly.

### Reading from the end (tail)

* A negative `offset` addresses lines from EOF: `offset=-1` is the last line,
  `offset=-N` the Nth from the end.  `limit` then counts forward from that
  start; `None` runs to EOF.
* Tail reads use **negative line numbers** in `line_start`, `line_end` and
  `next_offset` (`-1` is the last line).  A page that reaches EOF is
  `truncated: false` with `next_offset: null` and `line_end: -1`.
* Plain text is located by a chunked **backward scan** (`_locate_tail_start`):
  LF and lone-CR terminators are counted (CRLF is one), one trailing line
  ending is ignored so it does not add an empty line, and a terminator split
  across chunk boundaries is handled.  I/O is O(tail bytes) and memory is
  O(chunk); the page is then read forward from the located byte offset.
* The backward locator and the forward page read are each bounded by
  `max_bytes`.  If the locator cannot reach the requested line within the cap,
  the result is an empty, truncated page (`truncation_reason: scan_cap`) with a
  note; use grep or a smaller tail.
* When the backward scan reaches byte 0 it reports the exact `total_lines`.
  A partial tail does not scan to the start, so `total_lines` stays `null`.
  A tail offset beyond the start clamps to the first line (reported as
  `-total_lines`) and carries a note.
* HTML/anydoc pages are already in memory, so they are sliced from the end
  directly and always report the exact `total_lines`.
* A tail read validates only the returned tail region.  Binary content or
  invalid UTF-8 earlier in the file is not seen unless the tail reaches it.
* An empty file has no tail line: the result is empty, `total_lines: 0`, with
  a "past the start" note.

## `grep` context contract

* `context_lines` (default 2, range `0..7`) controls how many lines are shown
  before and after each match.  Out-of-range values are clamped (the MCP tool
  schema rejects them during validation).
* Results are grouped per file into hunks:
  `{path, start_line, end_line, lines}` where each line is
  `{line_number, line, match}`.  Line numbers are explicit, so a hunk can be
  read directly or used to derive a `read_file` offset.
* Windows that overlap or touch are merged into one hunk; shared lines are
  emitted once.  Windows are clamped at the start and end of the file.
* `max_results` counts **matching** lines only; context does not consume the
  cap.  When the cap is reached mid-hunk, the hunk ends at the last returned
  match and trailing context is dropped, so a match beyond the cap is never
  mislabelled as `match: false`.
* `max_chars` (server-owned, 100k) counts both match and context text.  A line
  that does not fit is truncated and `end_line` reflects the last emitted
  line; the result is `truncated: true`.
* Reaching the match cap sets `truncated: true`.  The in-house backend stops
  scanning when it fills the cap, so an exact-cap result may also report
  `truncated: true` because it did not prove there were no further matches.
* Both backends produce the same hunk shape.  Ripgrep is invoked with
  `-C <n>` and its `context` JSON records are grouped by contiguous line
  numbers; the in-house backend merges windows over
  `file_ops.split_lines` output.  The ripgrep stdout record cap is sized for
  the match cap plus context records and file markers.
* `context_lines=0` returns only matching lines, but adjacent matches still
  merge into one contiguous hunk (`match: true` on each).
* `files_scanned` stays `null` for ripgrep and a count for the in-house
  walker; the `.gitignore` divergence documented above is unchanged.

## `find_files` / `rg_files` / `list_files` truncation contract

* `max_results` (default 100) **caps the result list**: only the first
  `max_results` entries are returned.  The payload sets `truncated: true`
  when the cap cut the listing short.  All three tools stop one entry past
  the cap, so the true total is not known and no omitted count is claimed.
* A truncated listing carries a human-readable `note` (a plain string) with
  this wording, the cap interpolated:

  `Results truncated: showing the first <max_results> entries; more exist and were not listed.`

  The wording is defined once as `TRUNCATION_NOTE` in
  `klea_utils/mcp/tool_impls/file_ops.py` and imported by `find_files`,
  `rg_files` and `list_files`.

  `note` is the empty string when the listing is complete.  As with
  `read_file`, a truncated result is a **success** (`error` empty), not an
  MCP error.
* Payload keys, per tool:

  | Tool | Keys |
  |------|------|
  | `find_files` (ripgrep backend and the `find_files_inhouse` fallback) | `files`, `truncated`, `error`, `note` |
  | `rg_files` | `files`, `truncated`, `error`, `note` |
  | `list_files` | `files`, `error`, `truncated`, `note` |

* `list_files` already used `note` for its unfiltered-fallback message (a
  requested filter that matched nothing).  When such a fallback listing is
  also cut short, the truncation sentence is **appended** after the fallback
  sentence rather than replacing it.
* Every return path has the same shape: the `rg_files` error returns (not a
  directory, spawn error, no matches, ripgrep failure) and the `find_files`
  error returns carry `note: ""`.

## Edge-case matrix

| Case | Handling |
|------|----------|
| Few short lines | Normal page. |
| Many short lines | Paged by `limit`; `next_offset`. |
| Few long lines | Line-boundary cap; a single over-long line paged by `char_offset`. |
| Many long lines | Both bounds apply; page ends on a line boundary. |
| `limit=None` with more content than the budget | Character-capped page; `truncation_reason: char_budget`; continue with `next_offset`. |
| Empty file | `total_lines: 0`, empty content. |
| `offset` past EOF | Empty content + a "past the end" `note`. |
| Negative `offset` | Tail read; `-1` is the last line, line numbers and `next_offset` are negative. |
| Tail reaches EOF | `truncated: false`, `next_offset: null`, `line_end: -1`. |
| Tail reaches byte 0 | Exact `total_lines` reported; the start is `-total_lines` (the first line). |
| Tail beyond the start | Clamped to the first line + a "before the start" `note`. |
| Tail locator hits `max_bytes` | Empty, truncated page; `truncation_reason: scan_cap`; a `note` suggests grep or a smaller tail. |
| Negative `offset`, empty file | Empty content + a "past the start (0 lines)" `note`. |
| Non-positive `limit` | Coerced to one line (never reads the whole file). |
| Binary (NUL) | Refused (error). |
| Invalid UTF-8 | Refused (error). |
| UTF-8 BOM | Stripped; reported in `note`. |
| CRLF / lone CR | Normalised to LF for line numbering. |
| Form feed / Unicode separators | Not line breaks (stay in the line). |
| Raw `.csv` / `.tsv` | Note that fields may span lines (converted when anydoc is available). |
| File > `max_bytes` | Paged; not refused.  A page needing more than the scan cap is truncated with a note. |
| Large file, page near the start | Readable; only the page's bytes are scanned. |
| Large file, page far in | Truncated + `grep` hint (Phase 1); byte-offset seek is the Phase 2 follow-up. |
| Page does not reach EOF | Forward read: `total_lines: null`.  Tail read: `line_end: -1` means EOF; `total_lines` stays null unless the scan reached byte 0. |
| File grows mid-read | Bounded by the page plus the `max_bytes` scan cap; never unbounded. |
| Symlink loop / unresolvable | Not a file (error). |
| Symlink / `..` outside root | Handled by the client-side gate (deny/ask), not the tool. |
| FIFO / device / directory | "Not a file" + `nearby`. |
| Missing file | Error + `nearby` + `note`. |

## Known limitations (deliberate)

* **Path containment is path-based and a snapshot.**  A hard link inside the
  root to an inode named outside it is allowed (undetectable path-wise); a
  path swapped for an external symlink after the check (TOCTOU) could still be
  opened.  Hardening needs a per-open check (`openat`/`O_NOFOLLOW`).
* **No transcoding.**  Only UTF-8 text is supported; UTF-16/latin-1 files are
  refused (or reported as binary).  A future option could transcode.
* **Line offsets scan from the start.**  A page is read lazily (O(page)
  memory), but reaching a line `offset` still counts newlines from byte 0, so a
  late page costs O(offset) I/O.  Byte-offset region reads (grep emits match
  offsets; `read_file` seeks) are the Phase 2 follow-up (`devdocs/backlog.md`).
* **Tail scans are linear in the tail.**  The backward locator reads up to
  `max_bytes` from EOF to find the requested line, so a very negative offset on
  a large file costs that much I/O.  A partial tail does not learn the total,
  and a last line longer than `max_bytes` cannot be located.  Tail reads
  validate only the returned region; binary content or invalid UTF-8 earlier
  in the file is not seen.
* **Truncated forward reads do not compute a total.**  The exact total is
  available when the read reaches EOF (forward) or byte 0 (tail).  A huge
  negative offset can force the latter within `max_bytes`; otherwise the agent
  should tail, grep, or use `run_command`.
* **Conversions are whole-document.**  HTML (BeautifulSoup) and anydoc convert
  the whole file before paging, so those paths cannot stream; the converted
  text is cached so re-paging does not re-convert.
* **`run_command` is not a sandbox.**  Its working directory is advisory; it
  has the host user's authority.  See `mcp-permissions.md`.
* **Third-party MCP servers** ignore Klea's gate; their boundary is whatever
  the operator sandboxes.

## Pointers

* Primitives: `klea_utils/mcp/tool_impls/file_ops.py`,
  `klea_utils/mcp/tool_impls/grep_hunks.py` (shared hunk grouping and
  character budget), `klea_utils/mcp/tool_impls/permission.py`.
* Tools: `klea_utils/mcp/tool_impls/{read_file,write_file,edit_file,list_files,find_files,grep,run_command}.py`,
  wrappers in `klea_utils/mcp/server/bundled_tools.py`.
* Permission model: `mcp-permissions.md`, ADR-0007, ADR-0037, ADR-0038,
  ADR-0039.
