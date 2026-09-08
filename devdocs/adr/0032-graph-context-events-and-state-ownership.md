---
status: "accepted"
date: 2026-09-04
decision-makers: Ankur Sinha
consulted: ""
informed: klea contributors
---

# Graph-level context events and state ownership

## Context and Problem Statement

Frontends need session/run-level context that is not scoped to a node --
the agent's operating mode (ADR-0030) being the first
example.  The existing stream contract (ADR-0013) is node-scoped:
`NodeStreamEvent` carries ``info``/``debug``/``state``/``usage`` types,
each with a mandatory ``node``, rendered into per-node inspector/status
panes.  A mode badge is session-level, not per-node.

Within a LangGraph run, however, only nodes write custom stream events
(``get_stream_writer()`` is documented as callable only from inside a
node or functional-API task), and LangGraph state channels are fully
shared -- every node can read and write every state field.  If session
context is state, and state is node-writable, what does a separate
graph-level "context" event actually buy us, and how do we keep it from
being just another node-authored event under a different name?

We need to decide how session context is produced and how much of the
"graph-level" separation can be structural rather than behavioural.

## Decision Drivers

* Session context is a *projection of checkpointed state*: state is the
  single source of truth for the mode (the graph-level session attribute;
  assurance/verification is expected to be per-artifact and authoritative
  in node state once the ADR-0029 verification phase lands).
* The separation between node-scoped events and a graph-level context
  event should be as *structural* as LangGraph allows, not merely a
  naming convention.
* Persistence guidance from LangGraph: a setting that must persist
  across a thread's checkpoints belongs in **state** (or the application
  session store), not in the transient ``config`` object.
* The emission infrastructure must stay app-agnostic (ADR-0031); each
  app defines what its session context *is*.

## Considered Options

* **A. Node-authored context events** -- a node calls
  ``write_custom_stream({"type": "context", "data": {...}})``.  Rejected:
  the "graph-level" distinction is cosmetic (the writer is still a node),
  it adds a second authoring path that can diverge from state, and there
  is nothing structural stopping arbitrary nodes from emitting them.
* **B. State-derived context events via a graph hook (chosen)** --
  ``BaseLangGraph`` gains ``context_snapshot(state) -> dict | None``
  (default ``None``); ``run_graph_astream_events`` derives a
  ``{"type": "context", "data": {...}}`` event from each ``values``
  state snapshot (change-deduped).  Nodes cannot author ``context``
  events: the ``custom`` channel forwards only node-scoped types.
* **C. Ownership registry in state** -- a state field mapping node labels
  to the fields they may modify (e.g.
  ``{"ModeDecision": ["mode"]}``), plus a ``get_context``
  node emitting the event.  Rejected: LangGraph offers no per-node write
  restriction (verified: ``StateGraph.add_node`` deprecated kwargs are
  ``retry``/``input`` only; ``output_channels``/``input_channels`` in
  ``state.py`` are the compiled graph's run-stream plumbing, not per-node
  gates), so the registry is a convention stored in checkpoints with no
  enforcement, and context would still be node-authored.
* **D. Mode request via ``config["configurable"]``** -- rejected for a
  *session* mode: ``config`` is transient and not checkpointed, and the
  requested mode must persist for the session (a query succeeding
  without re-sending it should not silently fall back to general).
  Per the LangGraph persistence guidance the selection therefore belongs
  in state (or an application session store).  ``config`` remains the
  home for genuinely transient per-call flags in the future.

## Decision Outcome

Chosen option: **B. State-derived context events via a graph hook.**

State is the single source of truth for session context; the ``context``
stream event is a read-only projection of it, produced by the streaming
layer, not by nodes.

* `BaseLangGraph.context_snapshot(state: dict) -> dict | None` -- hook,
  default ``None``.  ``run_graph_astream_events`` calls it on every
  ``values`` snapshot (normalized to a dict, since pydantic-typed graphs
  yield the state *instance*), and yields a ``{"type": "context",
  "data": {...}}`` event when the returned snapshot changes.
* Node-authored ``custom`` events of type ``context`` are **not**
  forwarded: the ``custom`` channel handles ``progress`` and the
  node-scoped ``info``/``debug``/``state``/``usage`` types only.  The
  separation is structural on the emission side -- a node cannot produce
  a ``context`` event through any supported path.
* Each app overrides the hook to define its context: ``KleaAgent``
  returns ``{"mode", "requested", "note"}`` projected from its
  ``KleaAgentState.mode`` model.  ``requested`` is projected so the
  frontend can restore the re-request after a page reload: ``Mode`` is a
  whole-object field (no reducer), so an empty re-request would otherwise
  silently reset the checkpointed mode on the next query.  RAG returns
  ``None`` today, so no behaviour change.
* The mode lives in state as a single ``mode`` field holding a ``Mode``
  model: ``requested`` (the per-call ``extra_state`` ask at invoke),
  ``resolved`` (set by the ``ModeDecision`` node at task entry), and
  ``note`` (the inform-branch explanation).  Persisting the request in
  the checkpoint is what makes the session remember its mode across
  turns (per the LangGraph persistence guidance).  Writing the fields is
  a documented behavioural contract: only ``ModeDecision`` modifies
  them, because LangGraph gives no per-node write restriction (the
  alternative C was rejected on the same ground).
* Assurance/verification state is **deferred** to the ADR-0029
  verification phase.  A graph-level assurance scalar is deliberately
  *not* introduced: verification is an attribute of the *artifacts* a
  node produces (plan, generated code, executed results), so it is
  expected to ride the node/artifact state and the ADR-0013 node-event
  system, not the graph-level ``context`` projection.

