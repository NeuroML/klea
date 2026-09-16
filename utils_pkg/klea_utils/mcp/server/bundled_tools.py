#!/usr/bin/env python3
"""
FastMCP tool wrappers for the shared Klea bundled tools.

File: klea_utils/mcp/server/bundled_tools.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Annotated

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import Field

from klea_utils.mcp.registry import tool_meta
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.tool_impls.download_file import download_file as download_file_impl
from klea_utils.mcp.tool_impls.grep import grep as grep_impl
from klea_utils.mcp.tool_impls.list_files import list_files as list_files_impl
from klea_utils.mcp.tool_impls.read_file import read_file as read_file_impl
from klea_utils.mcp.tool_impls.run_command import (
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_MAX_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_ENV_VAR,
)
from klea_utils.mcp.tool_impls.run_command import run_command as run_command_impl
from klea_utils.mcp.tool_impls.web_fetch import web_fetch as web_fetch_impl
from klea_utils.mcp.tool_result import to_result

#: Common tags carried by every bundled tool, so "enable the common set"
#: is a single `include_tags: ["bundled"]` in the app config.
BUNDLED_TAG = "bundled"


@tool_meta(ToolInfo(tags={BUNDLED_TAG, "web"}, read_only=True))
async def web_fetch(
    ctx: Context,
    url: Annotated[str, Field(min_length=1)],
    timeout: Annotated[float, Field(ge=1.0, le=120.0)] = 30.0,
    max_chars: Annotated[int, Field(ge=1, le=1_000_000)] = 100_000,
) -> ToolResult:
    """Fetch a URL and return its text content.

    Use this tool to read web pages, docs, or other HTTP resources.

    Use when:
    - Reading a page or document from the web.
    - Checking a URL that a user or another tool referenced.

    Do not use for:
    - Downloading a file to disk (use the download file tool instead).

    Example: web_fetch(url="https://example.com")

    Args:
        url: HTTP or HTTPS URL to fetch.
        timeout: Request timeout in seconds.
        max_chars: Maximum number of characters of content to return.

    Returns:
        Dictionary with url, status_code, content_type, content, truncated, error.
    """
    session = ctx.lifespan_context.get("http_session")
    result = await web_fetch_impl(
        session=session,
        url=url,
        timeout=timeout,
        max_chars=max_chars,
    )
    return to_result(result)


@tool_meta(
    ToolInfo(tags={BUNDLED_TAG, "local", "files"}, checkpaths=["path"], read_only=True)
)
async def list_files(
    path: Annotated[
        str,
        Field(
            description=(
                "Directory path to list. Must be relative to current working "
                "directory and cannot contain '..' for security"
            ),
            min_length=1,
        ),
    ],
    max_depth: Annotated[
        int | None,
        Field(description="Maximum directory depth to traverse. 'None' for unlimited"),
    ] = None,
    pattern: Annotated[
        str,
        Field(
            description=(
                """
                Space separated file patterns to filter based on files type.
                Correct: '*.py'
                Correct: '*.md'
                Correct: '*.py *.md'
            """
            )
        ),
    ] = "*",
    include_files: Annotated[
        bool, Field(description="Whether to include files in results")
    ] = True,
    include_directories: Annotated[
        bool, Field(description="Whether to include directories in results")
    ] = True,
    recursive: Annotated[
        bool, Field(description="If True, traverse subdirectories recursively")
    ] = False,
    max_results: Annotated[
        int, Field(description="Maximum number of entries to return", ge=1, le=10000)
    ] = 100,
) -> ToolResult:
    """List files and directories with filtering and metadata.

    Use this tool to explore the local file system structure and find
    specific files.

    Use when:
    - Discovering what files exist in the working directory.
    - Finding files by name, type, or location.

    Do not use for:
    - Reading a file's contents (use the read file tool instead).

    Example: ``list_files(path=".", pattern="*.py", recursive=True)``

    Args:
        path: Directory path to list. Must be relative to the current working
            directory and cannot contain '..' for security.
        max_depth: Maximum directory depth to traverse. 'None' for unlimited.
        pattern: Space separated file patterns to filter files by type.
        include_files: Whether to include files in results.
        include_directories: Whether to include directories in results.
        recursive: If True, traverse subdirectories recursively.
        max_results: Maximum number of entries to return.

    Returns:
        Dictionary with list of files, truncated flag, and error.
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
    ToolInfo(tags={BUNDLED_TAG, "local", "files"}, checkpaths=["path"], read_only=True)
)
async def read_file(
    path: Annotated[
        str,
        Field(
            description=(
                "File path to read. Must be relative to current working "
                "directory and cannot contain '..' for security"
            ),
            min_length=1,
        ),
    ],
    offset: Annotated[
        int,
        Field(description="1-indexed line to start reading from", ge=1),
    ] = 1,
    limit: Annotated[
        int | None,
        Field(description="Maximum number of lines to return. 'None' for end of file"),
    ] = 2000,
    max_chars: Annotated[
        int,
        Field(description="Hard cap on characters of content to return", ge=1),
    ] = 100_000,
) -> ToolResult:
    """Read a file and return a slice of its text content.

    Use this tool to inspect source files, logs, or documents as plain text.
    Document formats (PDF, office files) are converted to Markdown first.

    Use when:
    - You need to see the contents of a file in the project.
    - You want to page through a large file by line numbers.

    Do not use for:
    - Listing a directory (use the list files tool instead).
    - Fetching remote content (use the web fetch tool instead).

    Example: read_file(path="README.md", offset=1, limit=100)

    Args:
        path: File path to read. Must be relative to the current working
            directory and cannot contain '..' for security.
        offset: 1-indexed line to start reading from.
        limit: Maximum number of lines to return. None reads to the end.
        max_chars: Hard cap on characters of content to return.

    Returns:
        Dictionary with content, line range, total_lines, truncated, error.
    """
    result = read_file_impl(
        path=path,
        offset=offset,
        limit=limit,
        max_chars=max_chars,
    )
    return to_result(result)


