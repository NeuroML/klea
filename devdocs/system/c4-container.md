# C4 model: Level 2 -- Container diagram

Status: architecture documentation. Reflects the monorepo at the time of
writing. This is the Level 2 view of the Klea C4 model; the Level 1 system
context is in `c4-system-context.md`, the RAG Level 3 view is in
`c4-component-rag.md`, and the deployment view is in `c4-deployment.md`.
Lower-level code views live in sibling files to be added as needed.

## Scope and intent

A container diagram zooms into the system in scope (Klea) and shows the
*containers* -- the independently deployable applications, services, datastores,
and the shared library -- plus how they interact and how they connect to the
external systems from Level 1.  Per the C4 standard
(https://c4model.com/diagrams/container) containers are things that can be
deployed/run independently (or, for a library, are a separately packaged unit).

**System in scope:** the whole Klea product.  Containers map closely to the
monorepo packages, but note that `klea_utils` is a *library* (not independently
deployed) and the bundled `klea-mcp` server is launched *as a subprocess* of
each app rather than run on its own.

Level 1 is kept as a `C4Context` diagram.  Level 2 is rendered as a Mermaid
`flowchart` with `layout: elk` instead of `C4Container` because Mermaid's C4
has a fixed 2-column grid with straight `Rel` lines that obscure boxes at this
density; `elk` orthogonally routes edges around nodes.  This is a
documentation-notation choice, not a system architecture decision, so no ADR is
created  ---  see the note below.

## Container diagram -- Klea containers (flowchart, elk)

```mermaid
---
config:
  layout: elk
  elk:
    mergeEdges: false
    nodeSpacing: 40
    rankSpacing: 60
---
flowchart TB

    %% Persons
    researcher["Researchers<br/>Use Klea across research workflow:<br/>literature via RAG; agent-driven<br/>hypothesis / plan / execute"]
    devops["Operators / DevOps<br/>Configure profiles, build/manage<br/>vector stores, run MCP servers,<br/>deploy locally or as shared infra"]
    extagent["External AI Agents<br/>Consume nml-mcp and<br/>(optionally) RAG over HTTP / MCP"]

    %% Klea boundary
    subgraph Klea [Klea]
        direction TB
        ui["Client UIs<br/>Python (klea_agent.ui / klea_rag.ui)<br/>Per-app NiceGUI pages + CLI wiring<br/>Compose klea_utils UI components; CLI auto-spawns server, connects over HTTP/SSE"]
        agent["klea_agent<br/>Python / FastAPI / LangGraph<br/>General-purpose coding agent (KleaAgent)<br/>includes former code_pkg  ---  WIP<br/>CLI: klea; serve: :8006"]
        rag["klea_rag<br/>Python / FastAPI / LangGraph<br/>Domain-configurable RAG pipeline (RAG)<br/>CLI: klea-rag; serve: :8005  ---  mature"]
        nmlmcp["nml-mcp (neuroml_mcp)<br/>Python / FastMCP<br/>NeuroML MCP server<br/>CLI: nml-mcp  ---  streamable-http :8542"]
        bundled["bundled klea-mcp<br/>Python / FastMCP<br/>Shared tools (web_fetch, file read/list, download)<br/>stdio subprocess per app; standalone via klea-mcp"]
        utils["klea_utils (components/helpers library)<br/>Python<br/>BaseLangGraph + shared nodes, FastAPI helpers,<br/>vector-store + BM25, UI components, biblio/DOI"]
        vstores["Vector / BM25 Stores<br/>Chroma, Qdrant, pgvector + BM25<br/>URI paths: chroma:/, qdrant:http://, pgvector:postgresql://"]
        sqlite["Session / Checkpoint Stores<br/>SQLite<br/>sessions.db, checkpoints.db"]
    end

    %% External systems
    llm["LLM Providers<br/>Ollama, OpenAI, HuggingFace,<br/>custom OpenAI-compatible"]
    vbackends["Vector Store Backends<br/>Chroma, Qdrant, pgvector engines"]
    datasources["Data Sources<br/>Domain data (nml-mcp example:<br/>NeuroML-DB, OSB, docs.neuroml.org,<br/>ModelDB/DANDI/FigShare/GitHub, DOI)<br/>Reached ONLY via nml-mcp"]
    biofair["BioFAIR Infrastructure<br/>UKRI life-sciences commons (to-be)<br/>Data Commons + Method Commons via APIs"]
    web["Web / Internet<br/>General web access"]
    sandbox["Sandbox Execution<br/>Local subprocess + Docker<br/>runs generated NeuroML / LEMS code"]
    hf["HuggingFace<br/>Model hub + Spaces hosting"]

    %% Core interactions
    researcher --> ui
    devops --> agent
    devops --> rag
    devops --> nmlmcp
    extagent --> nmlmcp
    extagent -.-> rag
    ui --> agent
    ui --> rag
    agent --> bundled
    rag --> bundled
    agent --> nmlmcp
    rag --> nmlmcp
    agent -.-> rag
    agent --> vstores
    rag --> vstores
    agent --> llm
    rag --> llm
    agent --> sqlite
    rag --> sqlite
    agent --> utils
    rag --> utils
    nmlmcp --> utils
    vstores --> vbackends
    nmlmcp --> datasources
    nmlmcp --> web
    nmlmcp --> sandbox
    agent -.-> biofair
    rag -.-> biofair
    agent --> hf
    rag --> hf
    nmlmcp --> hf
```

*Notes:* `flowchart` with `layout: elk` is used here because Mermaid's `C4Container` has a fixed 2-column grid and straight `Rel` lines that obscure boxes at this density. `elk` provides orthogonal edge routing around nodes. Level 1 remains `C4Context` (less dense, renders fine). Dashed edges (`-.->`) denote to-be / optional integrations (`BioFAIR`, `agent->rag`, `extagent->rag`). GitHub's Mermaid renderer may fall back from `elk` to `dagre` if `@mermaid-js/layout-elk` is not bundled  ---  the diagram remains readable, just with dagre routing.

## Containers

| Container | Package | Role | Key entry points |
|-----------|---------|------|------------------|
| Client UIs | `klea_agent.ui` / `klea_rag.ui` | Per-app NiceGUI pages and CLI wiring composed from `klea_utils` UI components; CLI auto-spawns the server and connects over HTTP/SSE | `klea`, `klea-rag` CLIs |
| `klea_agent` | `agent_pkg` | General-purpose coding agent (`KleaAgent` over `BaseLangGraph`); **WIP** | `klea`, `klea-serve` (HTTP `:8006`) |
| `klea_rag` | `rag_pkg` | Domain-configurable RAG pipeline (`RAG` over `BaseLangGraph`); mature path | `klea-rag`, `klea-rag-serve` (HTTP `:8005`) |
| `nml-mcp` | `mcp_pkg` | NeuroML MCP server: model gen, NeuroML-DB/OSB search, sandboxed code exec, web/doc tools | `nml-mcp` (streamable-http `:8542`) |
| bundled `klea-mcp` | `klea_utils.mcp.server.bundled` | Shared tools server (web_fetch, file read/list, download); launched as a stdio subprocess by each app | `klea-mcp` (standalone) |
| `klea_utils` | `utils_pkg` | Components/helpers library: `BaseLangGraph`, shared graph nodes, FastAPI helpers + generic routers, vector-store + BM25 managers, UI components, biblio/DOI | (imported by all apps) |
| Vector / BM25 Stores | -- | Retrieval stores (URI-style paths: `chroma:`, `qdrant:`, `pgvector:`, BM25 `.pkl`) | -- |
| Session / Checkpoint Stores | -- | SQLite `sessions.db`, `checkpoints.db` | -- |

## How the containers interact

- Every app (`klea_agent`, `klea_rag`) subclasses `BaseLangGraph`
  (`klea_utils.graph.base`) and is served as a FastAPI app assembled from the
  generic `klea_utils.api` helpers (`make_app` + the generic routers), with
  each app defining its own chat router (API contract, see ADR-0031).
- Each app launches the **bundled `klea-mcp`** server as a **stdio subprocess**
  and connects to it (plus any configured MCP servers) at runtime via the
  `MCPConfig` mechanism.
- All three apps also call the **`nml-mcp`** NeuroML server over HTTP/MCP.
- Researchers reach the apps through the **Client UIs** (each app's CLI spawns
  the server, then that app's TUI/Web page connects over HTTP/SSE; per-app
  pages compose shared `klea_utils` UI components, ADR-0031).
- Datastores: apps read/write the **Vector / BM25 Stores** (backed by the
  Chroma/Qdrant/pgvector engines) and persist conversation/checkpoint state in
  **SQLite**.
- `nml-mcp` is the only container that reaches the external **Data Sources**
  (NeuroML-DB, OSB, docs.neuroml.org, ModelDB/DANDI/FigShare/GitHub, DOI
  resolvers) and the **Sandbox** / **Web** systems.

## Open architecture decision (forward reference)

The edge `klea_agent -> klea_rag` ("uses RAG as retrieval backend") is drawn
but its **mechanism is undecided** -- tracked as a future ADR.  Candidate mechanisms are the RAG HTTP API versus
direct vector-store access; note that RAG returns natural-language answers
for humans while the agent needs the retrieved documents.  Until then, the
agent and RAG are wired through shared MCP servers (e.g. both point at
``nml-mcp``) rather than a direct code dependency.  See also the agent
correctness architecture ``ADR-0029`` (``accepted``; supersedes ``ADR-0025``).

## Out of scope (Level 3+)

The internals of the RAG container (``classify_question``,
``generate_retrieval_query``, ``retrieve_info``, ``answer_from_context``,
``evaluator`` etc.) are shown at Level 3 in `c4-component-rag.md`
(auto-generated Mermaid core + ``elk`` augmentation, with drift check
against ``rag_pkg/example-configs/rag-lang-graph.mmd``).  The agent's graph
nodes (``planner``, ``explore_planner``, ``goal_setter``,
``evaluator``, ``tools_router``) will be shown at Level 3 when the
correctness architecture (``ADR-0029``, ``accepted``; superseding ``ADR-0025``) is implemented.  The ``nml-mcp``
tool/sandbox layout and the ``klea_utils`` API/stores internals are
future code-level views.  The deployment view (local vs container
platform with HuggingFace Spaces as a nested node) is ``c4-deployment.md``.
