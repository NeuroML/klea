---
status: "accepted"
date: 2026-09-02
decision-makers: Ankur Sinha
consulted: ""
informed: klea contributors
---

# Agent operating modes and assurance levels

## Context and Problem Statement

Klea is intended to support both general agentic tasks and correctness-critical scientific workflows.

A correctness-centred scientific workflow (ADR-0029) requires an approved curated knowledge source so that the agent can retrieve evidence, inspect that evidence, and subsequently perform the required planning, execution and verification workflow. However, not all tasks require this level of assurance. Requiring a curated knowledge source for every task would unnecessarily restrict general-purpose use of Klea.

Without a curated knowledge source, Klea can still provide useful general agentic capabilities, but it cannot establish evidence-grounded scientific correctness through the correctness-centred workflow. In the absence of curated information, Klea therefore has two choices: refuse to proceed (as verification cannot be established) or downgrade to a general, explicitly unverified mode.

We need to decide how the architecture distinguishes ordinary agent operation from the higher-assurance scientific workflow, and under what conditions each workflow applies.

## Decision Drivers

* Dual mandate: Klea must be usable as a general coding/research assistant and as a correctness-centred scientific workflow (ADR-0029).
* Correctness requires curated evidence: scientific verification depends on an approved curated knowledge source; without it the evidence/provenance/verification chain cannot be closed.
* General usefulness must not be blocked: ordinary coding, scaffolding and exploration tasks should not require upfront construction of a curated corpus.
* No silent downgrade: a task that requires scientific assurance must not be silently served as a plausible but unverified answer.
* Assurance must be explicit and inspectable: the result's assurance level must be carried as structured state, not inferred from a warning sentence in generated text (ADR-0013 inspection).
* Data lifecycle separation: constructing a vector store or other searchable representation of a curated corpus can be long-running (minutes to hours for large corpora) and must not be treated as a synchronous step in the agent's task plan.
* Product clarity: users should not need to switch to a different agent for tasks that do not require curated grounding.

## Considered Options

* **A. Strict scientific-only (refuse without source)** -- require an approved curated knowledge source for every task. If none exists, refuse to proceed. Rejected as the sole operating mode: it preserves correctness guarantees trivially, but blocks general-purpose use and forces users to build a curated source before any value is delivered. Retained only as the *behaviour inside* Scientific mode, not as the global policy.

* **B. Silent downgrade to general behaviour** -- when no curated source exists, proceed anyway and produce a best-effort answer, optionally appending a warning in generated text. Rejected: correctness becomes a matter of prompt compliance and warning placement; the user cannot structurally distinguish a verified scientific result from a plausible unverified one, contradicting ADR-0029's requirement that assurance be an architectural property, not a text convention.

* **C. Explicit two modes with assurance levels (chosen)** -- support two operating modes as architectural objects. *Scientific mode* is the correctness-centred workflow of ADR-0029 and requires an approved curated knowledge source. *General mode* permits operation without a source, provides normal agentic capability, but never produces a result that can be represented as a verified scientific result. Mode is selected at task entry and assurance status is carried as structured state. This preserves correctness guarantees for scientific work while keeping general work unblocked.

## Decision Outcome

Chosen option: **C. Explicit two modes with assurance levels (Scientific vs General).**

This ADR defines the boundary at which the invariants of ADR-0029 apply. It does not weaken those invariants; it states when they are enforced.

> **Scientific mode is the correctness-centred Klea workflow. General mode is an explicitly lower-assurance operating mode.**

The distinction is architectural rather than merely a UI setting: the active mode determines which workflow invariants apply and the assurance status that can be assigned to resulting artefacts.

### Scientific mode

Scientific mode is the correctness-centred operating mode defined by ADR-0029.

It requires an approved curated knowledge source and enforces the corresponding workflow, including:

* evidence retrieval and inspection;
* explicit planning;
* human approval for consequential execution where required;
* execution;
* independent verification against task success criteria; and
* provenance linking evidence, decisions, execution and verification.