@tool_meta(
    ToolInfo(tags={BUNDLED_TAG, "local", "files"}, checkpaths=["path"], read_only=True)
)
async def grep(
    pattern: Annotated[str, Field(min_length=1)],
    path: Annotated[
        str,
        Field(
            description=(
                "Directory to search. Must be relative to the current working "
                "directory and cannot contain '..' for security"
            ),
            min_length=1,
        ),
    ] = ".",
    include: Annotated[
        str | None,
        Field(
            description=(
                "Space separated glob patterns to restrict which files are "
                "searched, e.g. '*.py' or '*.py *.md'. Omit to search all files"
            )
        ),
    ] = None,
    case_sensitive: Annotated[
        bool, Field(description="Whether the search is case sensitive")
    ] = True,
    include_ignored: Annotated[
        bool,
        Field(
            description=(
                "Also search files ignored by .gitignore. Default false, which "
                "skips ignored files (and skips .git/cache directories always)"
            )
        ),
    ] = False,
    max_results: Annotated[
        int,
        Field(description="Maximum number of matching lines to return", ge=1, le=1000),
    ] = 100,
) -> ToolResult:
    """Search file contents for lines matching a regular expression.

    Use this tool to find where a symbol, string, or pattern occurs in the
    files under a directory.

    Use when:

    - Locating a definition, call site, or error message in the codebase.
    - Searching the contents of many files without reading each one.

    Do not use for:

    - Listing files by name without searching their contents (use the list
      files tool instead).
    - Reading a whole file (use the read file tool instead).

    Example: ``grep(pattern="def main", include="*.py")``

    Args:
        pattern: Regular expression to search for.
        path: Directory to search, relative to the project directory.
        include: Space separated glob patterns restricting which files are
            searched.
        case_sensitive: Whether the search is case sensitive.
        include_ignored: Also search .gitignore-ignored files.
        max_results: Maximum number of matching lines to return.

    Returns:
        Dictionary with pattern, path, matches (path, line_number, line),
        truncated, files_scanned, error.
    """
    result = await grep_impl(
        pattern=pattern,
        path=path,
        include=include,
        case_sensitive=case_sensitive,
        include_ignored=include_ignored,
        max_results=max_results,
    )
    return to_result(result)


