---
status: "accepted"
date: 2026-09-02
decision-makers: Ankur Sinha
consulted: ""
informed: klea contributors
---

# Agent correctness architecture: evidence, provenance and verification as architectural objects

## Context and Problem Statement

How do we build an AI-enabled pipeline for scientific work where correctness is a required property of the workflow -- not merely plausible generation, but outputs that are grounded in curated evidence, traceable via provenance, and subjected to independent verification, and where that grounding is a structural property of the pipeline rather than an optional capability the model may invoke?

Generation capability does not establish correctness, and topology cannot guarantee it. The issue is not only that a user cannot see what was relied on; it is that when retrieval and verification are capabilities the LLM *may* invoke, the LLM decides what to plan, what to retrieve, and what counts as verified. That guarantees the pipeline will find a way to the end point, not that the conditions for assessing correctness were necessarily met. Accessibility and verifiability are consequences of getting this right, not the guarantee itself. The pipeline must therefore be constructed so that the conditions under which correctness can be assessed are structurally enforced -- only evidence-grounded, provenance-carrying, independently verified outputs can reach the user as completed results. Making outputs accessible and verifiable is how the architecture makes correctness assessable, not what makes outputs correct.

## Decision Drivers

* Required property of correctness: the workflow must make it structurally impossible for an unverified or ungrounded result to be presented as a completed scientific result. Intermediate outputs from LLMs may exist internally; the gate is that they cannot reach the user as a correct scientific result. Topology does not make outputs correct; it makes correctness assessable. Speed of delivery is secondary to this guarantee.
* Grounding in curated evidence: outputs must be derived from retrieved evidence, not from parametric memory.
* Verifiable lineage via provenance: every claim must be traceable to its sources so correctness is demonstrable after the fact.
* Inspectability per stage: evidence, plan, execution and verification must each be separately inspectable, not conflated in one LLM call, so failures are attributable.
* Human agency: consequential actions must be intervenable before execution.
* Evidence-driven replanning: failure of evidence or verification must drive retrieval of more evidence and replanning, not context-soup retry.
* Model specialisation: explicit architectural stages allow models to be selected according to the capability required by each role, avoiding unnecessary use of expensive reasoning models for routine operations.

## Considered Options

* **A. General-purpose coding agents (Claude Code, Codex, opencode) with retrieval and verification as capabilities** -- wire MCP servers and vector stores into an off-the-shelf host as tools the LLM may call. The LLM decides per turn whether to retrieve, plan, execute, or declare success. Rejected: couples planning + retrieval + verification in a single LLM call; no explicit contract for evidence, provenance or verification; pipeline branching is uninspectable; correctness depends on prompt compliance, not architecture. This is the industry default described in ADR-0025 option A.
* **B. Klea agent: correctness-centred architectural loop (chosen)** -- treat evidence, provenance and verification as first-class architectural objects that every correctness-critical task must traverse. The task-solving process is an explicit loop with distinct stages, each addressing one verifiability question:

  | Stage | Question |
  |-------|----------|
  | Curated knowledge / RAG | Did the agent have the right information? |
  | Evidence inspection | Can I see what it relied on? |
  | Explicit plan | Can I understand what it intends to do? |
  | Human approval | Can I intervene before consequential actions? |
  | Execution | Did it actually implement the plan? |
  | Verification | Does the implementation work? |
  | Provenance | Can I trace decisions/results to sources? |
  | Replanning | What happens when evidence or implementation fails? |

  The loop is:

  ```
  TASK -> DISCOVERY -> RETRIEVE EVIDENCE -> EVIDENCE INSPECTION -> PLAN
         -> HUMAN REVIEW (approve / modify / reject) -> EXECUTE -> VERIFY
         -> {succeeds -> next step; fails -> REPLAN -> retrieve more evidence}
  ```

  Each stage is an architectural object (state field + node/edge contract), not an optional tool.
* **C. Tool-augmented general-purpose agent (experimental baseline, not chosen)** -- C is retained as an experimental architectural baseline rather than as a candidate Klea architecture. Use an existing coding agent as the orchestration layer while exposing Klea RAG and scientific MCP services (NeuroML, file-gateway, DOI, sandbox) as tools. This provides access to curated information and domain operations but leaves retrieval, planning, evidence assessment and verification under model control. C is not selected as the governing architecture; it exists to isolate the contribution of Klea's orchestration architecture from the contribution of its tools and domain knowledge. Rejected as governing architecture for the same reason as A: retrieval remains optional, evidence is not distinguished from synthesis, planning is not reified state, execution is not gated by approval, verification is not independent, and provenance is not structurally carried. The model may use the curated tools, but correctness-critical stages remain contingent on the model choosing to invoke and interpret them appropriately.

