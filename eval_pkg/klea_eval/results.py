# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Result storage: the ADR-0059 writer and reader for evaluation runs.

The module owns the on-disk result format that ADR-0059 defines and that the
metrics collector (ADR-0058) and the run manager (:mod:`klea_eval.run_manager`)
leave to it:

- ``runs/<run_id>/run.json``: the run envelope (``run_id``, ``case_id``,
  ``case_version``, timestamps, ``status``, resolved models, the harness seed
  and the ``schema_version`` of the record);
- ``runs/<run_id>/metrics.jsonl``: written by the run manager, read back here;
- ``runs/index.jsonl``: exactly one line per completed run, a lookup and
  listing entry point only, never an aggregation store.

Directory creation, the ``COMPLETE`` marker and the atomic rename stay in
:mod:`klea_eval.workspace`; the per-turn ``metrics.jsonl`` lines stay in
:mod:`klea_eval.run_manager`.  This module only writes ``run.json`` and the
index line, always through a temporary file or a single ``O_APPEND`` write, so
a reader never observes a partial record.

Retention, ``transcript.jsonl``, role-to-model resolution, cost derivation and
suite-level aggregation (``pass_at_k``) are deliberately out of scope here:
they belong to ADR-0060 and the later phases of the implementation plan.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from klea_eval import metrics, workspace
from klea_eval.metrics import SCHEMA_VERSION

logger = logging.getLogger(__name__)

__all__ = [
    "INDEX_FILE_NAME",
    "INDEX_METRICS_PATH",
    "METRICS_FILE_NAME",
    "RUN_FILE_NAME",
    "SCHEMA_VERSION",
    "IndexEntry",
    "ResultsError",
    "ResultsSchemaError",
    "RunMetadata",
    "StoredRun",
    "append_index_entry",
    "index_path",
    "load_stored_run",
    "read_index",
    "read_metadata",
    "run_metadata_path",
    "to_index_entry",
    "write_metadata",
]

#: File name of a run's envelope, at the run root.
RUN_FILE_NAME = "run.json"

#: File name of the suite-level index, directly under the runs directory.
INDEX_FILE_NAME = "index.jsonl"

#: Directory-relative path of a run's metrics log, as stored in the index.
METRICS_FILE_NAME = workspace.METRICS_FILE_NAME
INDEX_METRICS_PATH = METRICS_FILE_NAME


class ResultsError(Exception):
    """Base error for result storage operations."""


class ResultsSchemaError(ResultsError):
    """Raised when a stored result uses an unsupported schema version."""


