#!/usr/bin/env python3
"""
FastMCP tool wrappers for the shared Klea bundled tools.

File: klea_utils/mcp/server/bundled_tools.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from typing import Annotated, Any

from fastmcp import Context
from fastmcp.tools import ToolResult
from pydantic import Field

from klea_utils.mcp.registry import tool_meta
from klea_utils.mcp.schemas import ToolInfo
from klea_utils.mcp.tool_impls.download_file import download_file as download_file_impl
from klea_utils.mcp.tool_impls.edit_file import edit_file as edit_file_impl
from klea_utils.mcp.tool_impls.find_files import find_files as find_files_impl
from klea_utils.mcp.tool_impls.grep import grep as grep_impl
from klea_utils.mcp.tool_impls.grep_hunks import (
    DEFAULT_CONTEXT_LINES,
    MAX_CONTEXT_LINES,
)
from klea_utils.mcp.tool_impls.list_files import list_files as list_files_impl
from klea_utils.mcp.tool_impls.papers.search import (
    DEFAULT_MAX_RESULTS as PAPERS_DEFAULT_MAX_RESULTS,
)
from klea_utils.mcp.tool_impls.papers.search import MAX_RESULTS as PAPERS_MAX_RESULTS
from klea_utils.mcp.tool_impls.papers.search import Domain
from klea_utils.mcp.tool_impls.papers.search import search_papers as search_papers_impl
from klea_utils.mcp.tool_impls.read_file import read_file as read_file_impl
from klea_utils.mcp.tool_impls.run_command import (
    DEFAULT_MAX_OUTPUT_CHARS,
    DEFAULT_MAX_TIMEOUT_SECONDS,
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_ENV_VAR,
)
from klea_utils.mcp.tool_impls.run_command import run_command as run_command_impl
from klea_utils.mcp.tool_impls.web_fetch import web_fetch as web_fetch_impl
from klea_utils.mcp.tool_impls.web_search import DEFAULT_MAX_RESULTS, MAX_RESULTS
from klea_utils.mcp.tool_impls.web_search import web_search as web_search_impl
from klea_utils.mcp.tool_impls.write_file import write_file as write_file_impl
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


@tool_meta(ToolInfo(tags={BUNDLED_TAG, "web"}, read_only=True, open_world=True))
async def search_papers(
    ctx: Context,
    query: Annotated[
        str,
        Field(
            description="Keywords to search for, e.g. 'hodgkin huxley'", min_length=1
        ),
    ],
    domain: Annotated[
        Domain,
        Field(
            description=(
                "'general' for all fields, or 'life-sciences' to search "
                "biomedical literature (Europe PMC, PubMed) first"
            )
        ),
    ] = "general",
    preprints: Annotated[
        bool,
        Field(
            description=(
                "Also search preprints (not peer reviewed). Set only when the "
                "user asks for preprints, or the peer reviewed results are "
                "not enough"
            )
        ),
    ] = False,
    max_results: Annotated[
        int,
        Field(
            description=(
                "Maximum number of peer reviewed results, and separately of "
                "preprint results"
            ),
            ge=1,
            le=PAPERS_MAX_RESULTS,
        ),
    ] = PAPERS_DEFAULT_MAX_RESULTS,
) -> ToolResult:
    """Search academic papers by keywords.

    Use this tool to find published research papers on a topic, with their
    title, authors, year, journal, DOI, link and abstract.  Peer reviewed
    papers are searched first; preprints are only included when
    ``preprints`` is true, and are marked with ``peer_reviewed: false``.

    Use when:
    - Finding papers or references on a scientific topic.
    - Looking for the source of a model, method or result.

    Do not use for:
    - Reading a paper's full text (use the web fetch or download tool).
    - General web pages, news or docs (use the web search tool instead).

    When you cite a preprint, tell the user it has not been peer reviewed.

    Example: search_papers(query="hodgkin huxley", domain="life-sciences")

    Args:
        query: Keywords to search for.
        domain: 'general' or 'life-sciences'.
        preprints: Also search preprints, listed after the peer reviewed results.
        max_results: Maximum results from the peer reviewed sources and preprints.

    Returns:
        Dictionary with query, domain, preprints, results (title, authors,
        year, journal, abstract, doi, url, peer_reviewed, source), error and
        note.
    """
    session = ctx.lifespan_context.get("http_session")
    result = await search_papers_impl(
        session=session,
        query=query,
        domain=domain,
        preprints=preprints,
        max_results=max_results,
    )
    return to_result(result)


@tool_meta(
    ToolInfo(tags={BUNDLED_TAG, "web", "search"}, read_only=True, open_world=True)
)
async def web_search(
    ctx: Context,
    query: Annotated[str, Field(min_length=1)],
    max_results: Annotated[int, Field(ge=1, le=MAX_RESULTS)] = DEFAULT_MAX_RESULTS,
) -> ToolResult:
    """Search the web and return ranked results with snippets.

    Use this tool to discover current information, documentation, or sources
    online, then read a chosen result with the web fetch tool.

    Use when:
    - You need to find information on the web (discovery).
    - You want links and snippets before fetching a page.

    Do not use for:
    - Reading the content of a specific URL (use the web fetch tool instead).
    - Downloading a file to disk (use the download file tool instead).

    Example: web_search(query="NeuroML specification")

    Args:
        query: Free-text search query.
        max_results: Maximum number of results to return (1-20).

    Returns:
        Dictionary with query, provider, results (list of title, url,
        snippet, published, score), and error.
    """
    session = ctx.lifespan_context.get("http_session")
    result = await web_search_impl(
        session=session,
        query=query,
        max_results=max_results,
    )
    return to_result(result)


#: Tag marking the tool as searching the deployment's document stores.
STORES_TAG = "stores"


def _stores_description() -> str:
    """Render the store-search description from the resolved configuration.

    The domain list is read from the stores configuration resolved by
    :func:`klea_utils.stores.utils.resolve_stores_config_path` so the model
    sees what it can search without first calling the tool in catalogue
    mode.  A domain is shown with its description when it has one, and as
    its bare key otherwise.  Any resolution or load failure (no
    configuration, missing file, invalid contents) leaves the domain section
    out, so the description stays usable and the tool reports the problem
    itself.

    :returns: The tool description.
    """
    description = (
        "Search the deployment's configured document stores for one domain.\n\n"
        "Use this tool to answer a question from the deployment's document "
        "collections: it queries the domain's vector and BM25 stores, fuses "
        "their results, re-ranks them and returns character-budgeted "
        "reference material.\n\n"
        "Use when:\n"
        "- Answering a question from the deployment's document collections.\n"
        "- Looking up material held in a vector store or BM25 corpus.\n\n"
        "Do not use for:\n"
        "- Reading a local file (use the read file tool instead).\n"
        "- General web pages (use the web search tool instead).\n\n"
        "Call it with mode 'catalogue' (or an empty domain) to list the "
        "domains with the filter fields each accepts.\n\n"
        "Args:\n"
        "    query: Search string; required unless mode is 'catalogue'.\n"
        "    domain: Domain to search, or empty to list the catalogue.\n"
        "    mode: One of 'hybrid', 'vector', 'bm25' or 'catalogue'.\n"
        "    top_k: Documents fetched per store (1-50).\n"
        "    rerank: Re-rank the fused results with the cross-encoder.\n"
        "    metadata_filter: Optional metadata filter in the store filter "
        "DSL.\n"
        "    max_chars: Character budget for the returned reference "
        "material.\n\n"
        "Returns:\n"
        "    Dictionary with query, domain, mode, top_k, results, count, "
        "content, catalogue, truncated, error and note."
    )

    try:
        from klea_utils.stores.utils import (
            load_retriever_config,
            resolve_stores_config_path,
        )

        path = resolve_stores_config_path()
        if path is None:
            return description
        config = load_retriever_config(path)
    except (ImportError, OSError, ValueError):
        return description

    lines: list[str] = []
    for name, domain_config in config.domains.items():
        text = (domain_config.description or "").strip()
        lines.append(f"- {name}: {text}" if text else f"- {name}")
    if not lines:
        return description
    return f"{description}\n\nAvailable domains:\n" + "\n".join(lines)


@tool_meta(
    ToolInfo(
        tags={BUNDLED_TAG, "local", STORES_TAG},
        read_only=True,
        idempotent=True,
        open_world=False,
    )
)
async def search_stores(
    ctx: Context,
    query: Annotated[str, Field(description="Search string.")] = "",
    domain: Annotated[
        str,
        Field(description="Domain to search, or empty to list the catalogue."),
    ] = "",
    mode: Annotated[
        str,
        Field(description="One of 'hybrid', 'vector', 'bm25' or 'catalogue'."),
    ] = "hybrid",
    top_k: Annotated[
        int,
        Field(description="Documents fetched per store", ge=1, le=50),
    ] = 5,
    rerank: Annotated[
        bool,
        Field(description="Re-rank the fused results with the cross-encoder"),
    ] = True,
    metadata_filter: Annotated[
        dict[str, Any] | None,
        Field(description="Optional metadata filter in the store filter DSL"),
    ] = None,
    max_chars: Annotated[
        int,
        Field(
            description="Character budget for the returned reference material",
            ge=1,
            le=200_000,
        ),
    ] = 20000,
) -> ToolResult:
    """Search the deployment's document stores for one domain."""
    # Lazy: the implementation pulls in the retrieval layer (LangChain, the
    # embedding model), so importing the CLI/bundled server stays light.
    from klea_utils.mcp.tool_impls.stores import search_stores as search_stores_impl

    # The client may name the stores configuration for this request in the
    # tool-call metadata instead of relying on the server's own default.
    # Every lookup is guarded so an absent request context, absent meta, or a
    # missing key simply falls back to the configured default.
    stores_config: str | None = None
    request_context = getattr(ctx, "request_context", None)
    meta = (
        getattr(request_context, "meta", None) if request_context is not None else None
    )
    if meta is not None:
        from klea_utils.stores.utils import STORES_CONFIG_META_KEY

        value = meta.get(STORES_CONFIG_META_KEY) if hasattr(meta, "get") else None
        if isinstance(value, str) and value:
            stores_config = value

    result = search_stores_impl(
        query=query,
        domain=domain,
        mode=mode,
        top_k=top_k,
        rerank=rerank,
        metadata_filter=metadata_filter,
        max_chars=max_chars,
        configured=stores_config,
    )
    return to_result(result)


