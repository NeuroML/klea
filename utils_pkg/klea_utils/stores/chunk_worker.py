#!/usr/bin/env python3
"""
Subprocess chunking workers -- isolate Docling's per-conversion memory leak

See ``devdocs/system/store-create.md``; ADRs 0001 and 0002.

File: klea_utils/stores/chunk_worker.py

Copyright 2026 Ankur Sinha
Author: Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""

from __future__ import annotations

import ctypes
import logging
import multiprocessing
import time
from dataclasses import dataclass
from pathlib import Path
from queue import Empty
from typing import Any

from .ingestion import StoresBuilder

logger = logging.getLogger(__name__)

#: How often (seconds) the parent polls a worker's result queue while it
#: runs, and the overall cap before a worker is considered hung and
#: killed (its batch is then marked failed and the run continues).
_WORKER_POLL_INTERVAL = 1.0
_WORKER_TIMEOUT_SECONDS = 4 * 3600


@dataclass
class ChunkWorkerConfig:
    """Picklable configuration handed to one conversion worker batch.

    The parent's :class:`StoresBuilder` is not picklable (it holds the
    Docling converter, chunker, resolver, etc.), so the worker rebuilds
    its own from these plain fields.
    """

    source_path: str
    max_tokens: int
    tokenizer_model: str
    do_ocr: bool
    mem_limit_bytes: int | None
    log_level: int


@dataclass
class ChunkItemResult:
    """Outcome of converting one file inside a worker.

    ``status`` is one of:

    * ``ok`` -- converted and cached; *file_headings_entry* holds the
      file's metadata-map template entry
    * ``zero_chunks`` -- converted but produced no chunks (scanned PDF)
    * ``failed`` -- conversion raised; *error* has the message
    * ``deferred`` -- not processed because the worker hit its RSS cap;
      *file_path* lets the parent re-dispatch it to a fresh worker
    """

    file_name: str
    file_hash: str
    status: str
    file_headings_entry: dict[str, Any] | None = None
    error: str | None = None
    file_path: str | None = None


def _current_rss_bytes() -> int | None:
    """Return this process's current RSS in bytes, or ``None`` if unknown.

    Read from ``/proc/self/status`` (Linux).  On other platforms, or when
    the file is unreadable, returns ``None`` so callers fall back to
    batch-size-bounded batching instead of a memory cap.
    """
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    # Line is "VmRSS:    <kB>".
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


def _over_mem_limit(mem_limit_bytes: int | None) -> bool:
    """Return ``True`` when this process's RSS exceeds *mem_limit_bytes*."""
    if mem_limit_bytes is None:
        return False
    rss = _current_rss_bytes()
    if rss is None:
        return False
    return rss > mem_limit_bytes


