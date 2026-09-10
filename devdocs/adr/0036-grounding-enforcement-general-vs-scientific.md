---
status: "proposed"
date: 2026-09-10
decision-makers: Ankur Sinha
consulted: "literature review (devdocs/system/agent-topology-literature-review.md)"
informed: klea contributors
---

# Grounding enforcement: optional in general mode, mandatory in scientific mode

## Context and Problem Statement

ADR-0029 makes grounding in curated evidence, evidence inspection, plan gating,
independent verification and provenance architectural invariants for
correctness-critical work.  ADR-0030 scopes those invariants to Scientific mode
and defines General mode as an explicitly lower-assurance mode that may operate
without a curated knowledge source.

That left open whether the general path may use retrieval, curated sources and
search tools at all.  Conflating "may use evidence" with "must verify against
it" either over-restricts general mode (no sources at all) or collapses the
modes (every task requires verification).  The distinction must be made
explicit now that the general path is being designed (ADR-0035).

Question: what is the boundary between General and Scientific mode with respect
to grounding?

## Decision Drivers

* General usability without setup: a user must be able to use Klea for ordinary
  tasks without building a corpus.
* No silent downgrade: a scientific task without a source must not be served as
  a verified result (ADR-0030).
* Capability reuse: retrieval, web search and tools are useful to both modes;
  the difference should be enforcement, not availability.
* A structurally clean boundary so the general path cannot silently acquire
  scientific complexity.
* Assurance status must be structured and visible (ADR-0030 inv. 4;
  ADR-0032 context events).

## Considered Options

* A. General mode is model-only: no retrieval, curated sources or search.
* B. General mode may ground, but with the same mandatory enforcement as
  Scientific mode.
* C. General mode MAY ground; Scientific mode MUST ground (chosen).

## Decision Outcome

Chosen option: **C. General mode MAY ground; Scientific mode MUST ground.**

* **General mode** may invoke retrieval, web search, files, and curated store
  search as ordinary tools.  It does not require evidence inspection, plan
  gating, independent verification or provenance, and its results are labelled
  **unverified**.  Its evaluator is **operational** (did the step work / is the
  task done).
* **Scientific mode** must ground correctness-critical work in an approved
  curated source, with evidence inspection, plan approval for consequential
  execution, independent verification against success criteria, and a closed
  provenance chain (ADR-0029 inv. 1-11).  Its evaluator is **epistemic**
  (groundedness, coverage, confidence; ADR-0009).  Only results satisfying the
  invariants are labelled **verified**.

The distinction is one of **enforcement**, not capability.  The scientific path
is the general base plus an additive correctness layer; the general path
contains no mandatory retrieval/evidence/gating/verification/provenance stages.

Assurance status is structured state and must be surfaced (ADR-0030 inv. 4,
ADR-0032).  Because general mode may retrieve from the same curated stores, the
frontend and context events must make "curated evidence, unverified workflow"
legible; the assurance label, not the presence of sources, distinguishes the
modes.  In practice a user needs stores only for Scientific mode.

This ADR refines, and does not weaken, ADR-0029 and ADR-0030.

### Consequences

* Good, because general tasks can use sources and tools without being forced
  through verification.
* Good, because the boundary is structural: scientific-only stages are absent
  from the general path, so general mode cannot silently become scientific.
* Good, because it preserves the no-silent-downgrade guarantee and the meaning
  of a verified result.
* Good, because capability (retrieval) is implemented once and shared.
* Bad, because similar-looking answers can come from either mode, so the
  frontend must carry the assurance label clearly.
* Bad, because the evaluator must have two distinct contracts (operational vs
  epistemic), increasing the testing surface.

### Confirmation

* The general graph contains no mandatory
  retrieval/evidence/verification/provenance stage; the scientific graph does.
* ``KleaAgentState.mode`` and the graph-level ``context`` event carry the
  resolved mode and note (ADR-0030/0032), and the frontend renders the
  unverified/verified assurance distinction.
* The evaluation harness includes a boundary guardrail assert and
  retrieval-available general tasks that do not require grounding.

## Pros and Cons of the Options

### A. General mode is model-only

* Good, because the boundary is trivially clear.
* Bad, because it needlessly denies general tasks useful tools (web search,
  files, stores).
* Bad, because it forces a capability split and duplicated implementations.

### B. General mode grounds with mandatory enforcement

* Good, because all grounded output would be verified.
* Bad, because it collapses the modes and forces stores for every task,
  contradicting ADR-0030.

### C. General MAY, scientific MUST (chosen)

* Good, because capabilities are shared and enforcement is the boundary.
* Good, because both modes keep their stated guarantees.
* Bad, because assurance signalling and two evaluator contracts must be
  maintained.

## More Information

* Refines: ADR-0029 (correctness invariants), ADR-0030 (operating modes and
  assurance).
* Related: ADR-0035 (general topology), ADR-0032 (context events), ADR-0013
  (inspection), ADR-0008/0011/0022/0024 (RAG pipeline used as a general-mode
  tool).
* Code loci to align: ``klea_agent/schemas.py`` (``Mode``),
  ``KleaAgent.context_snapshot``, frontend mode/assurance display.
