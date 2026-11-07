# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Offline unit tests for result storage (:mod:`klea_eval.results`).

Every test writes under ``tmp_path`` and passes an explicit workspace root or
run directory, so nothing depends on ``$KLEA_EVAL_WORKSPACE``, on ``~`` or on a
real evaluation workspace.  No model, no network call and no LLM judge is used,
so the module runs in the default selection as well as under
``pytest -m eval -k results``.

The tests pin the ADR-0059 contract: ``run.json`` at the run root, one
``runs/index.jsonl`` line per completed run, append-only index writes, atomic
``run.json`` replacement, rejection of unknown schema versions, and a read-back
that combines the envelope with the ``metrics.jsonl`` summary.  Retention (the
last N runs per case) is deliberately not exercised: it belongs to a later
phase, and this module never prunes a published run.

Coverage map (behaviour -> test):

- timestamps stamped in UTC: ``test_write_metadata_stamps_timestamps_in_utc``
- full envelope round trip: ``test_write_metadata_round_trips_every_field``
- ``run.json`` at the run root: ``test_run_json_sits_at_the_run_root``
- atomic replacement, no temp file: ``test_write_metadata_replaces_...``
- missing file: ``test_read_metadata_missing_file_raises``
- unknown schema version: ``test_read_metadata_rejects_unknown_...``, ``...``
- broken/non-object JSON: ``test_read_metadata_rejects_broken_json``, ``...``
- blank mandatory fields: ``test_write_metadata_rejects_blank_...``
- non-serialisable models: ``test_write_metadata_rejects_non_...``
- unsupported version on write: ``..._rejects_unknown_schema_version``
- index path layout: ``test_index_path_is_under_the_runs_directory``
- one line per append, earlier lines kept: ``test_append_index_entry_...``,
  ``test_appending_the_same_entry_twice_keeps_both_lines``
- append order preserved: ``test_read_index_returns_entries_in_append_order``,
  ``test_read_index_preserves_file_order_not_sorted_order``
- missing index: ``test_read_index_missing_file_returns_empty``
- malformed lines skipped: ``test_read_index_skips_malformed_...``
- unknown version in the index: ``test_read_index_rejects_unknown_...``
- index timestamp mapping: ``test_to_index_entry_uses_completed_at``, ``...``
- stored run read back: ``test_load_stored_run_combines_...``
- missing metrics file: ``test_load_stored_run_missing_metrics_raises``
- metrics ordering independence: ``test_load_stored_run_is_independent_...``
- full lifecycle: ``test_full_lifecycle_round_trip``
- stable ``run_id`` (D6): ``test_same_case_and_seed_yield_the_same_run_id``
- no pruning (retention deferred): ``test_result_storage_does_not_prune_...``
- ASCII single-line JSON: ``test_written_json_is_ascii_and_single_line``
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from klea_eval import workspace
from klea_eval.identity import derive_identity
from klea_eval.results import (
    INDEX_FILE_NAME,
    INDEX_METRICS_PATH,
    METRICS_FILE_NAME,
    RUN_FILE_NAME,
    SCHEMA_VERSION,
    IndexEntry,
    ResultsError,
    ResultsSchemaError,
    RunMetadata,
    append_index_entry,
    index_path,
    load_stored_run,
    read_index,
    read_metadata,
    run_metadata_path,
    to_index_entry,
    write_metadata,
)
from klea_eval.run_manager import append_metric

pytestmark = pytest.mark.eval

#: Logger the result storage module writes to; warnings are asserted on it.
RESULTS_LOGGER = "klea_eval.results"


def _write_lines(path: Path, *lines: str) -> Path:
    """Write ``lines`` to ``path``, one per line, and return the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    if text and not text.endswith("\n"):
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return path


def _write_records(path: Path, *records: dict[str, Any]) -> Path:
    """Serialise ``records`` as JSONL and return the written path."""
    lines = tuple(json.dumps(record, sort_keys=True) for record in records)
    return _write_lines(path, *lines)


def _metadata(**overrides: Any) -> RunMetadata:
    """Build a valid run envelope, overriding individual fields."""
    fields: dict[str, Any] = {
        "run_id": "run-1",
        "case_id": "case-1",
        "status": "completed",
    }
    fields.update(overrides)
    return RunMetadata(**fields)


def _entry(**overrides: Any) -> IndexEntry:
    """Build a valid index entry, overriding individual fields."""
    fields: dict[str, Any] = {
        "run_id": "run-1",
        "case_id": "case-1",
        "status": "completed",
        "timestamp": "2026-01-01T00:00:00+00:00",
    }
    fields.update(overrides)
    return IndexEntry(**fields)


def _turn(number: int, **extra: Any) -> dict[str, Any]:
    """Build a per-turn metric record shaped like the run manager's output."""
    record: dict[str, Any] = {
        "case_id": "case-1",
        "run_id": "run-1",
        "turn_number": number,
        "role": "chat",
        "status": "completed",
    }
    record.update(extra)
    return record


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """Return the result-storage warnings captured during a test."""
    return [
        record
        for record in caplog.records
        if record.name == RESULTS_LOGGER and record.levelno >= logging.WARNING
    ]