@tool_meta(
    ToolInfo(
        tags={BUNDLED_TAG, "web", "download"},
        checkpaths=["file_path"],
        destructive=True,
        open_world=True,
    )
)
async def download_file(
    ctx: Context,
    url: Annotated[str, Field(min_length=1)],
    file_path: Annotated[str, Field(min_length=1)],
) -> ToolResult:
    """Download a URL to a local file.

    Use this tool to fetch binary or text resources from the web and save
    them to disk for later reading or processing.

    Use when:
    - Downloading a file such as a PDF, dataset, or archive.
    - Saving remote content locally before inspecting it.

    Do not use for:
    - Reading a web page as text (use the web fetch tool instead).

    Example: download_file(url="https://example.com/paper.pdf", file_path="paper.pdf")

    Args:
        url: HTTP or HTTPS URL to download.
        file_path: Destination path, relative to the working directory.
            Existing files are overwritten.

    Returns:
        Dictionary with the saved path, or an error on failure.
    """
    session = ctx.lifespan_context.get("http_session")
    target = await download_file_impl(
        session=session,
        url=url,
        file_path=file_path,
    )
    if target is None:
        return to_result(
            {
                "error": "Download failed (check the URL, network, or file path permissions)."
            }
        )
    return to_result({"saved_to": str(target), "error": ""})


@tool_meta(
    ToolInfo(
        tags={BUNDLED_TAG, "local", "code"},
        checkpaths=["working_directory"],
        destructive=True,
        open_world=True,
    )
)
async def run_command(
    command: Annotated[
        str,
        Field(
            description=(
                "Shell command string to run. Pipes, '&&' and redirection are "
                "supported."
            ),
            min_length=1,
        ),
    ],
    working_directory: Annotated[
        str | None,
        Field(
            description=(
                "Directory to run the command in; must be inside the project "
                "directory. Defaults to the project directory."
            ),
        ),
    ] = None,
    timeout_seconds: Annotated[
        float,
        Field(
            description=(
                f"Seconds before the command is killed. Defaults to "
                f"{DEFAULT_TIMEOUT_SECONDS:g}; the maximum is set by the "
                f"{MAX_TIMEOUT_ENV_VAR} environment variable (default "
                f"{DEFAULT_MAX_TIMEOUT_SECONDS:g})."
            ),
            ge=1,
        ),
    ] = DEFAULT_TIMEOUT_SECONDS,
    max_output_chars: Annotated[
        int,
        Field(description="Maximum characters captured per output stream", ge=1),
    ] = DEFAULT_MAX_OUTPUT_CHARS,
) -> ToolResult:
    """Run a shell command and return its exit status and output.

    Use this tool to inspect or act on the workspace: print the working
    directory, list processes, run a build or test, or invoke a script.

    Use when:
    - You need a fact about the environment (working directory, host, date).
    - You need to run a build, test, or script in the project.

    Do not use for:
    - Reading a file (use the read file tool instead).
    - Listing a directory (use the list files tool instead).

    Example: run_command(command="pwd && ls", working_directory=".")

    Args:
        command: Shell command string to run.
        working_directory: Directory to run in; must be inside the project.
        timeout_seconds: Seconds before the command is killed.
        max_output_chars: Maximum characters captured per output stream.

    Returns:
        Dictionary with command, working_directory, returncode, stdout,
        stderr, truncated, error.
    """
    result = await run_command_impl(
        command=command,
        working_directory=working_directory,
        timeout_seconds=timeout_seconds,
        max_output_chars=max_output_chars,
    )
    return to_result(result)
