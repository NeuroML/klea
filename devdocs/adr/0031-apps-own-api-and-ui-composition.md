---
status: "accepted"
date: 2026-09-04
decision-makers: Ankur Sinha
consulted: ""
informed: klea contributors
---

# Apps own API contracts and UI composition; klea_utils is a components/helpers library

## Context and Problem Statement

`klea_utils` currently hosts a generic FastAPI chat API (`klea_utils.api`) and a
shared NiceGUI frontend (`klea_utils.ui`) that both apps (`klea_rag`, `klea_agent`)
wrap or launch. As the agent grows agent-specific state -- operating mode and
assurance (ADR-0030), plan steps, verification status, provenance -- the shared
chat contract would have to absorb one app's branches, and the shared UI would
have to learn one app's panels.

Where should the API contract and the UI composition live?

## Decision Drivers

* Apps have divergent contracts: payload schemas, SSE event streams, and the state
  fields they must expose to their frontends differ (e.g. only the agent carries
  `mode`/`assurance`).
* `klea_utils` must stay app-agnostic so it does not grow one app's specific
  behaviour under the guise of shared code.
* The full NiceGUI page must not be duplicated wholesale per app; reusable
  components remain shared.
* The wire contract between graph and frontends (`NodeStreamData`/`NodeStreamEvent`,
  the SSE event stream) should stay a single stable contract.

## Considered Options

* **A. One generic chat API and UI in utils shared by all apps** -- extend the
  shared router and shared page as each app needs new fields. Status quo.
* **B. Apps own API contract and UI composition; utils ships components/helpers
  only** -- each app defines its own payloads/routes/SSE framing and composes its
  own page from shared components (chosen).
* **C. Fork utils per app** -- each app copies the full API and UI it needs.
  Rejected: maximum duplication and drift.

## Decision Outcome

Chosen option: **B. Apps own API contracts and UI composition; `klea_utils` is a
components/helpers library.**

The boundary is a governance rule for where code lives, not a packet of file
moves:

* **`klea_utils` keeps** pure helpers (`paths`, `plogging`, `llm`, `stores`,
  `mcp`, `biblio`, `errors`, `imports`), graph infrastructure (`BaseLangGraph`,
  shared nodes, `NodeStreamData`/`NodeStreamEvent`, SSE wire contract), generic
  plumbing (the `make_app`/`make_serve_app` factories, `health`/`messages`/
  `sessions`/`models` routers, the SSE client), and the NiceGUI component library
  (`klea_utils.ui.web.nicegui.components`) plus shared frontend helpers
  (`state`, `client`, `parser`).  No module here encodes a single app's API
  contract or page layout.
* **Each app owns** its chat router (own `ChatPayload` + `create_chat_router()`
  using the shared `klea_utils.api.chat_core` helper, with app-specific
  `enrich` on the stream), its web page composition (`<app>.ui.web` builds the
  page from utils components), and its CLI wiring (which web entry to launch).

This refines ADR-0026: the client-server architecture stands unchanged, but the
placement of the orchestrator-facing API and of the Client UI container described
there is amended: the API contract and the UI composition live in the apps, not
in `klea_utils`. `c4-container.md` reflects this.

### Consequences

* Good, because agent-specific state (mode, assurance, plan, verification,
  provenance) can flow to its own API and UI without growing `klea_utils`.
* Good, because each app's contract can evolve independently without coordinated
  changes to shared code.
* Good, because UI components stay small, specific, and reusable across apps.
* Good, because the graph-to-frontend wire contract remains a single stable
  contract in `klea_utils`.
* Bad, because each app now carries its own chat router and page composition
  (initial near-duplication between `klea_rag` and `klea_agent`).
* Bad, because the boundary requires discipline: app-specific code must not
  creep back into `klea_utils`.

### Confirmation

Structural: `klea_utils.api` contains no chat payload or router tied to a single
app's state -- only the generic `chat_core` helper and the generic routers;
`klea_agent.api.chat`/`klea_rag.api.chat` define their own `ChatPayload` and
`create_chat_router()`; each app's `<app>.ui.web` composes its page exclusively
from `klea_utils` components; `mode`/`assurance` appear only in
`klea_agent` state, API and UI.  Lint/type/docs gates remain: `ruff check`,
`ty`, and `docs: make html`.

## Pros and Cons of the Options

### One generic chat API and UI in utils (A)

* Good, because both apps reuse identical endpoints and a single page.
* Bad, because extending the shared contract for one app's state (mode,
  assurance) forces app-specific branches into shared code.
* Bad, because `klea_utils` stops being "general enough" and grows whichever
  app asks for features first.

### Apps own API contract and UI composition (B, chosen)

* Good, because app-specific state, endpoints and UI elements live in the app.
* Good, because shared code stays app-agnostic and reusable.
* Good, because the stable wire contract keeps all frontends interoperable.
* Neutral, because the apps' chat routers and pages are initially near-identical;
  they diverge only as the apps' contracts diverge.
* Bad, because moderately more code lives in each app.

### Fork utils per app (C)

* Good, because each app is maximally independent.
* Bad, because the duplicated helpers and UI cannot be fixed once for all apps.
* Bad, because the shared wire contract could drift between forks.

## More Information

* Refines: ADR-0026 (client-server architecture stands; the described placement
  of the orchestrator API and Client UI is amended) and the `c4-container.md`
  Client UI container.
* Complements: ADR-0016 (`BaseLangGraph`), ADR-0019 (shared abstract nodes),
  ADR-0030 (agent operating modes; the first divergent agent contract that
  motivates this boundary).
* Code loci (to be aligned): `utils_pkg/klea_utils/api/chat_core.py`,
  `klea_rag/klea_rag/api/chat.py`, `klea_agent/klea_agent/api/chat.py`,
  `klea_utils/ui/web/nicegui/components/*`, `klea_rag/klea_rag/ui/web/*`,
  `klea_agent/klea_agent/ui/web/*`.