def convert_batch_worker(
    config: ChunkWorkerConfig,
    items: list[tuple[str, str]],
    queue: Any | None = None,
    current_file: Any | None = None,
) -> list[ChunkItemResult] | None:
    """Convert and cache *items* in this process, one per result.

    Runs in a spawned child for real chunking (with *queue* and
    *current_file* for observability), or directly in the parent under
    tests (``queue`` is ``None`` and results are returned).  Rebuilds
    its own :class:`StoresBuilder`, so Docling's per-conversion memory
    leak (unreclaimable in-process; see docling-project/docling#2788) is
    confined to this process and reclaimed by the OS when it exits.

    Before each file, if the process RSS exceeds *config.mem_limit_bytes*
    the remaining items are returned as ``deferred`` (the parent re-runs
    them in a fresh worker).  On platforms where RSS is unavailable the
    cap is skipped and the parent's fixed batch size bounds the worker
    instead.

    When *queue* is given (spawned worker), each :class:`ChunkItemResult`
    is put on the queue incrementally so the parent can log progress and,
    on worker death, knows which file was being processed via
    *current_file* and which results were already completed.  In that
    mode this function returns ``None``.  Without a queue it returns the
    list -- this path exists only for unit tests (in-process direct
    calls) so they stay simple and hermetic.

    :param config: Worker configuration
    :param items: ``(absolute_file_path, file_hash)`` tuples to process
    :param queue: Queue for incremental puts (spawned worker) or
        ``None`` (in-process unit tests only)
    :param current_file: Shared ``Value`` for the file currently being
        processed, or ``None`` (unit tests omit it)
    :returns: List of results when ``queue`` is ``None`` (tests), else
        ``None`` (spawned worker)
    """
    # Configure console logging exactly as the parent CLI does: root at
    # INFO (so third-party DEBUG from httpcore/httpx/transformers stays
    # off) with only the Klea namespaces raised to the requested level.
    # The worker is a separate process, so it must configure its own
    # loggers; reusing the shared helper keeps output format and level
    # behaviour consistent with the parent.
    from klea_utils.plogging import setup_root_logger

    setup_root_logger("klea-stores-create", stderr_level=config.log_level)
    worker_logger = logging.getLogger("klea-stores-create.ChunkWorker")
    builder = StoresBuilder(
        embedding_model="",
        logger=worker_logger,
        max_tokens=config.max_tokens,
        tokenizer_model=config.tokenizer_model,
        do_ocr=config.do_ocr,
    )
    builder._ensure_tokenizer()
    source_path = Path(config.source_path)
    resolver = builder._make_resolver(source_path)

    results: list[ChunkItemResult] = []

    def _emit(r: ChunkItemResult) -> None:
        if queue is not None:
            queue.put(r)
        else:
            results.append(r)

    def _set_current(path: str) -> None:
        if current_file is not None:
            try:
                with current_file.get_lock():
                    encoded = path.encode()[:4095]
                    # c_char array: pad with nulls so stale tail is cleared
                    current_file.value = encoded + b"\x00" * (4096 - len(encoded))
            except Exception:  # noqa: BLE001, S110
                pass

    try:
        for ctr, (file_path_str, file_hash) in enumerate(items, 1):
            _set_current(file_path_str)
            if _over_mem_limit(config.mem_limit_bytes):
                worker_logger.warning(
                    f"Worker RSS exceeded limit; deferring "
                    f"{len(items) - ctr + 1} remaining files to a fresh worker"
                )
                for remaining_path, remaining_hash in items[ctr - 1 :]:
                    _emit(
                        ChunkItemResult(
                            file_name=Path(remaining_path).name,
                            file_hash=remaining_hash,
                            status="deferred",
                            file_path=remaining_path,
                        )
                    )
                break

            file_path = Path(file_path_str)
            try:
                docs, extracted = builder._convert_and_chunk(file_path, resolver)
                builder._save_to_cache(docs, extracted, source_path, file_hash)
            except Exception as e:  # noqa: BLE001
                worker_logger.error(f"Failed to process {file_path.name}: {e}")
                _emit(
                    ChunkItemResult(
                        file_name=file_path.name,
                        file_hash=file_hash,
                        status="failed",
                        error=str(e),
                    )
                )
                continue

            if not docs:
                worker_logger.warning(
                    f"No chunks produced for {file_path.name}. This usually "
                    f"means the PDF is scanned/image-based and its text "
                    f"could not be extracted with OCR disabled. Re-run "
                    f"with OCR enabled (drop --no-ocr) or run "
                    f"'klea-stores-create pre-check' to classify it."
                )
                # Even with no chunks the template needs the DEFAULT entry
                # (extracted metadata such as PDF Info), so build it.
                entry = builder._build_file_headings_entry(
                    file_path.name, extracted, docs
                )
                _emit(
                    ChunkItemResult(
                        file_name=file_path.name,
                        file_hash=file_hash,
                        status="zero_chunks",
                        file_headings_entry=entry,
                    )
                )
                continue

            entry = builder._build_file_headings_entry(file_path.name, extracted, docs)
            _emit(
                ChunkItemResult(
                    file_name=file_path.name,
                    file_hash=file_hash,
                    status="ok",
                    file_headings_entry=entry,
                )
            )
    finally:
        # Flush any buffered DOI resolutions written by this worker so
        # later workers see them.  The resolver is protocol-typed (only
        # ``resolve`` is guaranteed), so close defensively.
        close = getattr(resolver, "close", None)
        if close is not None:
            close()

    if queue is not None:
        return None
    return results


