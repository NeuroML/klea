# API / SSE interaction sequences: RAG and agent chat flows

Status: architecture documentation.  Reflects the monorepo at the time of
writing.  This is the C4 *dynamic* view (runtime interaction) that pairs with
the static C4 views in the sibling files.  The chat request lifecycle is shown
per app because the two apps share the transport but differ in their contract
and lifecycle: RAG is a fire-and-answer pipeline, while the agent adds
human-in-the-loop interrupts, failure resume, and a session-context
projection.

See also: `streams.md` (the event catalogue this view sequences),
`c4-container.md` (Level 2), `c4-component-rag.md` and
`c4-component-agent.md` (Level 3 static views).

## Scope

The diagrams show the ordered HTTP and Server-Sent Events (SSE) exchange
between a client UI and a Klea FastAPI app, plus the app-side components each
step reaches.  Mermaid `sequenceDiagram` is used (the C4 dynamic notation in
docs-as-code form; renders on GitHub).  These are documentation of the
*intended* flow, not telemetry: for live wire traffic use the browser Network
panel or distributed tracing.

Every app is assembled by `make_app` (`klea_utils/api/app.py`) from the shared
routers (`health`, `sessions`, `messages`, `context`, `models`, `credentials`)
plus the app's own chat router (`klea_utils/api/chat_core.py` plumbing).  The
app port differs (`klea_rag` :8005, `klea_agent` :8006) but the paths below are
identical across apps.

## Shared framing (both apps)

The bootstrap reads are identical for RAG and the agent; the streaming path
shares the framing in `chat_core.stream_response` (single-flight per thread,
SSE framing, error frames) and `BaseLangGraph.run_graph_astream_events`
(emission).  What differs is the request body and the event/lifecycle branches,
shown per app below.

```mermaid
sequenceDiagram
    autonumber
    participant UI as UI
    participant API as FastAPI app
    participant Core as chat_core
    participant Reg as ActiveRunRegistry
    participant Graph as BaseLangGraph
    participant Store as SessionStore

    Note over UI,Store: Bootstrap (once per page load)
    UI->>API: GET /health/ready
    API-->>UI: 200 ready
    UI->>API: GET /chat/{user_id}
    API-->>UI: chat list
    UI->>API: GET /chat/{user_id}/{chat_id}/messages
    API-->>UI: message history
    UI->>API: GET /chat/{user_id}/{chat_id}/context
    API-->>UI: session context projection
    UI->>API: GET /chat/{user_id}/{chat_id}/models/active
    API-->>UI: resolved model per role

    Note over UI,Store: Streaming turn (shared framing)
    UI->>API: POST /query/stream with request body
    API->>Core: stream_response(...)
    Core->>Core: validate action against checkpoint state
    Core->>Store: create chat and persist user turn
    Core->>Reg: ensure thread free (single-flight)
    Core-->>UI: 200 text/event-stream
    Core->>Graph: run_graph_astream_events(input, thread_id, context)
    loop graph supersteps
        Graph-->>Core: progress / inspect / state / tool / context
        Core-->>UI: data: JSON event
        Core-->>UI: data: ping (only while a node runs >15s with no event)
    end
    alt success
        Core->>Store: persist assistant turn
        Core-->>UI: data: complete
    else failure
        Core-->>UI: data: error (resumable hint)
    end
    Core->>Reg: clear thread
```

Notes:

* The user turn is persisted when the run starts; the assistant turn on
  `complete` (or the interrupt question on pause), so a failed run stays
  visible and retryable.
* `POST /query/cancel` is a separate, idempotent request (always 204) that
  reaches the registered asyncio task via `ActiveRunRegistry.cancel`; the
  graph leaves a resumable checkpoint.
* `chat_core.stream_response` injects a periodic `ping` frame whenever no graph
  event arrives for `HEARTBEAT_INTERVAL_SECONDS` (15 s), so a long-running node
  (e.g. `run_command`) does not let the client's idle read timeout (300 s) or a
  proxy drop the SSE stream.  Clients ignore `ping`; see `streams.md`.

## RAG lifecycle (`klea_rag`)

RAG's `ChatPayload` is `{query, chat_id, user_id}` only.  It has no
`resume` / `interrupt_response` / `interrupt_cancel` fields and no
`context_snapshot` override, so no `context` event is produced.  The one
terminal is `complete`; failures surface as `error`.  Graph node labels match
`rag_pkg/example-configs/rag-lang-graph.mmd`.

```mermaid
sequenceDiagram
    autonumber
    participant UI as UI
    participant API as klea_rag chat router
    participant Core as chat_core.stream_response
    participant Graph as RAG graph
    participant LLM as LLM provider
    participant VS as Vector / BM25 stores
    participant MCP as MCP servers

    UI->>API: POST /query/stream {query, chat_id, user_id}
    API->>Core: stream_response(query=...)
    Core-->>UI: 200 text/event-stream
    Core->>Graph: astream_events(query)
    Graph-->>Core: Initializing
    Graph-->>Core: Checking safety
    alt safe
        Graph-->>Core: Classifying question
        alt domain_query
            Graph-->>Core: Splitting
            Graph-->>Core: Generating search
            Graph->>LLM: generate retrieval queries
            Graph-->>Core: Selecting tools
            Graph->>MCP: dispatch selected tools
            Graph->>VS: retrieve documents / BM25
            Graph-->>Core: Retrieving information
            Graph->>LLM: generate answer
            Graph-->>Core: Generating answer
            Graph-->>Core: Evaluating answer
            Graph-->>Core: Routing evaluation
            opt fallback
                Graph-->>Core: Answering generally
            end
            Graph-->>Core: Preparing response
        else non_domain_query
            Graph-->>Core: Answering generally
        else non_domain_refuse
            Graph-->>Core: Refusing query
        end
    else unsafe
        Graph-->>Core: Declining query
    end
    Graph-->>Core: Summarizing history
    alt success
        Core-->>UI: data: complete
    else failure
        Core-->>UI: data: error
    end
```