### Consequences

* Good, because the ``context`` event cannot diverge from state: it is
  derived from the checkpoint, not authored.
* Good, because the node-event / context-event split is structural on
  the emission side -- nodes literally cannot emit ``context`` events.
* Good, because the infrastructure is app-agnostic: the hook defines
  semantics, the emission is generic and reusable (RAG could opt in
  later with zero base changes).
* Good, because the mode is explicit structured checkpointed state and the
  session remembers it via the checkpointed ``mode`` model; assurance/
  verification state is added later per-artifact rather than forced into
  the graph-level projection.
* Bad, because the ``context`` event is emitted a superstep-boundary
  later than a node-authored event would be (irrelevant for a badge).
* Bad, because ``context`` is stream-only: callers that use
  ``run_graph_invoke`` (``POST /query``) never see it.  Partially
  addressed by the hydration endpoint below (a direct checkpoint read);
  ``POST /query`` responses still carry no context -- why: a bare
  ``ainvoke`` emits no ``values`` events, so ``context_snapshot`` only
  runs on the streaming path and the hydration GET (see the
  ``run_graph_invoke`` docstring).
* Bad, because the frontend keeps ``chat["context"]`` in memory only:
  after a page reload the badge is empty until the next query streams.
  **Implemented** with a generic ``GET /chat/{user_id}/{chat_id}/context``
  endpoint (mounts in both apps): it reads the checkpointed projection
  through the same ``context_snapshot`` hook + ``_normalise_state_snapshot``
  used by the stream path, and returns ``{"context": null}`` while a
  thread has no checkpoint (expectation is 200 + null -- the chat exists,
  only its projection is unset).
* Bad, because who may write the mode fields is a behavioural contract
  (LangGraph offers no per-node write restriction).  Documented, and the
  rejected registry (C) is the cautionary alternative.

### Confirmation

* Structural: ``run_graph_astream_events`` emits ``context`` events only
  from the ``values`` branch via ``context_snapshot``; a node that tries
  to ``write_custom_stream({"type": "context", ...})`` gets it dropped
  (asserted by ``ContextGraph`` in ``utils_pkg/tests/test_graph_base.py``).
* ``ModeDecision.execute`` returns state updates only; ``KleaAgent``
  overrides ``context_snapshot`` to project ``mode``/``requested``/
  ``note`` from its ``mode`` field.
* Lint/type/docs gates remain: ``ruff check``, ``ty``, ``docs: make html``.

## Pros and Cons of the Options

### Node-authored context events (A)

* Good, because no infrastructure change; a node writes the same custom
  channel as everything else.
* Bad, because the graph-level claim is cosmetic -- the event is written
  during a node's run.
* Bad, because any node could emit context events, so the "session
  context" contract is unenforceable and can drift from state.

### State-derived context events via a graph hook (B, chosen)

* Good, because context is a projection of the single source of truth.
* Good, because the separation is structural on the emission side.
* Good, because the hook keeps the mechanism generic/reusable.
* Neutral, because values-derived events appear one superstep later.
* Bad, because write-side ownership of the underlying state fields
  remains a behavioural contract (LangGraph limitation, shared with all
  alternatives).

### Ownership registry in state (C)

* Good, because ownership is declared explicitly and is inspectable.
* Bad, because it has no framework enforcement (verified: no per-node
  write restriction in this LangGraph version), so it is a convention
  stored in checkpoints -- bloat plus brittle node-label coupling.
* Bad, because context is still authored by a node, so it does not
  address the graph-level question at all.

### Mode request via config (D)

* Good, because ``config["configurable"]`` is the idiomatic place for
  control flags and keeps the state surface purely factual.
* Bad, because ``config`` is transient and not checkpointed: the session
  would forget its mode selection unless the caller re-sends it every
  turn or the selection is stored out-of-graph (a follow-up
  `sessions_db`-style store mirroring per-chat model overrides).
* Neutral, because ``config`` stays available for genuinely transient
  per-call flags in the future.

## More Information

* Refines: ADR-0013 (stream contract gains the graph-level ``context``
  event type), ADR-0030 (the operating mode is surfaced to the frontend
  by this graph-level ``context`` event rather than node
  ``NodeStreamData`` -- the ADR-0030 confirmation wording is amended
  accordingly, and assurance/verification state is deferred to the
  ADR-0029 verification phase), and
  complements ADR-0031 (infrastructure remains app-agnostic; hooks are
  app-defined).
* Verified LangGraph facts (installed version): `get_stream_writer` is
  node/task-only; `StateGraph.add_node` deprecated kwargs are
  ``retry``/``input`` only; the `output_channels`/`input_channels`
  references in ``graph/state.py`` are the compiled graph's run-stream
  plumbing, not per-node write gates; a per-node *read* restriction
  exists via ``input_schema``; ``config`` is transient and not
  checkpointed; ``graph.update_state`` runs no nodes and emits no stream
  events.
* External state writes (``graph.update_state``) produce no stream
  events under any option here; a session-global mode toggle that must
  take effect without a query therefore needs a fetch/return surface
  (deferred; see the hydrate follow-up under Consequences).
* Code loci: ``utils_pkg/klea_utils/graph/base.py``
  (``context_snapshot`` + ``values``-branch emission),
  ``agent_pkg/klea_agent/klea_agent.py`` (hook override),
  ``agent_pkg/klea_agent/nodes/mode_router.py`` (``ModeDecision`` state
  updates only), ``utils_pkg/tests/test_graph_base.py`` (structural
  test).