# -- run envelope (run.json) ---------------------------------------------


def test_write_metadata_stamps_timestamps_in_utc(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    metadata = _metadata()

    written = write_metadata(run_dir, metadata)

    assert written.created_at is not None
    assert written.completed_at is not None
    created = datetime.fromisoformat(written.created_at)
    completed = datetime.fromisoformat(written.completed_at)
    assert created.tzinfo == UTC
    assert completed.tzinfo == UTC
    assert metadata.created_at is None


def test_write_metadata_round_trips_every_field(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    metadata = _metadata(
        created_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:00:05+00:00",
        case_version="1.0",
        harness_seed="7",
        models={"plan": "model-x", "execute": "model-y"},
    )

    written = write_metadata(run_dir, metadata)

    assert written == metadata
    read_back = read_metadata(run_dir)
    assert read_back == metadata
    assert read_back.models == {"plan": "model-x", "execute": "model-y"}
    assert read_back.harness_seed == "7"
    assert read_back.case_version == "1.0"


def test_run_json_sits_at_the_run_root(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"

    assert run_metadata_path(run_dir) == run_dir / RUN_FILE_NAME
    assert "workspace" not in run_metadata_path(run_dir).parts

    write_metadata(run_dir, _metadata())

    assert run_metadata_path(run_dir).is_file()
    assert not (run_dir / "workspace" / RUN_FILE_NAME).exists()


def test_write_metadata_replaces_atomically_without_a_temp_file(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run-1"

    write_metadata(run_dir, _metadata(status="failed"))
    write_metadata(run_dir, _metadata(status="completed"))

    assert read_metadata(run_dir).status == "completed"
    assert not (run_dir / f"{RUN_FILE_NAME}.tmp").exists()
    assert sorted(path.name for path in run_dir.iterdir()) == [RUN_FILE_NAME]


def test_read_metadata_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(ResultsError):
        read_metadata(tmp_path / "absent")

    empty_dir = tmp_path / "runs" / "run-1"
    empty_dir.mkdir(parents=True)
    with pytest.raises(ResultsError):
        read_metadata(empty_dir)


def test_read_metadata_rejects_unknown_schema_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    payload = {**_metadata().to_dict(), "schema_version": "9.9"}
    (run_dir / RUN_FILE_NAME).write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ResultsSchemaError):
        read_metadata(run_dir)


def test_read_metadata_rejects_broken_json(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / RUN_FILE_NAME).write_text("{not json", encoding="utf-8")

    with pytest.raises(ResultsError):
        read_metadata(run_dir)


def test_read_metadata_rejects_a_non_object_document(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    run_dir.mkdir(parents=True)
    (run_dir / RUN_FILE_NAME).write_text("42", encoding="utf-8")

    with pytest.raises(ResultsError):
        read_metadata(run_dir)


@pytest.mark.parametrize("field", ["run_id", "case_id", "status"])
def test_write_metadata_rejects_blank_mandatory_fields(
    tmp_path: Path, field: str
) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    blanked = replace(_metadata(), **{field: "   "})

    with pytest.raises(ResultsError):
        write_metadata(run_dir, blanked)

    assert not run_metadata_path(run_dir).exists()


def test_write_metadata_rejects_non_serialisable_models(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    metadata = _metadata(models={"plan": object()})

    with pytest.raises(ResultsError):
        write_metadata(run_dir, metadata)

    assert not run_metadata_path(run_dir).exists()


def test_write_metadata_rejects_unknown_schema_version(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"

    with pytest.raises(ResultsSchemaError):
        write_metadata(run_dir, _metadata(schema_version="2.0"))


def test_write_metadata_rejects_a_non_metadata_argument(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"

    with pytest.raises(ResultsError):
        write_metadata(run_dir, cast(Any, "not metadata"))


# -- suite index (index.jsonl) -------------------------------------------


def test_index_path_is_under_the_runs_directory(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    path = index_path(root)

    assert path == root / "runs" / INDEX_FILE_NAME
    assert path.parent == workspace.runs_dir(root)
    assert not path.exists()


def test_append_index_entry_writes_one_line_per_entry(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    entry = _entry()

    written = append_index_entry(entry, root)

    assert written == index_path(root)
    assert written == workspace.runs_dir(root) / INDEX_FILE_NAME
    lines = written.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["run_id"] == "run-1"
    assert payload["case_id"] == "case-1"
    assert payload["status"] == "completed"
    assert payload["metrics_path"] == INDEX_METRICS_PATH
    assert INDEX_METRICS_PATH == METRICS_FILE_NAME
    assert payload["schema_version"] == SCHEMA_VERSION


def test_appending_the_same_entry_twice_keeps_both_lines(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    entry = _entry()

    append_index_entry(entry, root)
    append_index_entry(entry, root)

    lines = index_path(root).read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all(isinstance(json.loads(line), dict) for line in lines)
    assert read_index(root) == (entry, entry)


def test_append_index_entry_rejects_a_non_entry_argument(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    with pytest.raises(ResultsError):
        append_index_entry(cast(Any, {"run_id": "run-1"}), root)


def test_read_index_returns_entries_in_append_order(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    first = _entry(run_id="run-1")
    second = _entry(run_id="run-2", case_id="case-2")
    third = _entry(run_id="run-3", status="failed")

    for entry in (first, second, third):
        append_index_entry(entry, root)

    entries = read_index(root)

    assert entries == (first, second, third)
    assert [item.metrics_path for item in entries] == [INDEX_METRICS_PATH] * 3
    assert [item.schema_version for item in entries] == [SCHEMA_VERSION] * 3


def test_read_index_preserves_file_order_not_sorted_order(tmp_path: Path) -> None:
    root = tmp_path / "workspace"

    append_index_entry(_entry(run_id="run-b"), root)
    append_index_entry(_entry(run_id="run-a"), root)

    assert [item.run_id for item in read_index(root)] == ["run-b", "run-a"]


def test_read_index_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_index(tmp_path / "workspace") == ()


def test_read_index_skips_malformed_lines_with_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    root = tmp_path / "workspace"
    good = json.dumps(_entry().to_dict(), sort_keys=True)
    _write_lines(
        index_path(root),
        good,
        "",
        "{not json",
        '{"run_id": "run-2"}',
        "42",
    )

    with caplog.at_level(logging.WARNING, logger=RESULTS_LOGGER):
        entries = read_index(root)

    assert [item.run_id for item in entries] == ["run-1"]
    assert _warnings(caplog)


def test_read_index_rejects_unknown_schema_version(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    payload = {**_entry().to_dict(), "schema_version": "9.9"}
    _write_lines(index_path(root), json.dumps(payload, sort_keys=True))

    with pytest.raises(ResultsSchemaError):
        read_index(root)


def test_to_index_entry_uses_completed_at() -> None:
    metadata = _metadata(
        run_id="run-7",
        created_at="2026-01-01T00:00:00+00:00",
        completed_at="2026-01-01T00:10:00+00:00",
    )

    entry = to_index_entry(metadata)

    assert entry == IndexEntry(
        run_id="run-7",
        case_id="case-1",
        status="completed",
        timestamp="2026-01-01T00:10:00+00:00",
        metrics_path=INDEX_METRICS_PATH,
        schema_version=SCHEMA_VERSION,
    )


def test_to_index_entry_falls_back_to_created_at() -> None:
    metadata = _metadata(run_id="run-8", created_at="2026-02-02T00:00:00+00:00")

    entry = to_index_entry(metadata)

    assert entry.run_id == "run-8"
    assert entry.timestamp == "2026-02-02T00:00:00+00:00"
    assert entry.metrics_path == "metrics.jsonl"


def test_to_index_entry_rejects_a_non_metadata_argument() -> None:
    with pytest.raises(ResultsError):
        to_index_entry(cast(Any, "not metadata"))


# -- stored run ----------------------------------------------------------


def test_load_stored_run_combines_envelope_and_metrics(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    metadata = write_metadata(run_dir, _metadata())
    _write_records(
        workspace.metrics_path(run_dir),
        _turn(1, input_tokens=20),
        _turn(2, input_tokens=50, output_tokens=30),
    )

    stored = load_stored_run(run_dir)

    assert stored.metadata == metadata
    assert stored.metadata.status == "completed"
    assert stored.metrics.run_id == "run-1"
    assert stored.metrics.case_id == "case-1"
    assert stored.metrics.turn_count == 2
    assert stored.metrics.total_input_tokens == 70
    assert stored.metrics.total_output_tokens == 30


def test_load_stored_run_missing_metrics_raises(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    write_metadata(run_dir, _metadata())

    with pytest.raises(ResultsError):
        load_stored_run(run_dir)

    with pytest.raises(ResultsError):
        load_stored_run(tmp_path / "absent")


def test_load_stored_run_is_independent_of_metric_line_order(
    tmp_path: Path,
) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    write_metadata(run_dir, _metadata())
    _write_records(
        run_dir / METRICS_FILE_NAME,
        _turn(2, input_tokens=50, output_tokens=30),
        _turn(1, input_tokens=20),
    )

    stored = load_stored_run(run_dir)

    assert stored.metrics.turn_count == 2
    assert stored.metrics.total_input_tokens == 70
    assert stored.metrics.total_output_tokens == 30


# -- lifecycle -----------------------------------------------------------


def test_full_lifecycle_round_trip(tmp_path: Path) -> None:
    identity = derive_identity("case-1", "0")
    root = tmp_path / "workspace"
    tmp_dir = workspace.prepare_run(identity.run_id, root)

    append_metric(
        workspace.metrics_path(tmp_dir),
        _turn(1, run_id=identity.run_id, input_tokens=10, output_tokens=5),
    )
    metadata = write_metadata(
        tmp_dir,
        RunMetadata(
            run_id=identity.run_id,
            case_id="case-1",
            status="completed",
            harness_seed="0",
        ),
    )
    final_dir = workspace.publish_run(identity.run_id, root)
    entry = to_index_entry(metadata)
    append_index_entry(entry, root)

    stored = load_stored_run(final_dir)

    assert workspace.is_complete(final_dir)
    assert stored.metadata == metadata
    assert stored.metadata.harness_seed == "0"
    assert stored.metrics.total_input_tokens == 10
    assert stored.metrics.total_output_tokens == 5
    assert read_index(root) == (entry,)


def test_same_case_and_seed_yield_the_same_run_id(tmp_path: Path) -> None:
    first = derive_identity("case-1", "0")
    second = derive_identity("case-1", "0")

    assert first.run_id == second.run_id

    roots = (tmp_path / "first", tmp_path / "second")
    for root in roots:
        tmp_dir = workspace.prepare_run(first.run_id, root)
        metadata = write_metadata(
            tmp_dir,
            RunMetadata(
                run_id=first.run_id,
                case_id="case-1",
                status="completed",
                harness_seed="0",
            ),
        )
        workspace.publish_run(first.run_id, root)
        append_index_entry(to_index_entry(metadata), root)

    stored = [read_metadata(workspace.run_dir(first.run_id, root)) for root in roots]
    indexed = [read_index(root)[0] for root in roots]

    assert [item.run_id for item in stored] == [first.run_id, first.run_id]
    assert [item.case_id for item in stored] == ["case-1", "case-1"]
    assert [item.run_id for item in indexed] == [first.run_id, first.run_id]


def test_result_storage_does_not_prune_published_runs(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    run_ids = [
        derive_identity("case-a", "0").run_id,
        derive_identity("case-b", "0").run_id,
    ]

    published: list[Path] = []
    for run_id in run_ids:
        tmp_dir = workspace.prepare_run(run_id, root)
        metadata = write_metadata(
            tmp_dir,
            RunMetadata(run_id=run_id, case_id="case-x", status="completed"),
        )
        published.append(workspace.publish_run(run_id, root))
        append_index_entry(to_index_entry(metadata), root)

    assert all(path.is_dir() for path in published)
    assert all(workspace.is_complete(path) for path in published)
    assert len(read_index(root)) == 2


# -- serialisation -------------------------------------------------------


def test_written_json_is_ascii_and_single_line(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run-1"
    root = tmp_path / "workspace"
    metadata = write_metadata(run_dir, _metadata(models={"chat": "na\u00efve"}))

    text = run_metadata_path(run_dir).read_text(encoding="utf-8")

    assert text.isascii()
    assert text.count("\n") == 1
    assert text.endswith("\n")
    assert "\\u00ef" in text
    assert json.loads(text)["models"]["chat"] == "na\u00efve"

    append_index_entry(to_index_entry(metadata), root)
    index_text = index_path(root).read_text(encoding="utf-8")
    assert index_text.isascii()
    assert index_text.count("\n") == 1
    assert index_text.endswith("\n")
