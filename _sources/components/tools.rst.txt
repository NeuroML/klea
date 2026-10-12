Tools
=====

Klea's graphs call *tools* supplied by MCP servers (see :doc:`mcp-servers`).
The bundled common tools below ship with the agent, which auto-launches them
with no setup; a RAG deployment opts in per domain, and other MCP servers add
their own tools.  This page indexes the bundled tools and what each one does.

Each tool is either **read-only** or **destructive** (a standard MCP
annotation).  Under the ``read_only`` tool-access level only the read-only
tools are offered; the destructive tools require ``full`` access.  Some tools
also reach the network (marked *open world*).  See :doc:`mcp-servers` for the
access model and :doc:`agent` for how the agent uses it.

Read-only tools
---------------

These only read; they are available at every access level.

.. list-table::
   :header-rows: 1
   :widths: 22 58 20

   * - Tool
     - What it does
     - Tags
   * - ``web_fetch``
     - Read a web page or document from a URL and return its text.
     - ``web``
   * - ``web_search``
     - Search the web and return ranked links with snippets.  Works with no
       setup; optional provider API keys raise rate limits (see
       :doc:`../install`).
     - ``web``, ``search``
   * - ``search_papers``
     - Search academic papers and return title, authors, year, journal,
       abstract, DOI and link, with a flag for peer reviewed or preprint.
       Works with no setup; an optional Semantic Scholar key adds one more
       source (see :doc:`../install`).
     - ``web``
   * - ``list_files``
     - List files and directories in the project.
     - ``local``, ``files``
   * - ``find_files``
     - Find files by name or path pattern across the project.
     - ``local``, ``files``
   * - ``read_file``
     - Read a file as text (documents such as PDF are converted to Markdown);
       large files are read a page at a time.
     - ``local``, ``files``
   * - ``grep``
     - Search the contents of the project's files with a regular expression.
     - ``local``, ``files``

Destructive tools
-----------------

These change the workspace, so they are offered only under ``full`` access and
are never run in ``read_only`` mode.

.. list-table::
   :header-rows: 1
   :widths: 22 58 20

   * - Tool
     - What it does
     - Tags
   * - ``download_file``
     - Download a URL and save it to a file on disk.
     - ``web``, ``download``
   * - ``write_file``
     - Create a file, or replace one entirely with new content.
     - ``local``, ``files``
   * - ``edit_file``
     - Make a targeted change to an existing file.
     - ``local``, ``files``
   * - ``run_command``
     - Run a shell command and return its output and exit status.
     - ``local``, ``code``

.. note::

   Every bundled tool also carries the ``bundled`` tag, so a deployment can
   expose or hide the whole set with a single tag filter, or a subset by each
   tool's other tags.  See :doc:`mcp-servers` for tag filtering and how to
   write your own tools.
