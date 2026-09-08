# AGENTS.md - Agent Package

General purpose agent (with coding capabilities) using LangChain/LangGraph.

## Package Overview

Package: `klea_agent`
CLI entry: `klea`, `klea-serve`

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

# Run tests with verbose output
pytest -v
```

## Architecture

### Package Structure
```
klea_agent/
├── api/             # Agent API contract (own payload + routers; ADR-0031)
│   ├── main.py      # FastAPI app creation
│   ├── chat.py      # ChatPayload (incl. operating-mode request) + /query[/stream]
│   ├── server.py    # Typer serve command
├── config.py        # Configuration loading (env file + JSON)
├── klea_agent.py    # Main Agent orchestrator (extends BaseLangGraph); wires the
│                    #   mode branch (ADR-0030), overrides context_snapshot for the
│                    #   graph-level ``context`` event (ADR-0032)
├── nodes/           # LangGraph nodes for agent workflows
│   ├── answer_user.py
│   ├── evaluator.py
│   ├── explore_planner.py
│   ├── goal_setter.py
│   ├── init_graph.py
│   ├── mode_router.py  # ModeDecision (resolves requested -> resolved mode) + ModeInformer
│   ├── planner.py
│   └── tools_router.py
│   └── prompts/     # Prompt markdown templates per node
├── schemas.py       # Pydantic schemas (incl. Mode: requested / resolved / note)
└── ui/
    ├── cli.py       # Typer CLI entry point (klea, klea-serve)
    └── web/         # NiceGUI page composition over klea_utils components (ADR-0031)
        ├── app.py   # process entry point
        ├── mode_ui.py # operating-mode selector + resolved-mode badge (status-pane slot)
        └── page.py  # setup_layout -- composes the shared components
```

The bundled tools server is shared: it lives in `klea_utils.mcp.server`
(`bundled.py` + `bundled_tools.py`), auto-launched by the agent as a stdio
subprocess (`python -m klea_utils.mcp.server.bundled`), and also runnable
standalone via the `klea-mcp` CLI. The tool picker/caller nodes are the
shared `klea_utils.nodes.tools_picker` / `klea_utils.nodes.tools_caller`.
```

### Key Technologies
- LangChain/LangGraph for agent orchestration
- FastMCP for MCP tool integration
- Typer for CLI
- NiceGUI/Streamlit UI shared from klea_utils

## Code Style

### File Organization
- **Header**: All Python files should have a copyright header
- **Docstrings**: Use reStructuredText format
- **Module structure**: `__init__.py` files should be minimal or empty

### Import Conventions
```python
# 1. Standard library imports
import asyncio
import os
from typing import Any, Dict, List

# 2. Third-party imports
from langchain_core.messages import HumanMessage, AIMessage
from fastmcp import Context
from pydantic import BaseModel

# 3. Local imports
from klea_agent.schemas import GoalSchema
from klea_agent.nodes import Planner
```

### Naming Conventions
- **Functions**: snake_case (`execute_node`, `validate_neuroml_model`)
- **Classes**: PascalCase (`KleaAgent`, `LangGraphAgent`)
- **Variables**: snake_case (`model_name`, `code_output`)
- **Constants**: UPPER_CASE (`DEFAULT_MODEL`, `MAX_RETRIES`)