Only results that satisfy the required grounding, provenance and verification conditions may be presented as verified or completed scientific results.

Scientific mode is therefore the mode in which Klea makes correctness/verification claims.

### General mode

General mode permits Klea to operate without a curated knowledge source.

It provides general agentic capabilities, including planning, tool use and execution, subject to the normal safety and human-approval mechanisms. However, without a curated knowledge source, the correctness-centred evidence and verification workflow cannot be applied.

Results produced in general mode are therefore explicitly **unverified** and must not be represented as verified scientific results.

### Mode selection

Mode may be selected explicitly by the user at session start (e.g. a CLI flag such as `klea --mode scientific` / `klea --mode general`, a profile/config setting, or an API field). When a mode is explicitly selected, Klea respects it. When no explicit selection is made, Klea determines at task entry whether the requested task requires the scientific correctness workflow and whether an approved curated knowledge source is available.

For tasks requiring scientific assurance:

* if a suitable knowledge source exists, the task enters Scientific mode;
* if no suitable knowledge source exists, Klea does not silently downgrade the task to an unverified result and does not proceed with unverified execution. It informs the user that verification cannot be established and offers two explicit paths: (a) restart/provide an approved curated knowledge source, or (b) downgrade to General mode with explicit user permission and continue as an unverified task. Without one of these, the task does not proceed.

For tasks that do not require scientific assurance, General mode may be used directly (whether selected explicitly or by default routing).

Switching within a session is one-way. Downgrading from Scientific to General mode is allowed with explicit user permission; remaining work in that session then continues under General-mode assurance (unverified). Upgrading from General to Scientific mode is not allowed within the same session; it requires a new session with an approved curated knowledge source. Providing a source mid-session does not retroactively make previously generated unverified results verified.

Knowledge-source creation is a separate, potentially long-running data-preparation workflow. Creating a vector store or other searchable representation of a curated corpus is not treated as a synchronous step in the agent's task plan.

### Architectural invariants

These hold regardless of how the LangGraph is wired. They are the mode-level invariants that complement ADR-0029's loop invariants.

1. **Scientific mode requires an approved curated knowledge source.**
2. **Scientific mode cannot bypass the correctness-centred workflow defined by the agent correctness architecture (ADR-0029).**
3. **General mode never produces a result labelled or represented as verified scientific output.**
4. **The assurance status of a result is explicit and carried as structured state, rather than inferred from the presence or absence of a warning in generated text.**
5. **Mode switching is one-way within a session: Scientific may be downgraded to General with explicit permission, but General may not be upgraded to Scientific; a scientific task requires a new session with an approved knowledge source. Providing a source mid-session does not retroactively make previously generated unverified results verified.**

### Consequences

* Good, because users can use Klea for ordinary tasks without first constructing a curated knowledge source.
* Good, because scientific workflows retain the stronger correctness guarantees defined by the correctness architecture (ADR-0029).
* Good, because the distinction between agent capability and assurance level is explicit and inspectable (structured state, not text).
* Good, because users do not need to switch to a different agent for tasks that do not require curated scientific grounding.
* Good, because knowledge-source construction remains a separate data lifecycle and can run asynchronously for large corpora.
* Good, because experimental comparisons can distinguish general agent behaviour from correctness-centred Klea behaviour (baseline vs Scientific mode).
* Bad, because the system must communicate assurance status clearly to users (inspector, status pane, API field).
* Bad, because task classification/mode selection introduces additional routing logic and an explicit approval gate when downgrading under scientific assurance.
* Bad, because the same underlying agent may operate under different workflow constraints depending on mode, increasing testing surface.
* Bad, because general-mode results may still appear plausible despite being unverified; the system therefore cannot rely on warnings alone to prevent users from treating them as authoritative.

### Confirmation

