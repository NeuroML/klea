MCP utilities
=============

Shared machinery for building MCP servers and clients used by Klea apps:
metadata schemas, tool registration, an httpx session lifespan, path
permission checks, and the reusable bundled tools server.

Schemas
-------

.. automodule:: klea_utils.mcp.schemas
   :members:
   :show-inheritance:

Errors
------

.. automodule:: klea_utils.mcp.errors
   :members:
   :show-inheritance:

Tool registration
-----------------

.. automodule:: klea_utils.mcp.registry
   :members:
   :show-inheritance:

Tool call dispatch
------------------

.. automodule:: klea_utils.mcp.dispatch
   :members:
   :show-inheritance:

HTTP session lifespan
---------------------

.. automodule:: klea_utils.mcp.lifespan
   :members:
   :show-inheritance:

Shared tool implementations
---------------------------

Framework-agnostic tool bodies that apps wrap into FastMCP tools, passing
their httpx session via the lifespan context.

.. automodule:: klea_utils.mcp.tool_impls.permission
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.session
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.ssrf
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.web_fetch
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.list_files
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.read_file
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.download_file
   :members:
   :show-inheritance:

Repository sources
------------------

Framework-agnostic functions that list the versions and files of archival
repositories (GitHub, FigShare, DANDI Archive, BioModels).  The returned
file lists carry direct ``download_url`` values that can be fed to
:func:`klea_utils.mcp.tool_impls.download_file.download_files` (or the
single-file ``download_file`` implementation), e.g. by wrapping them into
MCP tools.

.. automodule:: klea_utils.mcp.tool_impls.repositories.sources
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.repositories.errors
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.repositories.github
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.repositories.figshare
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.repositories.dandi
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.tool_impls.repositories.biomodels
   :members:
   :show-inheritance:

Bundled tools server
--------------------

The bundled tools server (auto-launched by apps as a stdio subprocess and
exposed standalone via the ``klea-mcp`` CLI) and its configuration.

.. autoclass:: klea_utils.mcp.server.config.BundledToolsConfig
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.server.bundled
   :members:
   :show-inheritance:

.. automodule:: klea_utils.mcp.server.bundled_tools
   :members:
   :show-inheritance:
