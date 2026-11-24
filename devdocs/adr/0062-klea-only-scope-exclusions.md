---
status: "accepted"
date: 2026-11-24
decision-makers: Ankur Sinha
---

# ADR-0062: Klea-only evaluation harness scope exclusions

## Context and Problem Statement

The evaluation harness exists to serve Klea and nothing else: it has a Klea-only remit. Three candidate features were closed as won't-implement on 2026-11-23, but that decision was recorded only in devdocs/backlog.md (lines 287-318). This ADR records the Klea-only remit and the three scope exclusions with their reasons so that the decision is durable and discoverable.

## Decision Drivers

- The operator is trusted.
- Determinism and reproducibility, as required by ADR-0061.
- The case format is the only contract, as established by ADR-0056.
- OS-level isolation is the only hard boundary, per ADR-0007 layer 3 and ADR-0038.

## Considered Options

- General harness features: build a sandbox/permission model, a general benchmark adapter contract, and repository/container binding into the harness.
- Per-suite scripts plus operator isolation: keep the harness narrow, let each suite ship its own pre-processing scripts, and let the operator isolate the run.

## Decision Outcome

Chosen option: "Per-suite scripts plus operator isolation", because it preserves the Klea-only remit and keeps the harness free of general-purpose surfaces. The harness is explicitly out of scope for the following three items:

(a) No harness sandbox or permission model. The operator isolates the run (container, bubblewrap, or chroot) and runs only trusted suites.

(b) No general benchmark adapter contract. A benchmark ships its own pre-processing scripts that emit case JSONL plus fixtures the loader already accepts.

(c) No repository or container binding. That is environment provisioning, not a correctness statement, so such suites are not used.

## Consequences

Good: there is no adapter plugin surface and no sandbox surface to maintain, test, or secure.

Accepted limits: a case command inherits the harness environment verbatim, and a detached descendant of a case command can escape subprocess.run's timeout kill. The latter stays an open backlog item.

## Related Decisions

- ADR-0056
- ADR-0057
- ADR-0058
- ADR-0059
- ADR-0060
- ADR-0061
- ADR-0007
- ADR-0038

## References

- devdocs/backlog.md:287-318
- devdocs/system/agent-evaluation-harness.md
