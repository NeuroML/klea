# AGENTS.md - MCP Package

Model Context Protocol (MCP) server providing tools for NeuroML development.

## Package Overview

Package: `neuroml_mcp`
CLI entry: `nml-mcp`

## Development Commands

### Building and Installation
```bash
# Install in development mode
uv pip install -e .

# Install development dependencies
uv pip install -e .[dev]
```

### Linting and Formatting
```bash
# Run ruff for linting and fixing
ruff check . --fix
ruff format .

# Sort imports specifically
ruff check . --select I --fix
```

### Testing
```bash
# Run all tests
pytest

# Run a single test file
pytest tests/test_mcp.py

# Run a specific test function
pytest tests/test_mcp.py::test_tool_discovery

# Run tests with verbose output
pytest -v

# Run tests marked as local only (require LLM access)
pytest -m localonly

# Run tests excluding local only
pytest -m "not localonly"
```

## Architecture

### Package Structure
```
neuroml_mcp/
├── server/        # FastMCP server
│   ├── app_lifespan.py
│   └── main.py
├── tools/         # Auto-discovered tools
│   ├── code_tools.py     # Code execution tools
│   ├── neuroml_tools.py  # NeuroML model tools
│   ├── web_tools.py      # Web search tools
│   └── sandbox/           # Sandboxed code execution
│       ├── docker.py     # Docker sandbox
│       ├── local.py      # Local subprocess sandbox
│       └── sandbox.py    # Abstract sandbox base
└── utils.py
```

### Key Technologies
- FastMCP framework
- PyNeuroML for NeuroML handling
- Starlette for HTTP
- Sandbox isolation for code execution

### Tool Development
- Tools are functions decorated with `@tool_meta(ToolInfo(...))`; the
  function name becomes the tool name (a `_tool` suffix is optional)
- Use `@context.require()` decorator for dependencies
- Return structured data (dicts, Pydantic models) rather than raw strings
- **Docstring-first convention (Klea's expectation):** the tool's LLM-facing
  description is the docstring's *opening text block*, written as a
  one-sentence summary, a "Use this tool to ..." sentence, "Use when:" /
  "Do not use for:" bullet sections, and one example line (target ~100-250
  tokens). Parameter descriptions go in a Google-style `Args:` section,
  which fastmcp parses into the input schema. Do NOT include long prose
  sections (template structure, next steps, error handling, etc.) or an
  `Args:` dump in the description: Klea's tool picker shows the opening
  block as the description and the `Args:`-derived schema as compact
  parameter lines, so duplicated prose just wastes prompt tokens.
  Rationale and sources (Anthropic, OpenAI, MCP spec, opencode) plus a
  reusable template: `docs/developer-guides/writing-mcp-tools.rst`
  ("Tool description length and style").

### Sandbox Implementation
- Inherit from `AsyncSandbox` abstract base class
- Use `@singledispatchmethod` for handling different request types
- Implement proper security isolation for code execution
- Support both local and Docker-based execution environments
- Handle process timeouts and resource limits

## Code Style

### File Organization
- **Header**: All Python files must start with `#!/usr/bin/env python3` shebang
- **Copyright**: Follow with copyright format: `# Copyright 2026 Ankur Sinha <ankursinha@fedoraproject.org>`
- **Docstrings**: Use reStructuredText format with parameter and return type documentation
- **Module structure**: `__init__.py` files should be minimal or empty; do not add `__all__` re-exports -- import from the specific modules

### Import Conventions
```python
# 1. Standard library imports
import asyncio
import os
from typing import Any, Dict, List

# 2. Third-party imports
from fastmcp import ClientContext, Context
from pydantic import BaseModel
from starlette.applications import Starlette

# 3. Local imports
from neuroml_mcp.sandbox.local import LocalSandbox
from neuroml_mcp.utils import register_all_tools
```

### Naming Conventions
- **Functions**: snake_case with descriptive names (`create_new_NeuroML_model`)
- **Classes**: PascalCase (`LocalSandbox`, `RunCommand`)
- **Variables**: snake_case (`tool_context`, `sandbox_manager`)
- **Constants**: UPPER_CASE (`DEFAULT_TIMEOUT`, `MAX_RETRIES`)
- **Tool functions**: Marked with `@tool_meta` for auto-discovery (the
  `_tool` suffix is a naming convention, not a requirement)
- **Private functions**: Prefix with underscore (`_internal_helper`)

### Async/Await Patterns
- All sandbox operations should be async
- Use `async with` for context managers when available
- Implement proper async function signatures with `await`
- Handle async exceptions appropriately

## Security Considerations

### Sandbox Security
- Never execute untrusted code outside sandboxes
- Implement proper resource limits and timeouts
- Validate all inputs before execution
- Use read-only file systems when possible
- Monitor for suspicious activity patterns

### Sandbox Usage Pattern
```python
async with sandbox_manager.get_sandbox() as sandbox:
    result = await sandbox.execute_command(command, timeout=30)
    if result.return_code != 0:
        raise RuntimeError(f"Command failed: {result.stderr}")
```
