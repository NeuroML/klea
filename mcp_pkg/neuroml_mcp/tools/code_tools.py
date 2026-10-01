#!/usr/bin/env python3
"""
General code execution tools.

Note that docstrings here should be written for the LLM to read.

File: neuroml_mcp/tools/code_tools.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from dataclasses import asdict
from typing import Annotated

from fastmcp.tools import ToolResult
from klea_utils.mcp.registry import tool_meta
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.tool_impls.list_files import list_files as list_files_impl
from klea_utils.mcp.tool_result import to_result
from pydantic import Field

from neuroml_mcp.tools.sandbox.sandbox import RunPythonCode

from .sandbox import nml_mcp_sandbox

# set the implementation for development
sbox = nml_mcp_sandbox


@tool_meta(
    ToolInfo(
        title="List files and directories",
        tags={"neuroml", "local", "files"},
        checkpaths=["path"],
        read_only=True,
    )
)
async def list_files(
    path: Annotated[str, Field(min_length=1)] = ".",
    max_depth: int | None = None,
    # LLMs are trained on shell style globs, so they insist on using space
    # separated file patterns. So we explicitly support these. Otherwise, this
    # becomes error prone.
    pattern: str = "*",
    include_files: bool = True,
    include_directories: bool = True,
    recursive: bool = False,
    max_results: Annotated[int, Field(ge=1, le=10000)] = 100,
) -> ToolResult:
    """List files and directories with filtering and metadata.

    Use this tool to explore the file system structure and find specific
    files.

    Use when:
    - You need to see what files and directories exist under a path.
    - You want to locate files matching a pattern (e.g. '*.py').

    Do not use for:
    - Reading the contents of a file (use the file reading tool instead).
    - Running commands or scripts (use the code execution tool instead).

    Example: list_files(path=".", pattern="*.py", recursive=True)

    Args:
        path: Directory path to list. Must be relative to the current working
            directory and cannot contain '..' for security. Defaults to the
            current directory ('.').
        max_depth: Maximum directory depth to traverse. 'None' for unlimited.
        pattern: Space separated file patterns to filter based on file type.
            Correct: '*.py', '*.md', '*.py *.md'.
        include_files: Whether to include files in results.
        include_directories: Whether to include directories in results.
        recursive: If True, traverse subdirectories recursively.
        max_results: Maximum number of entries to return.

    Returns:
        Dict with the matching files, an error message (if any), and a
        truncated flag.
    """
    result = list_files_impl(
        path=path,
        max_depth=max_depth,
        pattern=pattern,
        include_files=include_files,
        include_directories=include_directories,
        recursive=recursive,
        max_results=max_results,
    )
    return to_result(result)


@tool_meta(
    ToolInfo(
        title="Execute Python code",
        tags={"neuroml", "local", "code"},
        destructive=True,
    )
)
async def run_python_code(
    code: Annotated[str, Field(min_length=1)],
) -> ToolResult:
    """Execute Python code in a sandboxed environment.

    Use this tool to test code snippets, generate models, and perform
    calculations.

    Use when:
    - You need to run a short Python snippet to test or compute something.
    - You need to generate or manipulate NeuroML structures with code.

    Do not use for:
    - Simple file operations (use the file tools instead).
    - Long-running or interactive programs (the sandbox rejects these).

    Example: run_python_code("import numpy; print('numpy version:', numpy.__version__)")

    Args:
        code: Complete Python code to execute. Must be valid Python syntax
            and cannot require interactive input.

    Returns:
        Dict with the execution result.
    """
    request = RunPythonCode(code=code)
    async with sbox(".") as f:
        result = await f.run(request)
    data = asdict(result)
    if data.get("returncode") not in (0, None):
        data = {
            **data,
            "error": data.get("stderr")
            or f"Python code failed with returncode {data.get('returncode')}",
        }
    else:
        data = {**data, "error": ""}
    return to_result(data)