def _run_worker_and_put(
    config: ChunkWorkerConfig,
    items: list[tuple[str, str]],
    queue: multiprocessing.Queue,
    current_file: Any | None = None,
) -> None:
    """Spawn entry point: run the worker and put its results on *queue*.

    The worker puts one :class:`ChunkItemResult` per file incrementally
    (see :func:`convert_batch_worker`); a final ``None`` sentinel marks
    completion.  Exceptions are caught so the parent's polling logic can
    distinguish a crash (no sentinel) from success.

    :param current_file: Shared ``Value`` that the worker updates with the
        file currently being processed, for observability on death
    """
    try:
        result = convert_batch_worker(config, items, queue, current_file)
        # In-process tests call convert_batch_worker without a queue and
        # get a list back; in that mode _run_worker_and_put is not used.
        # In incremental (spawned) mode convert_batch_worker puts per file
        # itself and returns None, so nothing more to do here.
        if result is not None:
            # Fallback for non-incremental callers: put the whole list
            for r in result:
                queue.put(r)
    except Exception as e:  # noqa: BLE001
        # The process is about to exit; the parent's polling/kill logic
        # handles a missing sentinel.  Log for the record.
        logger.error(f"Chunk worker crashed before returning results: {e}")
    finally:
        # Sentinel so the parent knows the worker finished its puts.
        try:
            queue.put(None)
        except Exception:  # noqa: BLE001, S110
            pass


def _run_one_worker(
    ctx: Any,
    config: ChunkWorkerConfig,
    batch: list[tuple[str, str]],
) -> list[ChunkItemResult] | None:
    """Spawn one worker process for *batch* and collect its results.

    The worker puts one :class:`ChunkItemResult` per file incrementally,
    so even if it dies mid-batch the parent retains the prefix and can
    attribute the failure to the next file (via the shared
    ``current_file`` value and the count of received results).  Returns
    ``None`` only when the worker died before putting anything.

    Returns ``None`` when the worker died without returning results
    (e.g. it was OOM-killed) or exceeded the run-time cap; the caller
    then marks the batch as failed and the run continues.  Extracted as
    its own function so the parent's batching/deferral logic can be
    tested by monkeypatching it instead of exercising real spawns.
    """
    # Shared value so the parent can read which file the worker was on
    # when it died (even for fatal signals that bypass Python-level
    # exception handling).
    try:
        current_file = ctx.Value(ctypes.c_char * 4096, b"\x00" * 4096, lock=True)
    except Exception:  # noqa: BLE001
        current_file = None

    queue: Any = ctx.Queue()
    process = ctx.Process(
        target=_run_worker_and_put, args=(config, batch, queue, current_file)
    )
    process.start()

    batch_results: list[ChunkItemResult] = []
    started = time.monotonic()
    while len(batch_results) < len(batch):
        try:
            item = queue.get(timeout=_WORKER_POLL_INTERVAL)
            if item is None:
                # Sentinel: worker finished its puts.
                break
            batch_results.append(item)
            # Per-file progress while the worker is still running; this
            # is the incremental counterpart to the batch range log in
            # dispatch_conversion_batches and restores the (X/Y) feel
            # that was lost when conversion moved to subprocesses.
            logger.info(
                f"Completed {item.file_name} "
                f"({len(batch_results)}/{len(batch)} in batch) - {item.status}"
            )
        except Empty:
            if not process.is_alive():
                # Worker died mid-batch.  Attribute the failure to the
                # file it was processing (via shared value or count).
                cur = ""
                if current_file is not None:
                    try:
                        with current_file.get_lock():
                            raw = bytes(current_file.value).split(b"\x00", 1)[0]
                            cur = raw.decode()
                    except Exception:  # noqa: BLE001, S110
                        pass
                exitcode = process.exitcode
                sig = f" (signal {-exitcode})" if exitcode and exitcode < 0 else ""
                logger.error(
                    f"Worker pid {process.pid} died (exitcode={exitcode}{sig}) "
                    f"while processing {cur or batch[len(batch_results)][0] if len(batch_results) < len(batch) else 'unknown'}; "
                    f"collected {len(batch_results)}/{len(batch)} results before death"
                )
                break
            if time.monotonic() - started > _WORKER_TIMEOUT_SECONDS:
                logger.error(
                    f"Worker pid {process.pid} exceeded the "
                    f"{_WORKER_TIMEOUT_SECONDS}s cap; killing it"
                )
                process.kill()
                process.join()
                break

    process.join(timeout=5)
    if process.is_alive():
        logger.error(
            f"Worker pid {process.pid} did not exit after producing results; killing it"
        )
        process.kill()
        process.join()

    if not batch_results:
        return None
    return batch_results


