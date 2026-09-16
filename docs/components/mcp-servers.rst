MCP servers
===========

Klea acts as an MCP client: MCP servers supply the tools its graphs can
call, from the bundled common tools to domain-specific servers such as
``nml-mcp``.  This page describes how Klea uses and configures those
servers.

How Klea uses MCP
-----------------

Klea acts as the MCP client: it uses MCP servers to give its LLMs access
to external tools (validation, file handling, model lookup, code
execution, etc.).  A domain in a RAG or agent config can declare one or
more MCP servers; the tools they expose are fetched at startup and made
available to the graph's tool picker, which decides which tools to call
for a given query.

Configuring MCP servers
-----------------------

Each :doc:`domain <rag>` lists the MCP servers it can use under
``mcp_servers``.  Each entry maps a server name to the URL of a
streamable HTTP MCP endpoint:

.. code-block:: json

   {
       "domains": {
           "NeuroML": {
               "mcp_servers": {
                   "NeuroML": {
                       "url": "http://127.0.0.1:8542/mcp"
                   }
               }
           }
       }
   }

See :doc:`../tutorials/create-and-use-rag` for a full example and the
:doc:`../cookbook/huggingface` cookbook for a deployed setup.

Adding your own MCP servers
---------------------------

Klea ships two reference servers to copy from:

* the bundled common tools server in ``klea_utils.mcp.server`` (web
  fetch, file handling, command execution), which applications
  auto-launch as a stdio subprocess; and
* the NeuroML server in the ``mcp_pkg`` package (``neuroml_mcp``),
  exposed by the :doc:`nml-mcp <../cli/nml-mcp>` CLI.

To add your own, implement an MCP server (any language or framework
works), start it, and point a domain's ``mcp_servers`` entry at it.  The
tool modules in ``mcp_pkg`` and the bundled server are the worked
examples of the pattern Klea expects.  Tag filters (below) let you
expose only part of a server's toolset.

Filtering tools by tag
----------------------

Klea lets a deployment enable or disable the tools of a configured MCP
server without editing any server code.  Each tool carries *tags*; the
RAG/agent config selects which tags to expose through fastmcp's
``include_tags`` / ``exclude_tags`` fields on a server entry.  A RAG domain
that only wants the query tools of a server (not its local file / code
tools) can filter by tag:

.. code-block:: json

   {
       "domains": {
           "NeuroML": {
               "mcp_servers": {
                   "NeuroML": {
                       "url": "http://127.0.0.1:8542/mcp",
                       "include_tags": ["web", "neuroml"],
                       "exclude_tags": ["code"]
                   }
               }
           }
       }
   }

``include_tags`` exposes only tools carrying at least one listed tag;
``exclude_tags`` hides any tool carrying a listed tag.  Both are optional
and can be combined.  When only ``exclude_tags`` is set, everything except
the excluded tags is enabled.  These fields also work for stdio servers
(the bundled tools server below).

The tag vocabulary
^^^^^^^^^^^^^^^^^^

Tags are used for **Klea's own config filtering** -- which domain's tools
to expose, and whether to allow local or web-facing tools.  Two groups:

* **Scope** tracks where a tool operates:
  ``local`` (filesystem / process on the host) or ``web`` (interacts with
  external URLs / web APIs).
* **Domain / functional** groups tools by purpose, for example ``files``,
  ``code``, ``download``, ``neuroml``, ``neuroml-db``, ``osb``.

Every tool also carries the ``bundled`` tag when it comes from the common
bundled server, so enabling the whole common set is a single
``include_tags: ["bundled"]``.  Specific current assignments::

   bundled  web_fetch, list_files, read_file, download_file, run_command (each also has its scope + functional tags)

   Web scope:   web_fetch (bundled), download_file (bundled, download)
   Local scope: list_files / read_file (bundled, files),
                run_command (bundled, code),
                run_python_code / run_lems_simulation (neuroml, code),
                create_new_NeuroML_model (neuroml)

Behavioral intent (whether a tool is read-only or destructive) is *not*
tagged.  That is carried by standard MCP tool annotations
(``readOnlyHint`` / ``destructiveHint``), which any compliant client can
enforce without knowing Klea's tag vocabulary.  Tags answer "which tools to
expose"; annotations answer "what effects the tool may have".

