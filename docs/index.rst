Welcome to Klea
===============

Knowledge vaLidated Expert AI Assistant for scientific research.

Klea is a suite of AI tools for scientific research.  It provides a
general purpose agent with coding capabilities, a generic RAG pipeline,
and MCP servers for modelling and analysis.

Klea enforces groundedness: answers are drawn from retrieved sources and
checked by an evaluation loop, and each response records its provenance
(the documents and tools it drew on) so researchers can inspect and verify
the generated output.  When a query cannot be grounded in the available
sources, Klea flags the fallback rather than presenting it as confident
fact.

Architecture
------------

The project is organised as a monorepo with four installable packages:

.. list-table::
   :header-rows: 1

   * - Directory
     - Package
     - CLI
     - Purpose
   * - ``utils_pkg``
     - ``klea_utils``
     - ``klea-stores-create``
     - Shared utilities, vector store management, base graph classes
   * - ``rag_pkg``
     - ``klea_rag``
     - ``klea-rag``, ``klea-rag-serve``
     - Generic RAG pipeline with multi-domain support
   * - ``agent_pkg``
     - ``klea_agent``
     - ``klea``, ``klea-serve``
     - General purpose agent with coding capabilities
   * - ``mcp_pkg``
     - ``neuroml_mcp``
     - ``nml-mcp``
     - MCP server for NeuroML tooling

Each package is built on a shared foundation in ``klea_utils``, which
provides configurable LLM setup (with runtime model switching), vector
store abstraction (Chroma / PGVector / Qdrant), and the
:class:`~klea_utils.graph.base.BaseLangGraph` orchestrator framework.
Web interfaces are available via NiceGUI (primary) and Streamlit.

``klea_agent`` is the main application: a general purpose agent that can
also code.  ``klea_rag`` is an additional component, primarily consumed by
``klea_agent`` as a retrieval/RAG service.

C4 architecture model
---------------------

Klea is developed as a general-purpose RAG + agentic assistant for
scientific research and is not tied to any single domain.  The neuroscience
support comes from the domain-specific ``nml-mcp`` server and the curated
NeuroML vector stores.  The full C4 model (system context, containers,
components, deployment) is maintained as developer documentation: see
:doc:`developer-info`.

Quickstart
----------

New to Klea? Start with installation and the end-to-end RAG tutorial:

* :doc:`install` -- install Klea (PyPI or from source, with optional extras)
* :doc:`tutorials/create-and-use-rag` -- build your first vector store, configure a domain, and query it

Prototype Deployments
---------------------

These prototype Klea RAG deployments are available on HuggingFace that use the web interface.

- `NeuroML RAG <https://huggingface.co/spaces/NeuroML/NeuroKLEA>`__
- `OpenWorm RAG <https://huggingface.co/spaces/sanjayankur31/OpenWormLLM>`__

Please note that there are limited resources/credits available for these
prototypes, and so they may fall over if there is too much activity.
They are not production deployments.

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
   tutorials/index
   concepts/index
   cookbook/index
   cli/index
   glossary
   troubleshooting

.. toctree::
   :caption: Develop
   :hidden:

   contributing
   developer-info
   api/index

.. toctree::
   :caption: Project
   :hidden:

   getting-help
   code-of-conduct
