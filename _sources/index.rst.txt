Welcome to Klea
===============

Knowledge vaLidated Expert AI Assistant for scientific research.

Grounded, citation-backed answers over your own research sources.

Klea is a suite of AI tools for scientific research: a general-purpose
research agent, a generic RAG pipeline, and MCP servers for modelling and
analysis.

Why Klea
--------

Klea is a research assistant that grounds its answers in your own sources.
Queries are answered from domain-configurable knowledge stores (documents,
papers, databases) rather than the model's memory alone: answers are checked
by an evaluation loop, cite the documents they drew on, and record their
provenance so researchers can inspect and verify the output.  When a query
cannot be grounded in the available sources, Klea flags the fallback rather
than presenting it as confident fact.

On top of that grounded retrieval core sits a general-purpose research agent
for the research lifecycle -- literature review, hypothesis generation,
planning, coding, pipeline execution, and analysis.  Because capabilities
are supplied by MCP tools and domain-configurable knowledge stores rather
than hard-coded, the same assistant extends to new research tasks as tools
are added.  That focus on grounded, cited answers is what sets Klea apart
from general-purpose chat and coding assistants.

**Status:** the RAG pipeline (``klea_rag`` / ``klea_utils``) is ready to use
today.  The agent (``klea_agent``) is under active development, with an
initial release planned.

Features
--------

**Retrieval (RAG)**

* Multi-domain knowledge stores with automatic query classification and routing
* Grounded answers -- retrieval plus an evaluation loop, with every response
  recording the sources and tools it drew on
* Hybrid retrieval combining dense vector search and BM25 keyword search, fused
  with Reciprocal Rank Fusion and a recency tiebreaker
* Pluggable vector stores: Chroma, Qdrant, and PGVector
* Document ingestion via Docling with OCR, automatic bibliographic metadata
  extraction (DOI resolution through Crossref, OpenAlex, and Semantic Scholar),
  and domain-scoped metadata filters

**Agent (work in progress)**

* General-purpose research agent for literature review, hypothesis generation,
  planning, coding, pipeline execution, and analysis
* General and Scientific operating modes, with chat-versus-task routing and a
  planner -- Scientific mode awaits a curated knowledge source
* A human plan-review step, currently auto-approved; interactive pause/resume
  is pending
* Capabilities supplied by MCP tools rather than hard-coded, so the agent
  extends as tools are added
* Tool access levels (``read_only`` / ``full``) and sandboxed command execution
  with a wall-clock backstop

**Interfaces and models**

* CLI, FastAPI server, NiceGUI web UI, Streamlit, and TUI
* Bring-your-own LLM: OpenAI-compatible, Anthropic, HuggingFace, and custom
  endpoints, with runtime model switching and prompt caching
* NeuroML MCP tools: model validation, OSB and NeuroML-DB lookups, web search,
  and sandboxed code execution

Quickstart
----------

New to Klea? Start with installation and the end-to-end RAG guide:

* :doc:`install` -- install Klea (PyPI or from source, with optional extras)
* :doc:`guides/create-and-use-rag` -- build your first vector store, configure a domain, and query it

Prototype Deployments
---------------------

These prototype Klea RAG deployments are available on HuggingFace that use the web interface.

- `NeuroML RAG <https://huggingface.co/spaces/NeuroML/NeuroKLEA>`__
- `OpenWorm RAG <https://huggingface.co/spaces/sanjayankur31/OpenWormLLM>`__

Please note that there are limited resources/credits available for these
prototypes, and so they may fall over if there is too much activity.
They are not production deployments.

Architecture
------------

Klea is a monorepo of four installable packages: ``klea_utils`` (the shared
framework), ``klea_rag`` (the RAG pipeline), ``klea_agent`` (the research
agent), and ``neuroml_mcp`` (the NeuroML MCP server).  See
:doc:`components/index` for what each component does and how they fit
together.

Funding
-------

Klea is funded by the `BioFAIR <https://biofair.uk/>`_ Pathfinder
Projects grant `"Creating AI-enabled analysis pipelines for FAIR
neuroscience data"
<https://biofair.uk/updates/2026/biofair-pathfinder-projects-launch-with-800k-to-transform-uk-fair-practices/>`_,
awarded to `Padraig Gleeson
<https://profiles.ucl.ac.uk/11654-padraig-gleeson>`_ and `Ankur Sinha
<https://profiles.ucl.ac.uk/77575-ankur-sinha>`_ at `University College
London <https://openneuroai.org/>`_.

As part of this Pathfinder project, Klea is being tested for neuroscience
research, through the NeuroML-specific ``nml-mcp`` server and the curated
NeuroML vector stores.

Klea is developed and maintained by `Ankur Sinha
<https://profiles.ucl.ac.uk/77575-ankur-sinha>`_ (GitHub:
`@sanjayankur31 <https://github.com/sanjayankur31>`_) with contributions
from the NeuroML community (see `all-contributors
<https://github.com/NeuroML/klea#contributors>`_ and
:doc:`contributing`).

.. image:: _static/biofair-logo.png
   :alt: BioFAIR logo
   :class: biofair-logo
   :width: 30%
   :align: center

.. toctree::
   :caption: Usage
   :hidden:

   install
   concepts/index
   components/index
   guides/index
   cli/index
   glossary
   troubleshooting

.. toctree::
   :caption: Develop
   :hidden:

   contributing
   developer-info
   developer-guides/index
   api/index

.. toctree::
   :caption: Project
   :hidden:

   getting-help
   code-of-conduct
