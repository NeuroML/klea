MCP
===

What is MCP?
------------

`Model Context Protocol (MCP)
<https://modelcontextprotocol.io/>`_ is an open standard that lets LLMs
interact with external tools and data through a structured interface.  An
**MCP server** exposes *tools*: named, schema-validated operations (e.g.
"search the model database", "validate a NeuroML file", "run code").  A
**tool call** is what happens when the model decides to use one of those
tools: the client asks the server to list its tools (``tools/list``), the
model picks the right one and supplies arguments, the client invokes it
(``tools/call``), and the tool result is returned to the model as context
for the next step.

How Klea uses it
----------------

Klea acts as an MCP client.  It configures MCP servers per domain, filters
their tools by tag, and enforces tool access levels; it also ships a
bundled tools server and the NeuroML-specific ``nml-mcp`` server.  See
:doc:`../components/mcp-servers` for how Klea configures and uses MCP
servers and how to write tools for Klea.
