# AGENTS.md - Utils Package

Shared utilities for Klea packages.

## Package Overview

Package: `klea_utils`

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
pytest tests/test_utils.py

# Run tests with verbose output
pytest -v
```

## Architecture

### Package Structure
```
klea_utils/
├── api/            # FastAPI base layer: shared routers + chat plumbing
│   ├── app.py      # make_app() -- FastAPI factory with lifespan (graph + session store)
│   ├── chat_core.py # Shared chat plumbing (run_query, stream_response).  Apps own
│   │                 # their chat contract (own ChatPayload/router) via this (ADR-0031)
│   ├── health.py   # /health endpoint for readiness probes
│   ├── messages.py # message history CRUD per chat session
│   ├── models.py   # per-session runtime model switching endpoints
│   ├── server.py   # make_serve_app() -- Typer app wrapping uvicorn
│   ├── sessions_db.py # SQLite session store for chat persistence
│   ├── sessions.py # chat session CRUD (list, rename, delete)
│   ├── sse.py      # SSE streaming client (async gen for NiceGUI/TUI, sync for Streamlit)
│   └── utils.py    # URL validation, API readiness check
├── errors.py       # Custom exception classes
├── graph/          # LangGraph orchestrator base
│   └── base.py     # BaseLangGraph abstract class (setup, run, compile template);
│                   #   context_snapshot hook -> graph-level ``context`` stream
│                   #   events (ADR-0032)
├── llm.py          # LLM utilities: configurable models, provider introspection,
│                   #   model name parsing, three-layer config merge, HuggingFace
│                   #   auto-derivation, structured output fallback
├── nodes/          # Shared LangGraph node classes
│   ├── abstract.py # AbstractLangGraphNode, NodeStreamData (streaming contract)
│   ├── answer_general.py # General-purpose answer node (no retrieval needed)
│   ├── base.py     # LangGraphNode, LLMModel, _ConfigurableModel
│   ├── fixed_answer.py   # Node that returns a fixed/canned response
│   ├── guard.py    # GuardNode -- content safety check (skippable when model empty)
│   ├── guard_router.py   # GuardRouterNode -- routes based on guard_decision
│   └── summarise_memory.py # Memory summarisation node
│   └── prompts/    # Prompt markdown templates per node
├── paths.py        # platformdirs wrapper for OS-appropriate cache/data/config dirs
├── plogging.py     # Logging setup, mask_sensitive (API key masking), logfmt output
├── stores/         # Vector store management
│   ├── config.py   # Pydantic models for store configuration
│   ├── ingestion.py # Document ingestion pipeline (chunking, embedding, storage)
│   ├── retrieval/  # Retriever managers
│   │   ├── base.py # BaseKleaRetriever -- shared k-tracking and store loading
│   │   ├── vs.py   # VSRetriever -- vector store retrieval
│   │   └── bm25.py # BM25RetrieverManager -- BM25 keyword retrieval
│   └── utils.py    # Shared store helpers
├── tools.py        # MCP CallToolResult helpers (textualize content blocks)
├── ui/             # User interface frontends
│   ├── stores_create.py # CLI for store creation (klea-stores-create)
│   ├── tui/        # Textual/TUI chat client (repl.py)
│   └── web/        # Web frontends
│       ├── nicegui/   # NiceGUI shared components + process entry (ADR-0031)
│       │   ├── components/  # Reusable page pieces coordinated via PageContext:
│       │   │   ├── context.py   # PageContext (config, mutable state, element refs,
│       │   │   │                 #   callbacks; query_extra / status_extras slots)
│       │   │   ├── bootstrap.py # run_nicegui_server(page_builder=...) -- ui.run entry
│       │   │   ├── stream.py    # apply_stream_event (pure) + run_stream (SSE -> UI)
│       │   │   ├── status_pane.py # right drawer; app content slot (ctx.status_extras)
│       │   │   └── ... chat_bubble, chat_area, chat_list, header, inspector,
│       │   │         input_area, initial_load, model_dialog, storage, theme
│       │   ├── client.py  # server API client (sessions, models, chat)
│       │   ├── entry.py   # default_storage_env, app_name_from_argv
│       │   ├── parser.py  # argparse for the app entry point
│       │   └── state.py   # frontend chat/session state store
│       └── streamlit/ # Streamlit web UI
```

### Key Technologies
- FastAPI for REST/SSE API layer
- LangGraph for state machine orchestration
- LangChain-core for LLM abstractions (init_chat_model, configurable models)
- NiceGUI for reactive web UI (optional frontend)
- Streamlit for lightweight web UI (alternative frontend)
- HuggingFace for embeddings and inference endpoints
- Chroma / PGVector / Qdrant for vector store backends (URI-style config)
- httpx for async HTTP and SSE streaming
- tenacity for retry logic

## Code Style

### File Organization
- **Header**: All Python files should have a copyright header
- **Docstrings**: Use reStructuredText format
- **Module structure**: `__init__.py` files should be minimal or empty; do not add `__all__` re-exports -- import from the specific modules

### Import Conventions
```python
# 1. Standard library imports
import logging
from typing import Any, Dict, List

# 2. Third-party imports
from langchain_core.messages import BaseMessage
from langchain_huggingface import HuggingFaceEmbeddings
import httpx

# 3. Local imports
from klea_utils.plogging import setup_root_logger
from klea_utils.llm import get_default_model
```

### Naming Conventions
- **Functions**: snake_case (`setup_root_logger`, `get_default_model`)
- **Classes**: PascalCase (`NeuroMLLogger`, `LLMConfig`)
- **Variables**: snake_case (`model_name`, `api_key`)
- **Constants**: UPPER_CASE (`DEFAULT_MODEL`, `LOG_FORMAT`)

### Guidelines
- Utilities should be framework-agnostic where possible
- Provide sensible defaults but allow configuration
- Document environment variable dependencies
- Include type hints for all public functions