#: Domain names come from the deployment's configuration, so the description
#: is rendered once, after the function exists, and used by fastmcp at
#: registration time.
search_stores.__doc__ = _stores_description()


@tool_meta(
    ToolInfo(tags={BUNDLED_TAG, "local", "files"}, checkpaths=["path"], read_only=True)
)
async def list_files(
    path: Annotated[
        str,
        Field(
            description=(
                "Directory path to list. Must be relative to current working "
                "directory and cannot contain '..' for security. Defaults to '.'"
            ),
            min_length=1,
        ),
    ] = ".",
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
    - Finding a file anywhere under a directory tree by path pattern (use
      the find files tool instead).

    If the ``pattern`` or the ``include_*`` filters match nothing but the
    directory is not empty, the unfiltered listing is returned instead and
    ``note`` explains why, so the response still shows what is there.

    Example: ``list_files(path=".", pattern="*.py", recursive=True)``

    Args:
        path: Directory path to list. Must be relative to the current working
            directory and cannot contain '..' for security. Defaults to the
            current directory ('.').
        max_depth: Maximum directory depth to traverse. 'None' for unlimited.
        pattern: Space separated file patterns to filter files by type.
        include_files: Whether to include files in results.
        include_directories: Whether to include directories in results.
        recursive: If True, traverse subdirectories recursively.
        max_results: Maximum number of entries to return.

    Returns:
        Dictionary with list of files, truncated flag, error, and note (empty
        unless a filter matched nothing and the unfiltered listing was
        substituted).
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
async def find_files(
    pattern: Annotated[
        str,
        Field(
            description=(
                "Glob pattern matched against file paths, e.g. '*.py' or "
                "'**/*.py'. Use '*' to list every file"
            ),
            min_length=1,
        ),
    ] = "*",
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
    include_ignored: Annotated[
        bool,
        Field(
            description=(
                "Also list files ignored by .gitignore. Default false, which "
                "skips ignored files (and skips .git/cache directories always)"
            )
        ),
    ] = False,
    max_results: Annotated[
        int,
        Field(description="Maximum number of file paths to return", ge=1, le=10000),
    ] = 100,
) -> ToolResult:
    """Find files by path pattern anywhere under a directory.

    Use this tool to locate files by name or extension when you do not know
    where they are in the tree.  It returns file paths only, without
    contents or directory entries.

    Use when:

    - Finding files by name or extension across a project tree.
    - Discovering where a file lives before reading it.

    Do not use for:

    - Listing the contents of a specific directory, including its
      subdirectories (use the list files tool instead).
    - Searching inside file contents (use the grep tool instead).

    Example: ``find_files(pattern="**/*.py")``

    Args:
        pattern: Glob pattern matched against file paths; ``"*"`` lists all.
        path: Directory to search, relative to the project directory.
        include_ignored: Also list .gitignore-ignored files.
        max_results: Maximum number of file paths to return.

    Returns:
        Dictionary with files (paths relative to the search directory),
        truncated, error.
    """
    result = await find_files_impl(
        pattern=pattern,
        path=path,
        include_ignored=include_ignored,
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
                "directory and cannot contain '..' for security. Defaults to '.'"
            ),
            min_length=1,
        ),
    ] = ".",
    offset: Annotated[
        int,
        Field(
            description=(
                "1-indexed line to start reading from. Negative values count "
                "from the end: -1 is the last line, -N is the Nth line from the "
                "end"
            )
        ),
    ] = 1,
    char_offset: Annotated[
        int,
        Field(
            description=(
                "0-indexed character position within the start line; only "
                "needed to continue inside a line longer than the character cap"
            ),
            ge=0,
        ),
    ] = 0,
    limit: Annotated[
        int | None,
        Field(
            description=(
                "Maximum number of lines to return (>=1). None removes the "
                "line limit, but the server character budget still caps the "
                "response"
            ),
            ge=1,
        ),
    ] = 500,
    line_numbers: Annotated[
        bool,
        Field(
            description=(
                "Prefix each returned line with its line number (default true). "
                "Set false to get raw line text, e.g. to copy into an edit"
            )
        ),
    ] = True,
) -> ToolResult:
    """Read a file and return a slice of its text content.

    Use this tool to inspect source files, logs, or documents as plain text.
    Document formats (PDF, office files) are converted to Markdown first.

    Use when:

    - You need to see the contents of a file in the project.
    - You want to page through a large file by line numbers.
    - You need the end of a large file, such as the tail of a build log: use a
      negative offset (``offset=-100``) instead of paging from the start.

    Read file chunks instead of complete files. Use the grep tool to locate the
    region you need first, then read around it with ``offset``/``limit``.

    Do not use for:

    - Listing a directory (use the list files tool instead).
    - Fetching remote content (use the web fetch tool instead).

    Examples:

    - ``read_file(path="README.md", offset=1, limit=100)``
    - ``read_file(path="build.log", offset=-100)``

    Args:
        path: File path to read. Must be relative to the current working
            directory and cannot contain '..' for security. Defaults to the
            current directory ('.'), which is not a file, so an omitted path
            yields the nearby-entries fallback instead of a validation error.
        offset: 1-indexed line to start reading from. A negative value counts
            from the end: -1 is the last line, -N is the Nth line from the end.
        char_offset: 0-indexed character position within the start line; used
            only to continue inside a single line longer than the character
            cap (see ``next_char_offset`` in the result).
        limit: Maximum number of lines to return. ``None`` removes only the line
            limit; the server character budget still caps the response, so a
            ``None`` read can still be truncated with ``next_offset``. For a
            negative offset, this counts forward from the tail start.
        line_numbers: Prefix each line with its line number; set false for raw
            text to copy into an edit.

    The response size is always capped by a server-side character budget,
    reported as ``char_budget``; a large file is read by paging
    (``offset``/``limit``) or from the end rather than returned in one call.

    Returns:
        Dictionary with content, line range, total_lines, truncated,
        truncation_reason, char_budget, next_offset, next_char_offset, error
        and note.  ``line_start``/``line_end`` and ``next_offset`` are negative
        for a tail read, with -1 as the last line.  ``total_lines`` is ``null``
        when a streamed read stops before the relevant end (EOF for a forward
        read, the file start for a tail read); a tail that reaches past the
        start clamps to the first line and reports the exact total.  A partial
        tail that reaches EOF has ``truncated: false``, ``next_offset: null``
        and ``line_end: -1``.  ``char_budget`` is the effective server
        character cap, and ``truncation_reason`` is ``char_budget``,
        ``line_limit`` or ``scan_cap`` when truncated (empty otherwise), so a
        ``limit=None`` read shows why it was cut.  A truncated read is a
        success (empty ``error``) that normally ends on a line boundary and
        carries ``next_offset`` (and an explanatory ``note``) so the caller can
        continue.  A single line longer than the character cap is paged by
        characters: continue with the same ``offset`` and the returned
        ``next_char_offset``.  On a missing/not-a-file error, ``nearby``
        (entries in the nearest existing directory) and ``note`` are populated
        so the caller can see what exists instead.
    """
    result = read_file_impl(
        path=path,
        offset=offset,
        char_offset=char_offset,
        limit=limit,
        line_numbers=line_numbers,
    )
    return to_result(result)