## Decision Outcome

Chosen option: **B. Klea agent correctness-centred loop with evidence, provenance and verification as architectural objects.**

This ADR establishes the correctness architecture as the governing design for `klea_agent`. The graph and implementation may evolve, but the architecture remains valid because it is independent of node names, edge labels, or tool sets.

The architecture guarantees that correctness-critical conditions cannot be bypassed; it does not guarantee that those conditions are themselves sufficient to establish truth. The invariant is: completed results cannot bypass required grounding, provenance and verification stages.

### Architectural invariants

These hold regardless of how the LangGraph is wired. They are properties of the pipeline, not of any single implementation.

1. **Grounding is mandatory at task and step levels.** Correctness-critical tasks must pass through retrieval before planning, and individual execution steps must retrieve or receive the evidence required for their implementation. The agent cannot decide to omit required grounding. Task-level retrieval informs planning; step-level retrieval provides context for individual steps so reasoning stays grounded throughout execution.
2. **Evidence is distinct from synthesis.** Agent-facing retrieval provides structured evidence with provenance; human-facing retrieval may provide synthesised answers. The agent never substitutes synthesis for evidence.
3. **Evidence is inspectable.** Retrieved evidence and its provenance are inspectable before planning and before execution.
4. **Planning is explicit state.** The plan is a versioned, inspectable description of the proposed execution, not text emitted by an LLM. It exists independently of execution and is the subject of review.

Consequential execution means an action that changes persistent state, modifies user/project artefacts, affects an external system, incurs material computational/resource cost, or produces an output intended to be treated as a scientific result. Read-only inspection, retrieval and other non-mutating operations are non-consequential.

5. **Consequential execution is gated.** No consequential execution begins until the plan has been approved or modified. Approval is a graph state/transition (`approved -> execute`, `modify -> plan`, `reject -> replan`).
6. **Execution is attributable.** Each execution is attributable to the plan step and evidence that produced it.
7. **Verification is explicit.** Successful tool execution is not correctness. Correctness requires a separate verification procedure against the task's success criteria, preferably using an independent mechanism such as tests, validators, simulations, or domain-specific checks rather than the same generation step that produced the result.
8. **Provenance is closed over completed results.** Every completed consequential artefact has a provenance chain linking its supporting evidence, relevant decision/plan step, execution, and verification. No artefact may be presented as a completed result if any required provenance link is missing. Provenance therefore covers source, version, scope, execution and verification.
9. **Invalidation triggers replanning.** Verification failure, contradictory evidence, or material changes to relevant execution context invalidate the current plan or its supporting assumptions and return the workflow to an appropriate retrieval/planning stage. Replanning must address the cause of invalidation rather than blindly retrying the previous context. Replanning is bounded and evidence-driven.
10. **Replanning must make progress.** A replanning cycle must either introduce new evidence, revise the plan, or change the execution/verification strategy. The workflow must not repeatedly execute an unchanged plan against unchanged evidence and context.
11. **The workflow, not the LLM, defines the correctness-critical control flow.** LLMs operate within explicit stage contracts; they may reason and make decisions within those stages, but no correctness-critical architectural invariant may depend on an LLM choosing to invoke, skip, or reinterpret that invariant. The graph -- stages, state, and transitions -- is fixed and enforceable independent of any model's choices. This is the primary distinction from a general-purpose coding agent where the LLM defines the workflow.

### Design properties

These are not correctness invariants but desirable properties realised by the same boundaries.

* **Model specialisation.** Stage boundaries permit different LLMs or deterministic mechanisms to be selected according to capability, cost and latency without changing the workflow architecture. Model choice is decoupled from workflow architecture. Individual stages may use different LLMs, models of different capability/cost, or deterministic mechanisms without changing the correctness architecture or its invariants. Model selection is therefore an implementation choice within a stage contract, not a property of the workflow itself.

### Implementation implications

These show how the invariants and design properties are realised today. They may change without invalidating the ADR.

