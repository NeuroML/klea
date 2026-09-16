Components
==========

Klea is assembled from a small set of components, published as four
installable packages in the monorepo.  This section describes how each one
works and how they fit together; for the general ideas behind them, see
:doc:`../concepts/index`.

.. list-table::
   :header-rows: 1

   * - Directory
     - Package
     - CLI
     - Purpose
   * - ``utils_pkg``
     - ``klea_utils``
     - ``klea-stores-create``
     - Shared framework, utilities, vector store management
   * - ``rag_pkg``
     - ``klea_rag``
     - ``klea-rag``, ``klea-rag-serve``
     - Generic RAG pipeline with multi-domain support
   * - ``agent_pkg``
     - ``klea_agent``
     - ``klea``, ``klea-serve``
     - General-purpose research agent (coding, workflows, analysis, hypotheses)
   * - ``mcp_pkg``
     - ``neuroml_mcp``
     - ``nml-mcp``
     - MCP server for NeuroML tooling

.. toctree::
   :maxdepth: 1

   rag
   mcp-servers
   web-ui
   agent
   utils