``run_command`` executes a shell command and is therefore marked
``destructive`` + ``open_world``: it is full-mode only (never offered or run
under ``read_only``, see below), and its optional ``working_directory``
argument is checked like any other path but does **not** confine the command
(a shell can ``cd`` elsewhere or use absolute paths).  Commands default to a
30-second timeout with a 600-second ceiling, overridable via
``KLEA_RUN_COMMAND_MAX_TIMEOUT``.  See ADR-0038 for the design and its
limits.

Klea-authored tools (this one included) also refuse to run when the server
process has root privileges (uid 0), unless the ``KLEA_ALLOW_ROOT_TOOLS``
environment variable is set.  This catches accidental root deployments
(containers default to root); third-party MCP servers are not covered and run
with whatever privileges they are given.

The bundled tools server
^^^^^^^^^^^^^^^^^^^^^^^^

Klea ships a set of common tools (web fetch, file list/read, download)
as a shared MCP server in ``klea_utils.mcp.server``.  Applications
auto-launch it as a stdio subprocess by default, so users get the common
tools with no extra setup; the same server can be run standalone over HTTP
via the ``klea-mcp`` CLI for remote deployments.

Whether the bundled server is used, and which of its tools are exposed, is
configured under ``general.bundled_tools``:

.. code-block:: json

   {
       "general": {
           "bundled_tools": {
               "enabled": true,
               "include_tags": ["web"],
               "exclude_tags": ["download"]
           }
       }
   }

The agent enables the bundled server by default (batteries included); the
RAG leaves it disabled by default, since each RAG deployment is domain
specific and should wire in only the tools it needs.

How Klea uses the tools
-----------------------

At startup, the graph connects to each configured MCP server, lists its
tools, and stores per-domain metadata.  The tool picker node then selects
the tools relevant to the current query, and the selected tools are
called during graph execution.  When several MCP servers are configured,
fastmcp prefixes tool names with the server name (e.g.
``NeuroML_list_files``) so tools from different servers stay
distinct; Klea keeps these prefixed names unchanged.

Each dispatched call also has a wall-clock **backstop** so a hung tool cannot
stall the graph: the default is 900 seconds, overridable with the
``KLEA_TOOL_CALL_TIMEOUT`` environment variable (``0`` disables it).  A call
that exceeds it returns a non-halting error and is cancelled; tools should
still enforce their own, tighter timeouts.

Tool access levels
------------------

Klea can restrict which tools may be invoked.  The level is either
``full`` (every tool) or ``read_only`` (only tools explicitly annotated
``readOnlyHint: true`` and not ``destructiveHint: true``; a tool with no
annotation is **not** permitted).  Disallowed tools are never shown to the
model, and a call to one is rejected before it reaches the MCP server with
a non-halting error the model can adapt to.  See ADR-0037 for the design
and its trust limits.

The default level is set in the app config:

.. code-block:: json

   {
       "general": {
           "access_level": "read_only"
       }
   }

The agent defaults to ``full`` and accepts a per-request ``access_level``
override in the chat API; the RAG is fixed at ``read_only`` (it only
retrieves).  In the agent's web UI the level is a per-chat selector in the
status pane (next to the operating mode), so it can be changed without
editing the request payload; the effective level is projected back through
the graph's ``context`` event.

Because annotations are self-reported, tools that declare nothing are
hidden under ``read_only``.  A deployment can declare the capability of a
trusted tool explicitly, which takes precedence over its annotations:

.. code-block:: json

   {
       "general": {
           "tool_access": {
               "my_server_search": {"read_only": true},
               "my_server_fetch": {"read_only": false, "destructive": true}
           }
       }
   }

This is a least-privilege guard for trustworthy tools, not a sandbox: a
server that misreports its annotations cannot be confined this way.  For
servers Klea does not author, run them under OS-level isolation (see the
MCP permissions notes in the development documentation).

.. seealso::

   * :doc:`../developer-guides/writing-mcp-tools` -- conventions for
     authoring Klea's own MCP tools