* **Plan as state:** `PlanSchema` (`agent_pkg/klea_agent/schemas.py`) is the reified plan (ordered steps, available tools only, durable result names an artefact), versioned in the checkpoint, rather than LLM text.
* **Per-step evidence and outputs:** `step_outputs: dict[int, list[CallToolResult]]` and `artefacts: dict[str, ArtefactSchema]` carry per-step execution results; each step may carry its own evidence slice (invariant 1) and provenance lineage `{source, url, version, chunk_id, tool_call_id, timestamp}` (invariant 8).
* **Inspectability:** `NodeStreamData` via `AbstractLangGraphNode` (`utils_pkg/klea_utils/nodes/abstract.py`, ADR-0013/0019) renders evidence, plan, and verification inspectably; the graph cannot go from retrieval to execution without an inspection point (invariants 3, 4).
* **Gating:** permission-gated dispatch via `dispatch_tool_calls` (`klea_utils/mcp/dispatch.py`, ADR-0007/0003 `isError`) and the `HUMAN REVIEW` edge on `PlanSchema` (invariant 5); modality (always vs destructive-only vs checkpoint-resume) is configurable via checkpointer (ADR-0023).
* **Verification:** separate `VERIFY` stage (sandbox via `nml-mcp`, tests, NeuroML validation) against `GoalSchema.success_criteria`; only verified outputs advance the plan (invariant 7).
* **Checkpoint and type safety:** `KleaAgentState` provenance fields (`discovery_*`, `artefacts`, `step_outputs`) are distinct from `messages` and included in `get_allowed_msgpack_modules` for checkpointing (ADR-0023).
* **Model per role:** explicit stage boundaries allow different models per role according to reasoning requirements, latency and cost (design property). Cheap models can handle routine classification, retrieval assistance and tool selection while more capable reasoning models are reserved for planning, evidence assessment, verification and replanning; execution and validation can use deterministic mechanisms where possible. This is how the architecture realises model specialisation (decision driver) without coupling model choice to workflow correctness.
* **Current graph loci:** `agent_pkg/klea_agent/klea_agent.py:146` graph, `klea_agent/nodes/{goal_setter,planner,explore_planner,evaluator,tools_router}.py`, shared `ToolsPicker`/`ToolsCallerNode` (`klea_utils/nodes/tools_picker.py` / `tools_caller.py`, ADR-0020), mature RAG pipeline `rag_pkg/klea_rag/rag.py:191` (ADR-0008/0011/0022/0024) as the retrieval backend.

This supersedes ADR-0025's proposed topology: 0025's `goal_setter -> planner -> explore_planner -> tool_picker/caller -> observer -> evaluator` is retained as an implementation sketch but re-interpreted through the invariants and design properties above. Future LangGraph wiring changes that preserve the 11 invariants remain compliant with this ADR; implementation implications and design properties may be updated without re-deciding the architecture.

### Consequences

* Good, because correctness-critical stages are enforced by topology: no path exists from `TASK` to a user-visible completed result that bypasses evidence, provenance, plan review, or verification. Accessibility and verifiability follow from this guarantee. Option C cannot provide this because it leaves the same stages under model control.
* Good, because each stage answers one distinct question about correctness (did it have the right information? Can it be traced? Was it validated?), giving a stable mental model even as LangGraph node names change.
* Good, because inspection and provenance make the correctness claim auditable after the fact, not just plausible at generation time.
* Good, because tool-augmented baseline C is retained: the question "why not just augment an existing agent?" can be answered empirically by comparing B vs C on correctness metrics, not by assertion.
* Bad, because the architecture requires more LLM calls than a flat general-purpose agent: retrieval/evidence assessment, planning, execution decisions, and verification are separate stages. This increases inference cost and token consumption.
* Bad, because mandatory retrieval and multi-stage reasoning increase end-to-end latency. The existing Klea RAG pipeline is already slower than direct LLM/tool interaction; the agent necessarily adds further latency on top of retrieval rather than treating retrieval as an optional tool call.
* Bad, because the graph is more complex than a general-purpose agent. More state, transitions, failure modes, checkpoint boundaries, and stage-specific prompts must be implemented, tested and maintained.
* Bad, because mandatory verification can itself be expensive or unavailable for some tasks. Scientific verification may require simulations, tests, validators, external data or other computational resources.
* Bad, because correctness still depends on the quality and coverage of the evidence and verification mechanisms. An incomplete RAG index or inadequate verification procedure can still produce an incorrect result; the architecture makes these limitations explicit and assessable rather than eliminating them.
* Bad, because the stricter architecture may reduce the flexibility and responsiveness of a general-purpose agent. Tasks for which retrieval or verification adds little value may nevertheless incur some of the associated overhead.
* Good, because model selection is per architectural role. The explicit stage boundaries allow different models to be assigned to different roles according to their reasoning requirements, latency and cost. Cheap models can perform routine classification, retrieval assistance and tool selection, while more capable reasoning models can be reserved for planning, evidence assessment, verification and replanning. Execution and validation can use deterministic mechanisms where possible. Consequently, the additional number of LLM calls does not necessarily imply proportional increases in inference cost.
* Good, because these costs are deliberate: inference cost, latency and architectural complexity are being traded for explicit grounding, inspectability, provenance and verification. The project therefore treats these as measurable costs of the correctness architecture rather than optimisation targets that override its invariants.

