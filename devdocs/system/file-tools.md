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

* `offset` (1-indexed line), `limit` (lines; default 500, `None` = to the end),
  `char_offset` (0-indexed character within the start line, default 0).
* The character budget is **server-owned** (`_MAX_CHARS`, ~20k) and not exposed
  to the model, so a large file is read by paging rather than in one call.
* A page normally ends on a line boundary.  A single line longer than the
  budget is paged by characters.
* A truncated read is a **success** (`error` empty) carrying `truncated`,
  `next_offset`, `next_char_offset` and a `note` telling the caller how to
  continue.  It is not an MCP error.
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
* `total_lines` is exact only when the read reaches EOF; a streamed page that
  stops early does not scan the rest of the file, so the field is `null`.
* A **large-file truncation** carries a `note` recommending `grep` to locate
  the region and then reading it with `offset`/`limit`.

## Edge-case matrix

| Case | Handling |
|------|----------|
| Few short lines | Normal page. |
| Many short lines | Paged by `limit`; `next_offset`. |
| Few long lines | Line-boundary cap; a single over-long line paged by `char_offset`. |
| Many long lines | Both bounds apply; page ends on a line boundary. |
| Empty file | `total_lines: 0`, empty content. |
| `offset` past EOF | Empty content + a "past the end" `note`. |
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
| Page does not reach EOF | `total_lines: null` (streamed read). |
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
* **Conversions are whole-document.**  HTML (BeautifulSoup) and anydoc convert
  the whole file before paging, so those paths cannot stream; the converted
  text is cached so re-paging does not re-convert.
* **`run_command` is not a sandbox.**  Its working directory is advisory; it
  has the host user's authority.  See `mcp-permissions.md`.
* **Third-party MCP servers** ignore Klea's gate; their boundary is whatever
  the operator sandboxes.

## Pointers

* Primitives: `klea_utils/mcp/tool_impls/file_ops.py`,
  `klea_utils/mcp/tool_impls/permission.py`.
* Tools: `klea_utils/mcp/tool_impls/{read_file,write_file,edit_file,list_files,find_files,grep,run_command}.py`,
  wrappers in `klea_utils/mcp/server/bundled_tools.py`.
* Permission model: `mcp-permissions.md`, ADR-0007, ADR-0037, ADR-0038,
  ADR-0039.
