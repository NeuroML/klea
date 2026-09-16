Klea Utils
==========

``klea_utils`` is the shared foundation that every other Klea package is
built on.  It is published to PyPI, so its framework pieces can be reused
independently of the applications.

BaseLangGraph
-------------

At its core is :class:`~klea_utils.graph.base.BaseLangGraph`, a template
method for building LangGraph orchestrators.  ``setup()`` runs a fixed
sequence -- load configuration, set up models, configure resources,
connect the MCP client, load stores, compile the graph -- and subclasses
implement only the seams they need:

* ``_setup_models`` -- declare the model roles;
* ``_configure_resources`` -- declare MCP servers and knowledge stores;
* ``_create_graph`` -- define the nodes, edges and routing.

Execution (invoke, stream, structured event stream), session checkpointing
and per-request model overrides are provided by the base, so an
application gets them for free.  Both :doc:`rag` and :doc:`agent` are
subclasses.

Shared nodes and services
-------------------------

Alongside the base graph, ``klea_utils`` provides the pieces the
applications share:

* shared LangGraph nodes (guard, memory summarisation, tool picker and
  caller, general answering);
* configurable LLM setup with runtime model switching and provider
  handling;
* the vector and BM25 store abstraction (see :doc:`rag`) and the document
  ingestion pipeline;
* the FastAPI app factory, session store and SSE streaming;
* MCP tool implementations, the bundled tools server (see
  :doc:`mcp-servers`) and access-level enforcement;
* reusable NiceGUI components, a Streamlit UI and the TUI.

Interfaces
----------

The package also ships the ``klea-stores-create`` CLI for building vector
and BM25 stores, and the shared web UI components used by both
applications (see :doc:`web-ui`).

.. seealso::

   * :doc:`../api/index` -- the Python API reference
   * :doc:`../developer-info` -- architecture and the C4 model
   * :doc:`../contributing` -- development setup
