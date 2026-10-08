#!/usr/bin/env python3
"""
File download implementation for Klea MCP tools.

See ``devdocs/system/mcp-permissions.md``.

File: klea_utils/mcp/tool_impls/download_file.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

import asyncio
import logging
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from klea_utils.api.utils import _make_retryer_httpx
from klea_utils.mcp.tool_impls.session import SessionLike
from klea_utils.mcp.tool_impls.ssrf import _MAX_REDIRECTS, check_ssrf_async
from klea_utils.mcp.tool_impls.web_fetch import _honest_user_agent

logger = logging.getLogger(__name__)

#: Default cap for downloaded files (100 MiB). Larger datasets (GB) should
#: be downloaded by the user directly; the cap prevents OOM via
#: response.content buffering while still allowing streaming to disk.
_DEFAULT_MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


async def download_file(
    session: SessionLike | None,
    url: str,
    file_path: str | Path,
    params: dict[str, Any] | None = None,
    timeout: float | httpx.Timeout = 30.0,
    retries: int = 3,
    project_root: str | None = None,
    allow_internal_hosts: bool = False,
    max_download_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
) -> Path | None:
    """Download a URL to *file_path* (overwriting) and return the path.

    Framework-agnostic implementation shared across Klea MCP servers.  Apps
    wrap this in an MCP tool that supplies ``session`` from their lifespan
    context (see klea_utils.mcp.lifespan).  Note that since this overwrites,
    this should not be exposed directly as a tool; use a wrapper around this.

    The request carries an honest User-Agent and is subject to the shared
    SSRF guard (refusing private/loopback hosts unless
    *allow_internal_hosts* is set).  The raw response body is written as
    bytes, so binary files (PDFs, office documents) survive intact.

    Transient failures (timeouts, connection errors, HTTP 5xx/429) are
    retried with exponential backoff.  Returns ``None`` when the download
    fails (non-2xx response, no session available, an SSRF denial, or the
    target path is denied by the permission check).

    :param session: HTTP session to use for the request.  ``None`` when no
        session is available.
    :param url: HTTP or HTTPS URL to download.
    :param file_path: Destination file path (existing files are overwritten).
    :param params: Optional query parameters for the request.
    :param timeout: Request timeout in seconds.
    :param retries: Number of attempts for transient failures.
    :param project_root: Retained for callers; the boundary is enforced by
        the client-side gate, not here (ADR-0007 update 2026-10-08).
    :param allow_internal_hosts: Skip the SSRF guard (requests to loopback,
        private, link-local, or reserved addresses).
    :param max_download_bytes: Maximum bytes to download; larger responses
        are aborted and the download is treated as failed to avoid OOM.
    :returns: The written :class:`Path`, or ``None`` on failure.
    """
    logger.debug(
        f"Downloading\n"
        f"{url = }\n"
        f"{file_path = }\n"
        f"{params = }\n"
        f"{timeout = }\n"
        f"{retries = }\n"
        f"{project_root = }\n"
        f"{allow_internal_hosts = }"
    )

    if session is None:
        logger.warning(f"No HTTP session available for: {url}")
        return None

    if not allow_internal_hosts:
        ssrf_error = await check_ssrf_async(url)
        if ssrf_error is not None:
            logger.warning(f"SSRF guard blocked {url}: {ssrf_error}")
            return None

    async def _do_download() -> Path | None:
        current_url = url
        current_params = params
        for _ in range(_MAX_REDIRECTS + 1):
            response = None  # type: ignore[assignment]
            stream_exit = None  # type: ignore[assignment]
            # Prefer streaming to avoid OOM; fallback to get for fakes that raise
            if hasattr(session, "stream"):
                try:
                    stream_ctx = session.stream(
                        "GET",
                        current_url,
                        params=current_params,
                        headers={"User-Agent": _honest_user_agent()},
                        timeout=httpx.Timeout(timeout)
                        if not isinstance(timeout, httpx.Timeout)
                        else timeout,
                        follow_redirects=False,
                    )
                    # Support both real httpx (async context manager) and fake
                    if hasattr(stream_ctx, "__aenter__"):
                        response = await stream_ctx.__aenter__()  # type: ignore[attr-defined]
                        stream_exit = stream_ctx.__aexit__  # type: ignore[attr-defined]
                    else:
                        response = stream_ctx  # type: ignore[assignment]
                        stream_exit = None  # type: ignore[assignment]
                except AssertionError as exc:
                    logger.debug(
                        f"stream not supported for download, fallback to get: {exc}"
                    )
                    response = await session.get(  # type: ignore[attr-defined]
                        current_url,
                        params=current_params,
                        headers={"User-Agent": _honest_user_agent()},
                        timeout=timeout,
                        follow_redirects=False,
                    )
            else:
                response = await session.get(  # type: ignore[attr-defined]
                    current_url,
                    params=current_params,
                    headers={"User-Agent": _honest_user_agent()},
                    timeout=timeout,
                    follow_redirects=False,
                )

            try:
                # Follow redirects manually with per-hop SSRF check
                if response.status_code in (301, 302, 303, 307, 308):
                    loc = response.headers.get("location")
                    if not loc:
                        logger.warning(
                            f"Redirect {response.status_code} with no Location for {current_url}"
                        )
                        return None
                    next_url = urljoin(current_url, loc)
                    parsed_next = urlparse(next_url)
                    if (
                        parsed_next.scheme not in ("http", "https")
                        or not parsed_next.netloc
                    ):
                        logger.warning(f"Redirect to invalid URL: {next_url}")
                        return None
                    if not allow_internal_hosts:
                        ssrf_error = await check_ssrf_async(next_url)
                        if ssrf_error is not None:
                            logger.warning(
                                f"SSRF guard blocked redirect {current_url} -> {next_url}: {ssrf_error}"
                            )
                            return None
                    logger.debug(
                        f"Following redirect {response.status_code}: {current_url} -> {next_url}"
                    )
                    current_url = next_url
                    current_params = None  # params already encoded in Location
                    continue
                if not response.is_success:
                    if response.status_code == 429 or response.status_code >= 500:
                        # Transient server-side error; raise so the retryer retries.
                        response.raise_for_status()
                    logger.warning(
                        f"Failed to download {current_url}: HTTP {response.status_code}"
                    )
                    return None

                # Cap and symlink checks before writing
                target = Path(file_path)
                # Resolve boundary for symlink checks
                try:
                    root = (
                        Path(project_root).resolve()
                        if project_root
                        else Path.cwd().resolve()
                    )
                except Exception:  # noqa: BLE001
                    root = Path.cwd().resolve()

                # Early Content-Length guard
                clen = response.headers.get("content-length")
                if clen is not None:
                    try:
                        if int(clen) > max_download_bytes:
                            logger.warning(
                                f"Download Content-Length {clen} exceeds cap {max_download_bytes} for {current_url}"
                            )
                            return None
                    except (TypeError, ValueError):
                        pass

                target.parent.mkdir(parents=True, exist_ok=True)
                # Post-mkdir symlink escape check (TOCTOU mitigation)
                try:
                    if not target.parent.resolve().is_relative_to(root):
                        logger.warning(
                            f"Download parent outside project after resolve: {target.parent}"
                        )
                        return None
                    if (
                        target.exists()
                        and target.is_symlink()
                        and not target.resolve().is_relative_to(root)
                    ):
                        logger.warning(
                            f"Download target symlink outside project: {target} -> {target.resolve()}"
                        )
                        return None
                    # Also reject if any parent component is a symlink outside root
                    for parent in target.parent.parents:
                        if parent == root or str(parent).startswith(str(root)):
                            break
                        if parent.is_symlink() and not parent.resolve().is_relative_to(
                            root
                        ):
                            logger.warning(
                                f"Download parent symlink outside project: {parent}"
                            )
                            return None
                except Exception as exc:  # noqa: BLE001
                    logger.warning(f"Symlink check failed for {target}: {exc}")
                    return None

                # Stream to temp file with per-chunk cap to avoid OOM
                tmp = target.with_name(target.name + ".tmp")
                written = 0
                try:
                    # Prefer aiter_bytes for streaming; fallback to content for fakes
                    aiter = getattr(response, "aiter_bytes", None)
                    if callable(aiter):
                        with open(tmp, "wb") as f:  # noqa: ASYNC230
                            async for chunk in aiter():
                                if not chunk:
                                    continue
                                if written + len(chunk) > max_download_bytes:
                                    logger.warning(
                                        f"Download exceeds cap {max_download_bytes} for {current_url} ({written + len(chunk)} bytes)"
                                    )
                                    try:
                                        f.close()
                                        tmp.unlink(missing_ok=True)
                                    except Exception:  # noqa: BLE001, S110
                                        pass
                                    return None
                                f.write(chunk)
                                written += len(chunk)
                    else:
                        # Fallback for non-streaming fakes (response.content)
                        data = getattr(response, "content", b"")
                        if (
                            isinstance(data, (str, bytes))
                            and len(data) > max_download_bytes
                        ):
                            logger.warning(
                                f"Download content size {len(data)} exceeds cap {max_download_bytes} for {current_url}"
                            )
                            return None
                        # Re-check cap before write
                        if isinstance(data, str):
                            data = data.encode()
                        with open(tmp, "wb") as f:  # noqa: ASYNC230
                            f.write(data)  # type: ignore[arg-type]
                            written = len(data)  # type: ignore[arg-type]
                    # Atomic replace after successful write and re-check
                    if not tmp.exists():
                        logger.warning(
                            f"Temp file missing after write for {current_url}"
                        )
                        return None
                    # Final symlink check before replace (target may have become symlink)
                    if (
                        target.exists()
                        and target.is_symlink()
                        and not target.resolve().is_relative_to(root)
                    ):
                        logger.warning(
                            f"Download target symlink outside project at replace: {target}"
                        )
                        try:
                            tmp.unlink(missing_ok=True)
                        except Exception:  # noqa: BLE001, S110
                            pass
                        return None
                    tmp.replace(target)
                except Exception as exc:
                    logger.warning(
                        f"Failed to stream download for {current_url}: {exc}"
                    )
                    try:
                        if tmp.exists():
                            tmp.unlink(missing_ok=True)
                    except Exception:  # noqa: BLE001, S110
                        pass
                    # Let retryer handle transient errors
                    if isinstance(exc, (httpx.HTTPError, TimeoutError)):
                        raise
                    return None
                logger.info(f"Saved downloaded file to {target} ({written} bytes)")
                return target
            finally:
                if stream_exit is not None:
                    try:
                        await stream_exit(None, None, None)
                    except Exception:  # noqa: BLE001, S110
                        pass
        logger.warning(f"Too many redirects for {url}")
        return None

    retryer = _make_retryer_httpx(attempts=retries)
    try:
        return await retryer(_do_download)
    except (TimeoutError, httpx.HTTPError) as exc:
        logger.warning(f"Download failed for {url}: {exc}")
        return None


async def download_file_to_cache(
    session: SessionLike | None,
    url: str,
    cache_dir: str | Path,
    file_name: str,
    params: dict[str, Any] | None = None,
    timeout: float | httpx.Timeout = 30.0,
    retries: int = 3,
    allow_internal_hosts: bool = False,
    max_download_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
) -> Path | None:
    """Download a URL into *cache_dir* as *file_name* and return the path.

    Convenience wrapper around :func:`download_file` for callers that keep a
    per-app cache directory (see ``klea_utils.paths.get_cache_dir``).  The
    permission boundary is *cache_dir* itself: this helper may write inside
    it and nowhere else.

    :param session: HTTP session to use for the request.  ``None`` when no
        session is available.
    :param url: HTTP or HTTPS URL to download.
    :param cache_dir: Directory in which to store the downloaded file.
    :param file_name: File name under *cache_dir* (existing files overwritten).
    :param params: Optional query parameters for the request.
    :param timeout: Request timeout in seconds.
    :param retries: Number of attempts for transient failures.
    :param allow_internal_hosts: Skip the SSRF guard (requests to loopback,
        private, link-local, or reserved addresses).
    :param max_download_bytes: Maximum bytes to download; larger responses
        are aborted.
    :returns: The written :class:`Path`, or ``None`` on failure.
    """
    target = Path(cache_dir) / file_name
    return await download_file(
        session=session,
        url=url,
        file_path=target,
        params=params,
        timeout=timeout,
        retries=retries,
        # The cache helper's boundary is its own cache directory: it may
        # write anywhere inside it, and nowhere else.
        project_root=str(cache_dir),
        allow_internal_hosts=allow_internal_hosts,
        max_download_bytes=max_download_bytes,
    )


async def download_files(
    session: SessionLike | None,
    files: list[dict[str, Any]],
    target_dir: str | Path,
    max_concurrency: int = 3,
    timeout: float | httpx.Timeout = 30.0,
    retries: int = 3,
    max_download_bytes: int = _DEFAULT_MAX_DOWNLOAD_BYTES,
) -> dict[str, Any]:
    """Download a list of files into *target_dir*, bounded in concurrency.

    Framework-agnostic helper that drives :func:`download_file` for each
    entry in *files*, as returned by the repository source list functions
    (entries carry ``path`` and ``download_url``).  Relative ``path`` values
    are preserved under *target_dir* (parent directories are created as
    needed), and writes stay confined to *target_dir*.

    *target_dir* is an explicit destination directory -- it may be the
    current project, a working subfolder, or a cache directory -- so the
    downloaded files are immediately usable where the caller asked for
    them.

    Downloads run with bounded concurrency (an ``asyncio.Semaphore``) so a
    large dataset does not hammer the source server.  Individual failures
    are recorded per file and do not abort the rest of the batch, so a
    caller (e.g. an LLM) can retry the failed paths.

    :param session: HTTP session to use for the requests.  ``None`` when no
        session is available.
    :param files: File entries to download; each needs ``path`` (relative
        target path) and ``download_url``.
    :param target_dir: Destination directory under which the files are
        written.
    :param max_concurrency: Maximum number of downloads in flight.
    :param timeout: Request timeout in seconds per download.
    :param retries: Number of attempts for transient failures per download.
    :param max_download_bytes: Maximum bytes per file; larger files are
        treated as failed to avoid OOM.
    :returns: dict with ``results`` (one entry per file: ``path`` plus
        ``saved_to`` on success or ``error`` on failure) and a top-level
        ``error`` (only set when the whole batch fails unexpectedly).
    """
    logger.debug(
        f"Downloading {len(files)} files into {target_dir}\n"
        f"{max_concurrency = }\n{timeout = }\n{retries = }"
    )

    sem = asyncio.Semaphore(max(1, max_concurrency))

    async def _download_one(file_entry: dict[str, Any]) -> dict[str, Any]:
        path = file_entry.get("path", "")
        url = file_entry.get("download_url", "")
        if not path or not url:
            logger.warning(
                f"Skipping file entry without path/download_url: {file_entry}"
            )
            return {"path": path, "error": "missing path or download_url"}
        try:
            target = await download_file(
                session=session,
                url=url,
                file_path=Path(target_dir) / path,
                timeout=timeout,
                retries=retries,
                # The batch boundary is its destination directory: files
                # may be written inside it, and nowhere else.
                project_root=str(target_dir),
                max_download_bytes=max_download_bytes,
            )
        except (OSError, TimeoutError, httpx.HTTPError) as exc:
            logger.warning(f"Unexpected error downloading {url}: {exc}")
            return {"path": path, "error": str(exc)}
        if target is None:
            logger.warning(f"Download failed for {url}")
            return {"path": path, "error": "download failed"}
        return {"path": path, "saved_to": str(target)}

    async def _bounded(file_entry: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            return await _download_one(file_entry)

    results = await asyncio.gather(
        *(_bounded(f) for f in files), return_exceptions=True
    )
    normalized: list[dict[str, Any]] = []
    for entry in results:
        if isinstance(entry, BaseException):
            # Only reachable for unexpected errors outside _download_one.
            logger.error(f"Unexpected download_files error: {entry}")
            normalized.append({"path": "", "error": str(entry)})
        else:
            normalized.append(entry)

    logger.info(
        f"Downloaded {sum('saved_to' in r for r in normalized)}/"
        f"{len(normalized)} files into {target_dir}"
    )
    return {"results": normalized, "error": ""}
