Writing MCP tools
=================

This guide covers how Klea expects MCP tools to be written: the
*docstring-first* description convention, the ``isError`` contract, and
guidance on description length and style.  For configuring MCP servers in
a deployment see :doc:`../components/mcp-servers`; for the filesystem
permission model see the MCP permissions notes in the development
documentation.

Docstring-first convention
--------------------------

Klea expects MCP tool functions to follow a *docstring-first* convention,
so that the LLM-facing description stays concise and parameter details are
available in the tool schema:

1. **Tool description** -- the opening text block of the function
   docstring (what the tool does, when to use / not use it, and one short
   example).  Keep this block focused; it is what the tool picker shows
   the LLM.

2. **Parameters** -- describe each parameter in a Google-style ``Args:``
   section.  fastmcp parses these into the tool's input schema, and Klea
   renders them as compact one-line parameter entries (name, type,
   required/optional, default, description).  Optionality is always stated
   explicitly and a non-null schema default is shown (for example
   ``pattern (string, optional, default "*")``), so the tool picker does not
   have to infer it.

3. **Do not** set the tool description to the raw full docstring.  Klea
   shows the opening text block as the description and the
   ``Args:``-derived schema as the parameter list, so duplicating the
   ``Args:``/``Returns:`` prose in the description wastes prompt tokens.

4. **Return value** -- framework-agnostic implementation helpers
   (``klea_utils.mcp.tool_impls.*``) return a plain ``dict`` that signals
   failure with a non-empty ``error`` field (or legacy ``Error``).  Every
   **FastMCP wrapper** (functions decorated with ``@tool_meta``) must return
   ``ToolResult`` via ``klea_utils.mcp.tool_result.to_result`` so the MCP
   ``CallToolResult.isError`` flag is set correctly while preserving the
   full ``structured_content`` for the LLM to repair.  Returning a bare
   ``dict`` or ``str`` always yields ``isError: false`` (see
   ``fastmcp/tools/base.py:convert_result``).

Framework-agnostic implementation (not an MCP tool -- reused in tests and
direct Python calls):

.. code-block:: python

   # klea_utils/mcp/tool_impls/list_files.py -- plain dict, tested directly
   def list_files(path: str, pattern: str = "*") -> dict:
       """List files with optional pattern filtering.

       Args:
           path: Directory to list.
           pattern: Glob pattern.

       Returns:
           Dictionary with matching files, truncated flag, and error string
           (empty on success, non-empty on permission / not-found failure).
       """
       ...