Notes:

* No `interrupt` event is ever emitted; the loop branches are the RAG
  evaluator's routing, not pauses.
* `POST /query/cancel` is the only way to stop a run mid-flight.

## Agent lifecycle (`klea_agent`)

The agent's `ChatPayload` adds `resume`, `interrupt_response`, `interrupt_id`,
`interrupt_cancel`, `mode`, and `access_level`.  It overrides
`context_snapshot` (`klea_agent.py`), so the graph emits a `context` event
carrying mode, requested mode, assurance, note, and effective access level
(ADR-0032).  It has HITL nodes (`Awaiting review`, `Awaiting input`) whose
pause is delivered as an `interrupt` event (ADR-0046).  Graph node labels match
`agent_pkg/example-configs/klea-agent-lang-graph.mmd`.

```mermaid
sequenceDiagram
    autonumber
    participant UI as UI
    participant API as klea_agent chat router
    participant Core as chat_core.stream_response
    participant Graph as Agent graph
    participant Store as SessionStore
    participant LLM as LLM provider
    participant MCP as MCP servers

    UI->>API: POST /query/stream {query, mode, access_level}
    API->>Core: stream_response(extra_state={mode, access_level})
    Core-->>UI: 200 text/event-stream
    Core->>Graph: astream_events
    Graph-->>Core: Initializing
    Graph-->>Core: Determining mode
    Graph-->>Core: context {mode, requested, assurance, access_level}
    Core-->>UI: data: context
    Graph-->>Core: Checking safety
    alt task
        Graph-->>Core: Deciding route (task)
        Graph-->>Core: Planning
        alt needs_input
            Graph-->>Core: Awaiting input (interrupt)
            Core->>Store: persist question
            Core-->>UI: data: interrupt
            Note over UI,Store: Run pauses. No complete event follows.
            UI->>API: POST /query/stream {interrupt_response, interrupt_id}
            API->>Core: stream_response(interrupt_response=...)
            Core->>Graph: Command(resume=answer)
            Graph-->>Core: resumes at Awaiting input
        else review
            Graph-->>Core: Awaiting review (interrupt)
            Core-->>UI: data: interrupt (plan review)
            Note over UI,Store: If the run is paused, a new query is rejected with 409.
            UI->>API: POST /query/stream {interrupt_response: decision}
            API->>Core: stream_response(interrupt_response=...)
            Core->>Graph: Command(resume=decision)
            Graph-->>Core: resumes at Awaiting review
        else reasoning
            Graph-->>Core: Reasoning
            Graph-->>Core: Evaluating
        else tool
            Graph-->>Core: Selecting tools
            Graph->>MCP: dispatch selected tools
            Graph-->>Core: Running tools
            Graph-->>Core: Evaluating
        end
        Graph-->>Core: Composing answer
    else chat
        Graph-->>Core: Deciding route (chat)
    end
    Graph-->>Core: Preparing response
    Graph-->>Core: Summarizing history
    Core->>Store: persist assistant turn
    Core-->>UI: data: complete
```

Notes:

* **Answering an interrupt**: the follow-up `POST /query/stream` carries
  `interrupt_response` (and `interrupt_id`); `chat_core` validates it against
  the thread's paused state and resumes with `Command(resume=...)`.  A plain
  query while paused is rejected with 409.
* **Cancelling an interrupt**: `interrupt_cancel: true` resumes with
  `{"action": "cancel"}` and routes to `Cancelled`, a terminal run.
* **Resuming a failed run**: `resume: true` re-invokes the graph with `None`
  input so the failed node re-runs from its checkpoint (no user turn written).
* **Stopping an active run**: `POST /query/cancel` (idempotent, 204) cancels
  the asyncio task; the checkpoint stays resumable.

## Per-app deltas

| Aspect | `klea_rag` | `klea_agent` |
|--------|------------|--------------|
| Chat payload | `query`, `chat_id`, `user_id` | adds `resume`, `interrupt_response`, `interrupt_id`, `interrupt_cancel`, `mode`, `access_level` |
| Initial state extras | none | `mode.requested`, optional `access_level` |
| `context` event | none (base `context_snapshot` returns `None`) | emits mode / assurance / note / access_level |
| HITL `interrupt` / resume | none | `Awaiting input`, `Awaiting review` |
| Failed-run resume | n/a | `resume: true` |
| Cancel | `POST /query/cancel` | `POST /query/cancel` |
| Shared plumbing | `chat_core.stream_response`, `sse.py`, `runs.py`, `make_app` | same |
| Graph export | `rag_pkg/example-configs/rag-lang-graph.mmd` | `agent_pkg/example-configs/klea-agent-lang-graph.mmd` |

## Pointers

* Transport and framing: `klea_utils/api/sse.py` (client),
  `klea_utils/api/chat_core.py` (server runners),
  `klea_utils/api/chat_common.py` (thread identity, cancel),
  `klea_utils/api/runs.py` (`ActiveRunRegistry`).
* HITL: `klea_utils/api/hitl.py`; ADR-0046.
* Event catalogue: `streams.md`; ADR-0040 (amends ADR-0013).
* App contracts: `rag_pkg/klea_rag/api/chat.py`,
  `agent_pkg/klea_agent/api/chat.py` (ADR-0031).
* Session context: `agent_pkg/klea_agent/klea_agent.py` `context_snapshot`;
  ADR-0032.
* Routers: `klea_utils/api/{health,sessions,messages,context,models,credentials}.py`.
