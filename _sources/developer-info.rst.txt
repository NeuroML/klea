Developer info
==============

Klea keeps two documentation layers: this public user guide (``docs/``) and
internal developer notes (``devdocs/``) in the same repository.  ``devdocs/``
is the single source of truth for architecture, component contracts, and
decisions; this page links to it on GitHub (``development`` branch) and avoids
duplicating the diagrams and records here.

Developer docs on GitHub
------------------------

* `devdocs index on GitHub
  <https://github.com/NeuroML/klea/blob/development/devdocs/README.md>`__
  -- curated index of all developer notes (system docs and ADRs).
* `devdocs/ on GitHub
  <https://github.com/NeuroML/klea/tree/development/devdocs>`__
  -- browse the full folder (``system/``, ``adr/``, ``.agents/``).

``devdocs/`` is for contributors.  See :doc:`contributing` for workflow,
commands, and the ``AGENTS.md`` instructions for AI assistants.

Architecture (C4 model)
-----------------------

Klea's architecture is documented with the `C4 model
<https://c4model.com/>`_.  The model is maintained as internal developer
documentation in ``devdocs/`` so the diagrams below link to the relevant
files on GitHub.

Level 1 -- System Context
~~~~~~~~~~~~~~~~~~~~~~~~~

The system context diagram shows Klea as a single system, the people who use
it, and the external software systems it depends on.  Klea is developed as a
general-purpose RAG + agentic assistant for scientific research, and is being
tested as part of the BioFAIR Pathfinder project for neuroscience research
(via the ``nml-mcp`` server and the curated NeuroML vector stores), but the
agent and RAG are domain-configurable and work for any domain.

- `System Context diagram (Level 1)
  <https://github.com/NeuroML/klea/blob/development/devdocs/system/c4-system-context.md>`__

Level 2 -- Container
~~~~~~~~~~~~~~~~~~~~

The container diagram zooms into Klea and shows the containers -- the
independently deployable applications/services/datastores and the shared
library -- plus how they interact and connect to the external systems
from Level 1.

- `Container diagram (Level 2)
  <https://github.com/NeuroML/klea/blob/development/devdocs/system/c4-container.md>`__

Level 3 -- Component (RAG)
~~~~~~~~~~~~~~~~~~~~~~~~~~

The RAG component diagram zooms into the ``klea_rag`` container and shows
its components -- the graph nodes plus retrieval, MCP, and store
interactions -- and embeds the auto-generated LangGraph Mermaid source as
its faithful core.

- `RAG component diagram (Level 3)
  <https://github.com/NeuroML/klea/blob/development/devdocs/system/c4-component-rag.md>`__

Deployment
~~~~~~~~~~

The deployment diagram maps the Klea containers onto build-time and
run-time deployment nodes (developer workstation vs local vs container
platform with HuggingFace Spaces as a nested node).

- `Deployment diagram
  <https://github.com/NeuroML/klea/blob/development/devdocs/system/c4-deployment.md>`__

Further component (agent) and code views will be added to ``devdocs/`` as
they are written.

Architecture Decision Records (ADRs)
------------------------------------

ADRs are short, numbered records in MADR format in ``devdocs/adr/``
(``NNNN-<slug>.md``).  Each records context, options considered, outcome, and
consequences.  See `adr-template.md
<https://github.com/NeuroML/klea/blob/development/devdocs/adr-template.md>`__
for the template.

Browse all ADRs on GitHub:

* `devdocs/adr/ on GitHub
  <https://github.com/NeuroML/klea/tree/development/devdocs/adr>`__

Other system notes
------------------

System and component contracts (Mermaid diagrams and data-flow notes)
live in ``devdocs/system/``:

* `devdocs/system/ on GitHub
  <https://github.com/NeuroML/klea/tree/development/devdocs/system>`__
