---
status: "accepted"
date: 2026-10-28
decision-makers: [Architecture Team]
---

# ADR-0059: Result Storage

## Context and Problem Statement

The harness produces, for every test case run, a set of results that must survive the process and remain inspectable long after execution:

- **Run metadata**: the run identity (`run_id`), the originating `case_id`, timestamps, the resolved model configuration and seed parameters needed for reproducibility.
- **Metrics**: the `RunMetrics` record emitted by the centralized `RunMetricsCollector` (tool calls, latency, token usage, rubric scores and pass/fail outcomes), stamped with the schema version it was written against.
- **Artifacts**: the evidence behind the metrics  ---  the conversation transcript, tool-call payloads, and the final state of the isolated workspace (or a diff of it).

Adjacent ADRs already constrain this problem without solving it. ADR-0057 (Run Manager & Isolation) fixes the on-disk identity of a run as its UUID v5 `run_id` inside an isolated per-run workspace and states that persistence is the last lifecycle step of a run; its decision drivers reject external infrastructure and demand that isolation scale to very large suites within disk-space limits. ADR-0058 (Metrics Collection & Aggregation) names `metrics.jsonl` as the persistence target and defines the versioned `RunMetrics` schema as the single source of truth. ADR-0056 (Test Case Format) makes `case_id` the join key back to the input case and establishes JSONL as the project's serialization convention.

What remains undecided is how those pieces are laid out on disk: which files a run directory contains, how results are found and aggregated without scanning every directory, how schema versions are recorded, how a run is written so that a reader never observes a half-written result, and what happens when disk space runs out.

Without a declared storage layout, each consumer (reporting, comparison, debugging) will invent its own traversal convention, partial runs will be indistinguishable from complete ones, and metrics written under different schema versions will be silently mixed.

## Decision Drivers

* **Durability and completeness**: a stored run must be provably complete; a crashed run must be distinguishable from a finished one rather than appearing as a truncated-but-valid result.
* **Aggregation cost**: summarising thousands or millions of runs must not require opening every run directory; a cheap index is needed for suite-level reporting.
* **Isolation and locality**: all data for one run must live under that run's workspace (ADR-0057), so deletion, copying and parallel execution are per-directory operations.
* **Schema evolution**: every persisted record must carry the version of the schema it was written under (ADR-0058), so old results stay interpretable after the schema changes.
* **Simplicity of operation**: only standard filesystem operations and JSON libraries may be required  ---  no databases, servers or services (ADR-0057 drivers).
* **Debuggability**: results must be human-readable with ordinary tools (`cat`, `jq`, an editor) and comparable across runs by diffing files.
* **Disk-space discipline**: storage must scale to large suites; artifact retention must be a policy the user can tune, not an accident.
* **Concurrency safety**: parallel runs must be able to record results without corrupting shared structures.

## Considered Options

1. **Directory-per-run on the filesystem, plus a top-level append-only JSONL index.**
2. **A single monolithic JSONL results log per suite**, one line per metric record, with artifacts stored separately or dropped.
3. **An embedded SQLite database**, one file with tables for runs, metrics and artifacts.
4. **An external database or object store** (for example PostgreSQL or S3) as the system of record.

## Decision Outcome

**Chosen option: "Directory-per-run on the filesystem, plus a top-level append-only JSONL index"**, because it is the only option that satisfies all decision drivers simultaneously: it reuses the per-run workspace identity already fixed by ADR-0057, keeps every run self-contained and deletable as a directory, reuses the JSONL conventions of ADR-0056 and the `metrics.jsonl` target of ADR-0058, and adds no infrastructure beyond the filesystem while still offering a cheap aggregation path through a single index file.

### Storage layout

Each completed run is stored under its isolated workspace as `runs/<run_id>/`, containing:

```
runs/<run_id>/
├-- run.json           # envelope: run_id, case_id, case_version, timestamps, status,
|                      # resolved models, seed/model parameters, schema_version
├-- metrics.jsonl      # one RunMetrics record per line, as defined by ADR-0058
├-- transcript.jsonl   # conversation turns and tool-call records
├-- artifacts/         # workspace snapshot or diff, plus any produced files
└-- COMPLETE           # zero-byte marker written last; presence means the run is final
```

A single suite-level index, `runs/index.jsonl`, holds exactly one line per completed run: `run_id`, `case_id`, `status`, `timestamp`, `schema_version`, and the directory-relative path to `metrics.jsonl`. The index is the aggregation entry point; report generation reads the index first and only opens individual run directories when per-record detail is requested.

### Write protocol

Results are written at the end of the run (ADR-0057 lifecycle) into a temporary directory `runs/<run_id>.tmp/`, every file is flushed and `fsync`ed, the `COMPLETE` marker is written last, and the directory is then renamed to `runs/<run_id>/` in one atomic operation. The index line is appended with a single `O_APPEND` write after the rename. A reader therefore sees either no run or a complete run; a leftover `.tmp` directory is unambiguously an interrupted run.