@tool_meta(
    ToolInfo(
        tags={BUNDLED_TAG, "local", "files"}, checkpaths=["path"], destructive=True
    )
)
async def write_file(
    path: Annotated[str, Field(min_length=1)],
    content: Annotated[
        str,
        Field(description="Complete content to write to the file"),
    ],
) -> ToolResult:
    """Create a new file or overwrite an existing one with the given content.

    Use this tool to create a file, or to replace a file's entire contents.
    The write is atomic and an existing file's mode is preserved.

    Use when:

    - Creating a new file.
    - Replacing the whole content of a small file.

    Do not use for:

    - Reading a file (use the read file tool instead).
    - Listing a directory (use the list files tool instead).
    - Making a small change to an existing file (use the edit file tool instead).

    Example: write_file(path="notes.txt", content="hello world")

    Args:
        path: File path to write, relative to the project directory. Missing
            parent directories are created.
        content: Complete file content.

    Returns:
        Dictionary with path, created, bytes_written, additions, deletions,
        diff, error.
    """
    result = write_file_impl(path=path, content=content)
    return to_result(result)


@tool_meta(
    ToolInfo(
        tags={BUNDLED_TAG, "local", "files"}, checkpaths=["path"], destructive=True
    )
)
async def edit_file(
    path: Annotated[str, Field(min_length=1)],
    old_string: Annotated[
        str,
        Field(min_length=1, description="Exact text to replace"),
    ],
    new_string: Annotated[str, Field(description="Replacement text")],
    replace_all: Annotated[
        bool,
        Field(description="Replace every occurrence instead of requiring one"),
    ] = False,
) -> ToolResult:
    """Replace an exact span of text in an existing file.

    Use this tool for targeted changes.  To supply ``old_string``, first read
    the file with ``read_file(line_numbers=false)`` so the text matches
    exactly, including whitespace and indentation.

    Use when:

    - Changing a small part of an existing file.
    - Updating one occurrence, or every occurrence with ``replace_all``.

    Do not use for:

    - Creating a file or replacing its entire content (use the write file
      tool instead).
    - Reading a file (use the read file tool instead).

    Example: edit_file(path="a.py", old_string="x = 1", new_string="x = 2")

    Args:
        path: File path to edit, relative to the project directory.
        old_string: Exact text to replace; must occur once unless
            ``replace_all`` is set.
        new_string: Replacement text.
        replace_all: Replace every occurrence (default: require a unique
            match).

    Returns:
        Dictionary with path, replacements, additions, deletions, matcher,
        diff, error.  On a missing/not-a-file error, ``nearby`` (entries in
        the nearest existing directory) and ``note`` are populated so the
        caller can see what exists instead.
    """
    result = edit_file_impl(
        path=path,
        old_string=old_string,
        new_string=new_string,
        replace_all=replace_all,
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
    context_lines: Annotated[
        int,
        Field(
            description=(
                "Lines of context before and after each match (0-7). The "
                "default of 2 usually shows enough surrounding code to avoid a "
                "follow-up read_file; raise it for denser blocks"
            ),
            ge=0,
            le=MAX_CONTEXT_LINES,
        ),
    ] = DEFAULT_CONTEXT_LINES,
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
        context_lines: Lines of context before and after each match (0-7).
        max_results: Maximum number of matching lines to return.

    Returns:
        Dictionary with pattern, path, match hunks, truncated, files_scanned,
        error.  Each hunk is ``{path, start_line, end_line, lines}`` with one
        ``{line_number, line, match}`` entry per line, so callers get the
        surrounding code without a separate read.
    """
    result = await grep_impl(
        pattern=pattern,
        path=path,
        include=include,
        case_sensitive=case_sensitive,
        include_ignored=include_ignored,
        context_lines=context_lines,
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
                "error": "Download failed (check the URL, network, or file path permissions).",
                "display": {
                    "mime": "text/plain",
                    "data": f"download_file: {url} failed",
                },
            }
        )
    return to_result(
        {
            "saved_to": str(target),
            "error": "",
            "display": {
                "mime": "text/plain",
                "data": f"downloaded {url} -> {target}",
            },
        }
    )


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
                "supported. Scope the command to the requested target; do not "
                "run a broader command or suite than asked (for example run the "
                "one named test file, not the whole suite)."
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

    A non-zero exit code is a normal result, not an error: many tools signal
    conditions with it (``diff``/``grep`` return 1 for differences/no match, a
    failing test returns non-zero).  Judge the outcome from ``returncode`` and
    ``stdout``; ``error`` is set only when the command could not be run or was
    killed.

    A timed-out or cancelled command has its process group killed, but a
    command that detaches its children (for example daemonises or calls
    ``setsid``) may leave processes running; check with ``ps``/``pgrep`` and
    clean up if needed.

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