### Architectural trade-off

Klea deliberately trades latency, inference cost and implementation complexity for stronger guarantees around grounding, provenance and verification.

A general-purpose agent may answer a question in fewer model calls by deciding dynamically whether retrieval, planning or verification is necessary. Klea instead requires correctness-critical stages to occur as part of the graph.

This means that Klea is not intended to minimise time-to-first-answer or inference cost. The relevant question is whether the additional cost produces measurably more correct, grounded and verifiable scientific outputs.

The cost of the architecture must therefore be evaluated alongside its benefits, including:

* end-to-end latency
* number of LLM calls
* token consumption
* computational cost of retrieval and verification
* task completion rate
* scientific/functional correctness
* provenance completeness
* rate of errors detected by verification

### Confirmation

* Architectural confirmation is structural, not tied to exact LangGraph edge labels or current node names which may change. The invariant is: no path from `TASK` to a user-visible completed result that bypasses retrieval, inspection, plan review, or verification. Baseline C fails this invariant by construction (those stages are optional tool calls).
* Implementation confirmation checks that the compiled `klea_agent` graph exhibits the invariants (e.g. `graph.get_graph().draw_mermaid()` shows no bypass of `RETRIEVE EVIDENCE`, `EVIDENCE INSPECTION`, `HUMAN REVIEW` (for consequential steps), and `VERIFY`; `klea_agent/nodes` wiring review; `KleaAgentState` carries provenance fields distinct from `messages`). Tool-augmented baseline C can be evaluated by swapping the orchestration layer while keeping the same tools, to measure the correctness delta.
* Lint/type/docs gates remain: `ruff check`, `ty`, and `docs: make html` render the updated `c4-container.md` forward reference.

## Pros and Cons of the Options

### Klea agent: correctness-centred loop (chosen)

* Good, because evidence, provenance and verification are architectural guarantees, not prompt hopes.
* Good, because every architectural stage has one responsibility (robust for small models, ADR-0025 drivers) and re-planning is evidence-driven.
* Good, because human approval is a structural gate before consequential execution.
* Good, because model selection is per architectural role. The pipeline is not tied to a single LLM: models can be selected independently for different stages according to capability, latency and cost requirements.
* Bad, because more stages to operate and tune than a flat agent.

### General-purpose agents with retrieval and verification as capabilities

* Good, because minimal graph, lowest latency, maximal LLM flexibility.
* Bad, because planning + retrieval + verification are conflated; pipeline is inspectability-poor and correctness is unenforceable.

### Tool-augmented general-purpose agent (experimental baseline, not chosen)

* Good, because curated information and domain tools are available; likely better than A for domain tasks; useful evaluation baseline.
* Good, because no new agent to build or maintain.
* Bad, because architectural guarantees are still absent: retrieval remains optional, evidence is not structurally distinguished from synthesis (invariant 2), plan is not explicit state (invariant 4), execution is not gated (invariant 5), verification is not independent (invariant 7), and provenance is not carried (invariant 8). The workflow may succeed, but correctness-critical stages remain contingent on the model choosing to invoke and interpret them appropriately.
* Neutral, because evaluation can quantify the delta between C and B: does enforcement (B) measurably improve correctness over augmentation (C)? The ADR retains C precisely to make that comparison possible.

## More Information

* Supersedes: ADR-0025 (proposed agent topology) and the `c4-container.md:139` forward reference (agent->RAG mechanism undecided -- this ADR governs the loop, not the RAG transport).
* Related: ADR-0016 BaseLangGraph template, ADR-0019 shared abstract nodes, ADR-0020 unified picker/caller, ADR-0013 inspection, ADR-0003 isError, ADR-0007 permissions, ADR-0004 bundled tools, ADR-0005 httpx, ADR-0023 checkpoint, ADR-0026 client-server, ADR-0024 file-gateway.
* Code loci (current, to be aligned): `agent_pkg/klea_agent/klea_agent.py:146` graph, `klea_agent/schemas.py:68` state, `klea_agent/nodes/{goal_setter,planner,explore_planner,evaluator,tools_router}.py`, `klea_utils/nodes/{abstract,tools_picker,tools_caller}.py`, `rag_pkg/klea_rag/rag.py:191` mature RAG pipeline to be composed.
* Status `accepted` 2026-09-02; loop invariants govern `klea_agent` implementation. `c4-container.md` and `c4-component-agent.md` to be aligned to reflect the architecture.