@dataclass(frozen=True, slots=True)
class RunMetadata:
    """The ``run.json`` envelope of one run.

    The envelope records the identity, the status and the reproducibility
    parameters of a run (ADR-0057, ADR-0059) plus the ``schema_version`` that
    every reader must check before interpreting the record.
    """

    run_id: str
    case_id: str
    status: str
    schema_version: str = SCHEMA_VERSION
    created_at: str | None = None
    completed_at: str | None = None
    case_version: str | None = None
    harness_seed: str | None = None
    models: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return the envelope as a plain, JSON-ready mapping."""
        models = dict(self.models) if self.models is not None else None
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "schema_version": self.schema_version,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "case_version": self.case_version,
            "harness_seed": self.harness_seed,
            "models": models,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> RunMetadata:
        """Build an envelope from a parsed ``run.json`` mapping."""
        if not isinstance(data, Mapping):
            raise ResultsError(
                f"run metadata must be an object, got {type(data).__name__}"
            )
        source = "run metadata"
        version = data.get("schema_version", SCHEMA_VERSION)
        return cls(
            run_id=_checked_text(data.get("run_id"), "run_id", source),
            case_id=_checked_text(data.get("case_id"), "case_id", source),
            status=_checked_text(data.get("status"), "status", source),
            schema_version=_checked_schema_version(version, source),
            created_at=_optional_text(data.get("created_at"), "created_at", source),
            completed_at=_optional_text(
                data.get("completed_at"), "completed_at", source
            ),
            case_version=_optional_text(
                data.get("case_version"), "case_version", source
            ),
            harness_seed=_optional_text(
                data.get("harness_seed"), "harness_seed", source
            ),
            models=_optional_models(data.get("models"), "models", source),
        )


@dataclass(frozen=True, slots=True)
class IndexEntry:
    """One line of ``runs/index.jsonl``, pointing at a completed run."""

    run_id: str
    case_id: str
    status: str
    timestamp: str
    metrics_path: str = INDEX_METRICS_PATH
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        """Return the index line as a plain, JSON-ready mapping."""
        return {
            "run_id": self.run_id,
            "case_id": self.case_id,
            "status": self.status,
            "timestamp": self.timestamp,
            "metrics_path": self.metrics_path,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> IndexEntry:
        """Build an index entry from a parsed index line mapping."""
        if not isinstance(data, Mapping):
            raise ResultsError(
                f"index entry must be an object, got {type(data).__name__}"
            )
        source = "index entry"
        version = data.get("schema_version", SCHEMA_VERSION)
        metrics_path = data.get("metrics_path", INDEX_METRICS_PATH)
        return cls(
            run_id=_checked_text(data.get("run_id"), "run_id", source),
            case_id=_checked_text(data.get("case_id"), "case_id", source),
            status=_checked_text(data.get("status"), "status", source),
            timestamp=_checked_text(data.get("timestamp"), "timestamp", source),
            metrics_path=_checked_text(metrics_path, "metrics_path", source),
            schema_version=_checked_schema_version(version, source),
        )


@dataclass(frozen=True, slots=True)
class StoredRun:
    """A run read back from storage: its envelope and its metrics summary."""

    metadata: RunMetadata
    metrics: metrics.RunMetrics


def run_metadata_path(run_dir: str | Path) -> Path:
    """Return ``run.json`` at the run root, never under ``workspace/``."""
    path = Path(run_dir) / RUN_FILE_NAME
    logger.debug(f"{path = }")
    return path


def index_path(root: str | Path | None = None) -> Path:
    """Return ``<runs dir>/index.jsonl`` for the given workspace root."""
    root_path = Path(root) if root is not None else None
    path = workspace.runs_dir(root_path) / INDEX_FILE_NAME
    logger.debug(f"{path = }")
    return path


def write_metadata(run_dir: str | Path, metadata: RunMetadata) -> RunMetadata:
    """Atomically write ``metadata`` as the run's ``run.json``.

    The envelope is validated, any missing timestamp is stamped in UTC, and the
    file is written to a fsynced sibling temporary file that is then moved into
    place with :func:`os.replace`, so a reader never observes a partial
    ``run.json``.  An existing ``run.json`` is replaced; nothing is truncated in
    place.  The envelope that was written is returned.
    """
    if not isinstance(metadata, RunMetadata):
        raise ResultsError(f"expected a RunMetadata, got {type(metadata).__name__}")

    target = run_metadata_path(run_dir)
    _check_metadata(metadata)
    stamped = replace(
        metadata,
        created_at=metadata.created_at or _utc_now(),
        completed_at=metadata.completed_at or _utc_now(),
    )

    try:
        text = json.dumps(stamped.to_dict(), sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ResultsError(
            f"run metadata for '{target}' is not JSON serialisable: {exc}"
        ) from exc

    logger.debug(f"{target = }, {stamped.run_id = }")
    _atomic_write(target, f"{text}\n".encode())
    return stamped


def read_metadata(run_dir: str | Path) -> RunMetadata:
    """Read and validate a run's ``run.json`` envelope.

    A missing file, unreadable file, invalid JSON or a non-object document
    raises :class:`ResultsError`; a ``schema_version`` this module does not
    understand raises :class:`ResultsSchemaError` rather than being silently
    reinterpreted.
    """
    target = run_metadata_path(run_dir)
    logger.debug(f"{target = }")
    try:
        text = target.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ResultsError(f"no run metadata at '{target}'") from None
    except OSError as exc:
        raise ResultsError(f"could not read '{target}': {exc}") from exc

    try:
        data = json.loads(text)
    except ValueError as exc:
        raise ResultsError(
            f"run metadata at '{target}' is not valid JSON: {exc}"
        ) from exc

    if not isinstance(data, Mapping):
        raise ResultsError(
            f"run metadata at '{target}' must be a JSON object, "
            f"got {type(data).__name__}"
        )

    return RunMetadata.from_dict(data)


def append_index_entry(entry: IndexEntry, root: str | Path | None = None) -> Path:
    """Append ``entry`` as one line of ``runs/index.jsonl``.

    The line is written with a single ``O_APPEND`` write and fsynced, so
    concurrent writers never truncate earlier lines and earlier entries stay
    intact.  The index path is returned.
    """
    if not isinstance(entry, IndexEntry):
        raise ResultsError(f"expected an IndexEntry, got {type(entry).__name__}")

    _check_entry(entry)
    target = index_path(root)

    try:
        text = json.dumps(entry.to_dict(), sort_keys=True, ensure_ascii=True)
    except (TypeError, ValueError) as exc:
        raise ResultsError(
            f"index entry for '{target}' is not JSON serialisable: {exc}"
        ) from exc

    line = f"{text}\n".encode()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(descriptor, line)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ResultsError(f"could not append to '{target}': {exc}") from exc

    logger.debug(f"{target = }, {entry.run_id = }")
    return target


def read_index(root: str | Path | None = None) -> tuple[IndexEntry, ...]:
    """Read ``runs/index.jsonl`` in file order.

    A missing index is not an error and yields an empty tuple.  Blank,
    unparsable and non-object lines are skipped with a logged warning so that
    one damaged line does not hide the rest; a line carrying an unsupported
    ``schema_version`` raises :class:`ResultsSchemaError`.
    """
    target = index_path(root)
    logger.debug(f"{target = }")
    if not target.is_file():
        return ()

    try:
        lines = target.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ResultsError(f"could not read '{target}': {exc}") from exc

    entries: list[IndexEntry] = []
    for number, line in enumerate(lines, start=1):
        text = line.strip()
        if not text:
            logger.warning(
                "results: skipping blank index line %d in %s", number, target
            )
            continue

        try:
            data = json.loads(text)
        except ValueError:
            logger.warning(
                "results: skipping unparsable index line %d in %s", number, target
            )
            continue

        if not isinstance(data, Mapping):
            logger.warning(
                "results: skipping non-object index line %d in %s", number, target
            )
            continue

        try:
            entries.append(IndexEntry.from_dict(data))
        except ResultsSchemaError:
            raise
        except ResultsError:
            logger.warning(
                "results: skipping invalid index line %d in %s", number, target
            )
            continue

    return tuple(entries)


def to_index_entry(metadata: RunMetadata) -> IndexEntry:
    """Build the index line that points at a stored run."""
    if not isinstance(metadata, RunMetadata):
        raise ResultsError(f"expected a RunMetadata, got {type(metadata).__name__}")

    timestamp = metadata.completed_at or metadata.created_at or _utc_now()
    entry = IndexEntry(
        run_id=metadata.run_id,
        case_id=metadata.case_id,
        status=metadata.status,
        timestamp=timestamp,
        metrics_path=INDEX_METRICS_PATH,
        schema_version=metadata.schema_version,
    )
    logger.debug(f"{entry.run_id = }, {timestamp = }")
    return entry


def load_stored_run(run_dir: str | Path) -> StoredRun:
    """Read a stored run: its ``run.json`` envelope plus its metrics summary.

    The metrics log is read through :func:`klea_eval.metrics.load_run`, which
    accepts a run directory; a metrics failure is translated into the local
    error hierarchy.
    """
    directory = Path(run_dir)
    metadata = read_metadata(directory)

    try:
        summary = metrics.load_run(directory)
    except metrics.MetricsSchemaError as exc:
        raise ResultsSchemaError(str(exc)) from exc
    except metrics.MetricsError as exc:
        raise ResultsError(
            f"could not load the metrics of '{directory}': {exc}"
        ) from exc

    logger.debug(f"{directory = }, {summary.run_id = }")
    return StoredRun(metadata=metadata, metrics=summary)


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    now = datetime.now(UTC).isoformat()
    logger.debug(f"{now = }")
    return now


def _checked_text(value: Any, label: str, source: str) -> str:
    """Return a mandatory, non-empty string field."""
    if not isinstance(value, str):
        raise ResultsError(
            f"{source} field '{label}' must be a string, got {type(value).__name__}"
        )

    text = value.strip()
    if not text:
        raise ResultsError(f"{source} field '{label}' must not be empty")
    return text


def _checked_schema_version(value: Any, source: str) -> str:
    """Return a schema version this module understands, or raise."""
    version = _checked_text(value, "schema_version", source)
    if version != SCHEMA_VERSION:
        raise ResultsSchemaError(
            f"{source} uses unsupported schema_version {version!r}; "
            f"this reader understands {SCHEMA_VERSION!r}"
        )
    return version


def _optional_text(value: Any, label: str, source: str) -> str | None:
    """Return an optional string field, normalising an empty string to None."""
    if value is None:
        return None
    if not isinstance(value, str):
        raise ResultsError(
            f"{source} field '{label}' must be a string or null, "
            f"got {type(value).__name__}"
        )

    text = value.strip()
    return text or None


def _optional_models(value: Any, label: str, source: str) -> dict[str, Any] | None:
    """Return the optional ``models`` mapping with string keys."""
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ResultsError(
            f"{source} field '{label}' must be an object or null, "
            f"got {type(value).__name__}"
        )

    return {str(key): item for key, item in value.items()}


def _check_metadata(metadata: RunMetadata) -> None:
    """Validate the fields of an envelope before it is written."""
    source = "run metadata"
    _checked_text(metadata.run_id, "run_id", source)
    _checked_text(metadata.case_id, "case_id", source)
    _checked_text(metadata.status, "status", source)
    _checked_schema_version(metadata.schema_version, source)
    _optional_text(metadata.created_at, "created_at", source)
    _optional_text(metadata.completed_at, "completed_at", source)
    _optional_text(metadata.case_version, "case_version", source)
    _optional_text(metadata.harness_seed, "harness_seed", source)
    _optional_models(metadata.models, "models", source)


def _check_entry(entry: IndexEntry) -> None:
    """Validate the fields of an index entry before it is appended."""
    source = "index entry"
    _checked_text(entry.run_id, "run_id", source)
    _checked_text(entry.case_id, "case_id", source)
    _checked_text(entry.status, "status", source)
    _checked_text(entry.timestamp, "timestamp", source)
    _checked_text(entry.metrics_path, "metrics_path", source)
    _checked_schema_version(entry.schema_version, source)


def _atomic_write(target: Path, data: bytes) -> None:
    """Write ``data`` to ``target`` through a fsynced temporary sibling."""
    temporary = target.with_name(f"{target.name}.tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
        try:
            os.write(descriptor, data)
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, target)
        # Workspace owns the directory-fsync semantics; reuse them here so the
        # rename is durable exactly as it is for a published run.
        workspace._fsync_dir(target.parent)
    except OSError as exc:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            logger.debug(f"{temporary = }")
        raise ResultsError(f"could not write '{target}': {exc}") from exc

    logger.debug(f"{target = }, {len(data) = }")