* Architectural confirmation is structural: the compiled `klea_agent` graph exhibits a mode-routing branch at task entry (scientific vs general). In Scientific mode there is no path from `TASK` to a user-visible completed result that bypasses the ADR-0029 stages (`RETRIEVE`/`INSPECTION`/`PLAN`/`HUMAN REVIEW`/`VERIFY`/provenance). This is confirmed via `graph.get_graph().draw_mermaid()` and node wiring review. In General mode that bypass is explicit and the result carries `assurance=unverified`.
* Implementation confirmation checks that the operating mode is a checkpointed field on
  ``KleaAgentState`` (``mode``: a ``Mode`` model carrying the ``requested`` mode, the
  ``resolved`` mode and an explanatory ``note``) distinct from ``messages``, included in the
  checkpoint (ADR-0023), surfaced to the frontend via the graph-level ``context`` event
  projected from state by ``context_snapshot`` (ADR-0032, not node ``NodeStreamData``), and
  never derived from generated text.  Assurance/verification state (invariants 3-4) is
  deferred to the ADR-0029 verification phase; General vs Scientific mode is distinguishable
  by ``mode`` alone.
* Lint/type/docs gates remain: `ruff check`, `ty`, and `docs: make html` render the ADR.

## Pros and Cons of the Options

### Strict scientific-only (A)

* Good, because correctness guarantees are trivially preserved (no unverified scientific claim is possible).
* Bad, because general-purpose tasks are blocked until a curated source exists, contradicting the dual-mandate driver.
* Bad, because onboarding and iterative curation are unusable (large corpora take hours to ingest).
* Bad, because users are forced to a different agent for ordinary work.

### Silent downgrade to general behaviour (B)

* Good, because no refusal and no extra routing; minimal implementation.
* Bad, because a scientific task without evidence is still served as a plausible answer, violating the no-silent-downgrade driver.
* Bad, because assurance is inferred from wording of a warning, not structured state, so it is not inspectable or enforceable.
* Bad, because it undermines the premise of ADR-0029 (correctness as an architectural property).

### Explicit two modes with assurance levels (C, chosen)

* Good, because evidence, provenance and verification are architectural guarantees in Scientific mode, not prompt hopes, while general work remains unblocked.
* Good, because assurance status is explicit structured state (invariant 4), making verified vs unverified results distinguishable by architecture, not by text.
* Good, because knowledge-source construction can be asynchronous and off the critical path.
* Good, because it enables controlled experiments (General vs Scientific) on the same agent.
* Bad, because mode routing and assurance propagation must be implemented and tested.
* Bad, because the stricter Scientific path necessarily adds latency, cost and complexity over General mode (same trade-off as ADR-0029, but now scoped).

## More Information

* Supersedes: no prior ADR on operating modes; complements ADR-0029 Agent correctness architecture (which defines the correctness-centred loop that Scientific mode enforces) and the `c4-container.md` forward reference on the agent->RAG knowledge-source lifecycle.
* Related: ADR-0013 inspection, ADR-0019 shared abstract nodes, ADR-0023 checkpoint, ADR-0008/0011/0022/0024 RAG pipeline, ADR-0026 client-server.
* Code loci (to be aligned): `agent_pkg/klea_agent/klea_agent.py` mode routing at task entry (including ` --mode` CLI flag and the Scientific-without-source -> inform/downgrade-or-restart branch), `klea_agent/schemas.py` `mode`/`assurance` field on `KleaAgentState` (one-way downgrade only; upgrade requires new session), `klea_agent/ui/cli.py` mode flag, `klea_utils/stores` curated knowledge source lifecycle. Adding a mode does not re-decide ADR-0029; wiring changes that preserve both sets of invariants remain compliant.
* Relationship: ADR-0029 is not weakened. This ADR defines *when* its 11 invariants apply. Scientific mode = ADR-0029 in force; General mode = explicitly lower assurance. See ADR-0029 `c4-component-agent.md` alignment once mode routing is implemented.
* Grounding enforcement boundary: ADR-0036 (General mode MAY ground, Scientific mode MUST). General-path topology: ADR-0035.