FastMCP wrapper -- the only form registered with ``@tool_meta`` and exposed
to clients (MCP spec ``server/tools#Error Handling``):

.. code-block:: python

   from fastmcp import Context
   from fastmcp.tools import ToolResult
   from klea_utils.mcp.registry import tool_meta
   from klea_utils.mcp.schemas import ToolInfo
   from klea_utils.mcp.tool_result import to_result
   from klea_utils.mcp.tool_impls.list_files import list_files as list_files_impl

   @tool_meta(ToolInfo(title="List files and directories", tags={"local", "files"}, checkpaths=["path"], read_only=True))
   async def list_files(path: str, pattern: str = "*") -> ToolResult:
       """List files and directories with filtering and metadata.

       Use this tool to explore the local file system structure and find
       specific files.

       Use when:
       - Discovering what files exist in the working directory.
       - Finding files by name, type, or location.

       Do not use for:
       - Reading a file's contents (use the read file tool instead).

       Example: list_files(path=".", pattern="*.py", recursive=True)

       Args:
           path: Directory path to list.
           pattern: Space separated file patterns to filter files by type.

       Returns:
           ToolResult with structured_content dict (matching files, truncated
           flag) and error field; isError is true exactly when error is
           non-empty.  Preserve the dict shape so the LLM can remediate
           (vs raising ToolError which would strip structured_content).
       """
       result: dict = list_files_impl(path=path, pattern=pattern)
       return to_result(result)

Example with web data (same pattern -- ``Error`` is also honoured for legacy
repository tools, see ``klea_utils.mcp.tool_result.to_result``):

.. code-block:: python

   @tool_meta(ToolInfo(title="Find models on NeuroML-db"))
   async def get_models_from_neuromldb(
       ctx: Context, search_query: str, num: int = 3, download: bool = False
   ) -> ToolResult:
       """Search and optionally download cell and ion channel models from
       NeuroML-DB.

       Use this tool when you need example cell or ion channel models, or
       want to download models for local use.

       Use when:
       - Finding example cell and ion channel models.
       - Downloading models for use in your project.

       Do not use for:
       - Creating or editing NeuroML models (use the model template tool
         instead).
       - Running simulations (use the simulation tools instead).

       Example: get_models_from_neuromldb(search_query="cerebellum", download=True)

       Args:
           search_query: search term for querying NeuroML-DB.
           num: number of search results to get (clamped to 1-20).
           download: set to true to also download the models.

       Returns:
           ToolResult whose structured_content is the model dictionary (with
           an error field on failure); isError is true exactly when that
           error field is non-empty.
       """
       if not search_query.strip():
           return to_result({"Error": "search_query must be a non-empty string"})
       models: dict = await _search_neuromldb(search_query, num, download)
       return to_result(models)

Tool functions are registered by the ``@tool_meta`` decoration (see
``klea_utils.mcp.registry.register_tools``): any ``@tool_meta(ToolInfo(
title=..., tags=...))``-decorated function in a registered module becomes
a tool named after the function.  Helper functions without the decoration
are ignored.  Validation constraints (e.g. ``Field(min_length=1)``) may be
added to parameter annotations and are preserved in the schema.

Error handling (isError)
------------------------

The `MCP specification <https://modelcontextprotocol.io/specification/2025-06-18/server/tools#Error%20Handling>`_
distinguishes *protocol errors* (``error: {code, message}``) from *tool
execution errors* (``result: {content: [TextContent(error)], isError: true}``
for API failure / invalid input / business logic).  Klea's
``error``/``Error`` field is the second category and **must** be
``isError: true``.  ``klea_utils.mcp.tool_result.to_result`` bridges the
two: it preserves the full ``dict`` as ``structured_content`` (so the LLM
keeps ``columns``, ``rows``, ``known tables`` hints for repair) while
marking ``is_error`` from the trimmed ``error``/``Error`` string.  Raising
``ToolError`` would strip ``structured_content`` and ``return {"error":
"..." }`` as a bare ``dict`` would always yield ``isError: false`` via
``fastmcp/tools/base.py:convert_result``.

Clients that consume results -- ``klea_utils.tools.textualize_tool_results``
(``if result.is_error: **Error:**``) and
``klea_utils.nodes.tools_caller.ToolsCallerNode`` / ``klea_utils.mcp.dispatch``
(``success_count = sum(not r.is_error)``; permission denials via
``_denied_result(is_error=True)``) -- strictly respect the wire
``is_error`` flag, not the dict's ``error`` string.  Third-party servers
that still return a bare ``dict`` with ``{"error": "..."}`` will appear as
success (a code block) until they return a properly structured
``ToolResult`` -- via ``klea_utils.mcp.tool_result.to_result`` for Klea
servers or any equivalent ``ToolResult(content=[...], is_error=True)``
construction.  This visibility is intentional per ADR-0003; the server
must be fixed rather than papered over client-side.  See
``devdocs/adr/0003-mcp-iserror-compliance.md`` on GitHub for options
considered and verification.

The wrapper contract in short:

* Impl ``klea_utils.mcp.tool_impls.*``: ``-> dict`` (with ``error: ""`` on
  success) -- testable without MCP.
* Wrapper ``@tool_meta(ToolInfo(...))``: ``-> ToolResult``, ``return
  to_result(dict)`` on every path (success and failure) -- the only path
  that sets ``isError: true`` without losing ``structured_content``.

Tool description length and style
---------------------------------

The tool description is what the model uses to decide which tool to call,
so it is worth writing carefully.  A good rule of thumb is that a
description should read like a short, structured "how to use this tool"
note: roughly 100-250 tokens per tool.  This keeps the whole tool list
small enough to scale while still giving smaller models the guidance they
need to pick the right tool.  Sources consulted:

* **Anthropic** -- "Define tools"
  (https://docs.claude.com/en/docs/agents-and-tools/tool-use/define-tools)
  calls the description "by far the most important factor in tool
  performance" and recommends *at least 3-4 sentences* covering what the
  tool does, when it should (and should not) be used, what each parameter
  means, and any important caveats or limitations.  Descriptions are input
  tokens, so they count against the context window on every request.

* **OpenAI** -- "Function calling"
  (https://platform.openai.com/docs/guides/function-calling) recommends
  clearly describing the purpose of each function and parameter and
  including examples and edge cases, and suggests shortening descriptions
  when under token pressure and keeping fewer than ~20 tools available at
  once.

* **MCP specification** -- "Tools"
  (https://modelcontextprotocol.io/specification/2025-06-18/server/tools)
  defines ``description`` as a "human-readable description of
  functionality" and gives no length guidance, so the length is up to the
  server author.

* **opencode** (an open-source agentic coding tool,
  https://github.com/sst/opencode) keeps its core tool descriptions terse
  (~50-125 tokens) but ships richer, structured descriptions for its CLI
  tools (~100-600 tokens) that open with a one-sentence summary and use
  "Use when" / "Do NOT use" bullet sections and examples.  Its guidance:
  *"Keep the tool description concise; the full schema documentation
  remains in the signature."*

Klea's docstring-first convention (above) is a middle ground that keeps
descriptions small enough to scale:

* One-sentence summary on the first line.
* A short "Use this tool to ..." sentence.
* "Use when:" and "Do not use for:" bullet sections with cross-tool
  pointers, so smaller models can pick the right tool.
* One concrete "Example:" line.
* Parameter descriptions in a Google-style ``Args:`` section (parsed into
  the schema by fastmcp) rather than in the description.
* No long procedural prose (template structure, next steps, error
  handling, performance notes): that adds tokens without helping tool
  selection.

Reusable template
^^^^^^^^^^^^^^^^^

Copy this template when implementing a new tool; fill in the placeholders
and keep the whole block to roughly 100-250 tokens.  The wrapper must
return ``ToolResult`` via ``to_result`` (see above) -- never a bare
``dict`` or ``str``:

.. code-block:: python

   from fastmcp.tools import ToolResult
   from klea_utils.mcp.tool_result import to_result

   @tool_meta(ToolInfo(title="<Human title>", tags={"<domain>"}))
   async def <tool_name>(<param>: <type>) -> ToolResult:
       """<One-sentence summary of what the tool does>.

       Use this tool to <primary purpose>.

       Use when:
       - <case where this tool is the right choice>
       - <another concrete case>

       Do not use for:
       - <case better handled elsewhere> (use the <role> tool instead)

       Example: <tool_name>(<param>=<value>)

       Args:
           <param>: <what the parameter means and how it affects behaviour>.
           <param2>: <description>. Defaults to <default> if not specified.

       Returns:
           ToolResult with structured_content dict and error field; isError
           is true when error is non-empty.
       """
       result: dict = <impl_call>(<param>=<param>)
       return to_result(result)

Use generic role references ("use the file reading tool") rather than
exact tool names in the "Do not use for" pointers, because fastmcp
prefixes tool names with the server name (``NeuroML_list_files``) and
those prefixes vary between deployments.

.. seealso::

   * :doc:`../components/mcp-servers` -- configuring MCP servers and
     tool access levels in a deployment
   * :doc:`../concepts/mcp` -- the MCP standard itself