### Schema versioning

The `schema_version` field appears both in `run.json` and in every `metrics.jsonl` record, taken from the ADR-0058 `RunMetrics` schema. Readers must reject or explicitly upcast records whose version they do not understand; they must never silently reinterpret them.

### Retention

Artifact retention is configurable (default: keep full artifacts for the most recent N runs per case and store only metrics plus `run.json` for older runs). A purge operation deletes whole run directories and rewrites the index atomically; it never edits a run directory in place.

### Consequences

Good, because runs are self-contained directories  ---  copying a run for debugging, deleting a run to reclaim space, and running cases in parallel are all per-directory operations with no shared mutable state beyond an append-only index.

Good, because aggregation over large suites reads a single index file, and the index format is the same JSONL convention already used for test cases and metrics.

Good, because results are plain text and inspectable with ordinary tools, with no daemon to run and no schema migration service to operate.

Bad, because the index and the run directories are two places that must agree; an index line can survive a deleted directory or be missing after a crash between rename and append. A startup consistency check (or a rebuild-index command) is required.

Bad, because an append-only JSONL index degrades in query capability: any non-trivial query (group by case, percentile latency) requires a full scan and in-memory aggregation, which is acceptable at this scale but not a general query engine.

Bad, because per-run directory inodes and small files multiply at millions of cases; very large suites may need sharded run roots, which this ADR does not yet prescribe.

## Pros and Cons of the Options

### Option 1: Directory-per-run + append-only JSONL index (chosen)

* Good, because it aligns exactly with the `runs/<run_id>/` layout fixed by ADR-0057 and the `metrics.jsonl` target fixed by ADR-0058  ---  no redefinition of neighbouring interfaces.
* Good, because a run is a unit of atomicity, deletion and copying.
* Good, because it requires nothing beyond the standard filesystem and JSON libraries.
* Good, because the index gives O(1) suite-level listing without opening run directories.
* Neutral, because atomic completion relies on the rename-at-completion pattern, which the filesystem supports but which the harness must implement correctly.
* Bad, because index and directories can drift, requiring a consistency check.
* Bad, because inode count and small-file overhead grow linearly with the number of runs.

### Option 2: Single monolithic JSONL results log per suite

* Good, because it is the simplest possible layout: one file, one append per record, trivial to tail and ship.
* Good, because aggregation over metrics is a single sequential scan.
* Bad, because artifacts do not fit in a line; transcripts and workspace snapshots must be stored elsewhere, reintroducing the layout problem this ADR exists to solve.
* Bad, because concurrency requires external locking to keep lines intact, and a corrupted line contaminates the whole suite's record.
* Bad, because deleting one run's data means rewriting the entire log, which does not scale.
* Bad, because it violates ADR-0057's per-run isolation: results for one case are interleaved with all others.

### Option 3: Embedded SQLite database

* Good, because it offers real queries and indexes (group by case, filter by status, percentiles) without scanning.
* Good, because transactions give atomicity and crash safety for free.
* Good, because artifacts can be referenced by foreign key from a single metadata store.
* Bad, because it violates ADR-0057's "standard filesystem operations and JSON libraries suffice" driver and adds a dependency whose binary format is not human-readable without tooling.
* Bad, because concurrent writers from parallel runs hit locking and busy-retry behaviour that is harder to reason about than atomic renames.
* Bad, because it breaks the project's JSONL convention (ADR-0056) for results and complicates diffing and version-controlling of stored results.

### Option 4: External database or object store as system of record

* Good, because it scales furthest: managed storage, concurrent access, and server-side aggregation.
* Good, because it can centralize results across many machines and CI runs.
* Bad, because it directly contradicts ADR-0057's decision driver that no external infrastructure (databases, message queues) may be required.
* Bad, because it makes the harness unusable offline and adds credential and availability failure modes to every run.
* Bad, because local debugging and reproduction would depend on remote state, undermining the reproducibility goal.

## Related Decisions

- **ADR-0056 (Test Case Format)**: Supplies `case_id` as the join key stored in `run.json` and the index, and establishes the JSONL conventions this ADR reuses for `metrics.jsonl` and `index.jsonl`.
- **ADR-0057 (Run Manager & Isolation)**: Fixes `run_id` (UUID v5) as the directory and primary key, places storage inside the isolated per-run workspace, and defines persistence as the final run lifecycle step; this ADR specifies what that final step writes.
- **ADR-0058 (Metrics Collection & Aggregation)**: Defines the versioned `RunMetrics` schema and the collector that produces it; this ADR persists its output verbatim and stamps the schema version alongside every record rather than redefining any metric field.

Harness orchestration, the CLI surface and run scheduling are out of scope here and belong to a separate, later decision.

## References

- [JSONL Format Specification](https://jsonlines.org/)
- [MADR: Markdown Any Decision Records](https://adr.github.io/madr/)
- [POSIX `rename` atomicity](https://pubs.opengroup.org/onlinepubs/9699919799/functions/rename.html)