def dispatch_conversion_batches(
    parent_logger: logging.Logger,
    config: ChunkWorkerConfig,
    items: list[tuple[str, str]],
    batch_size: int,
) -> list[ChunkItemResult]:
    """Convert *items* by spawning a fresh worker process per batch.

    A fresh process per batch is deliberate: Docling leaks memory per
    conversion that cannot be freed in-process, so each worker's leak is
    reclaimed when it exits.  A worker stops at whichever comes first --
    its RSS cap (``config.mem_limit_bytes``) or *batch_size* -- marking
    the unprocessed tail ``deferred``; the parent re-dispatches those to
    a fresh worker.  On platforms where RSS is unavailable only
    *batch_size* bounds a worker.  A worker that dies without returning
    results (e.g. OOM-killed) has its whole batch marked ``failed`` and
    the run continues -- those files are simply re-converted on a later
    run.

    :param parent_logger: Logger for dispatch progress/errors
    :param config: Worker configuration
    :param items: ``(absolute_file_path, file_hash)`` items to convert
    :param batch_size: Max items handed to any single worker
    :returns: Flattened :class:`ChunkItemResult` for every item
    """
    ctx = multiprocessing.get_context("spawn")
    results: list[ChunkItemResult] = []
    pending: list[tuple[str, str]] = list(items)
    total = len(items)
    # Files that have already been retried once after a worker death;
    # a second death for the same file means it is poison and should be
    # marked failed instead of re-queued again.
    retried: set[str] = set()
    batch_no = 0

    while pending:
        batch_no += 1
        batch = pending[:batch_size]
        pending = pending[batch_size:]

        # Overall progress (X/Y) so the user sees where the run is,
        # even though conversion happens in subprocesses.
        batch_start = total - len(pending) - len(batch) + 1
        batch_end = total - len(pending)
        parent_logger.info(
            f"Processing files {batch_start}-{batch_end}/{total} "
            f"(batch #{batch_no}, {len(batch)} files) -- "
            f"spawning conversion worker"
        )
        batch_results = _run_one_worker(ctx, config, batch)

        if batch_results is None:
            # Worker died before putting anything (e.g. OOM-killed at
            # startup).  Re-queue once for a transient failure; on a
            # second death for the same files they are poison.
            to_retry: list[tuple[str, str]] = []
            for file_path_str, file_hash in batch:
                if file_hash in retried:
                    results.append(
                        ChunkItemResult(
                            file_name=Path(file_path_str).name,
                            file_hash=file_hash,
                            status="failed",
                            error="worker died repeatedly (possible poison file)",
                        )
                    )
                else:
                    to_retry.append((file_path_str, file_hash))
                    retried.add(file_hash)
            if to_retry:
                parent_logger.warning(
                    f"Conversion worker #{batch_no} produced no results; "
                    f"re-queuing {len(to_retry)} files for retry"
                )
                # Isolate the suspected file (first in batch) to the front
                # so a second crash confirms it.
                pending[:0] = to_retry
            continue

        # Worker died mid-batch: it put a prefix incrementally before
        # dying.  The missing tail was not put; re-queue it.
        if len(batch_results) < len(batch):
            missing = batch[len(batch_results) :]
            to_retry: list[tuple[str, str]] = []
            for file_path_str, file_hash in missing:
                if file_hash in retried:
                    results.append(
                        ChunkItemResult(
                            file_name=Path(file_path_str).name,
                            file_hash=file_hash,
                            status="failed",
                            error="worker died repeatedly mid-batch",
                        )
                    )
                else:
                    to_retry.append((file_path_str, file_hash))
                    retried.add(file_hash)
            if to_retry:
                parent_logger.warning(
                    f"Worker #{batch_no} died after {len(batch_results)}/"
                    f"{len(batch)} files; re-queuing {len(to_retry)} "
                    f"remaining files"
                )
                pending[:0] = to_retry
            # Fall through to handle the prefix that was completed;
            # deferred handling below will still apply if any.

        # Re-dispatch the deferred tail to a fresh worker (clean memory);
        # the deferred results themselves are not reported -- those files
        # are covered by the re-run.
        deferred = [r for r in batch_results if r.status == "deferred" and r.file_path]
        if deferred:
            parent_logger.warning(
                f"Worker #{batch_no} deferred {len(deferred)} files to a "
                f"fresh worker (memory cap)"
            )
            pending[:0] = [
                (r.file_path, r.file_hash) for r in deferred if r.file_path is not None
            ]
        results.extend(r for r in batch_results if r.status != "deferred")

    return results
