# Copyright 2026 Ankur Sinha <sanjay DOT ankur AT gmail DOT com>
"""Tests for the run manager: prepare, execute, retry, publish and skip.

Every turn executor, turn scorer and sleep function used here is a local fake,
so these tests never touch the network or a live model.  Cases are built from
the public :mod:`klea_eval.cases` dataclasses and each run is prepared under a
``tmp_path`` workspace root that the test owns.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from klea_eval import run_manager, workspace
from klea_eval.cases import TestCase, Turn
from klea_eval.run_manager import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    RunManagerError,
    RunPreparationError,
)


def _make_case(
    case_id: str = "case-alpha",
    turns: tuple[Turn, ...] | None = None,
    fixtures: dict | None = None,
    run_config: dict | None = None,
) -> TestCase:
    """Build a synthetic two-turn case without going through the JSONL loader."""
    if turns is None:
        turns = (
            Turn(turn_number=1, role="user", content="first turn"),
            Turn(turn_number=2, role="user", content="second turn"),
        )
    return TestCase(
        case_id=case_id,
        schema_version="1.0",
        description="synthetic case used by the run manager tests",
        turns=turns,
        fixtures=fixtures,
        run_config=run_config,
    )


def _prepare(
    tmp_path: Path, case: TestCase | None = None, harness_seed: str | int | None = None
) -> tuple[TestCase, Path, run_manager.RunContext]:
    """Prepare a run for ``case`` under a fresh workspace root in ``tmp_path``."""
    case = case if case is not None else _make_case()
    root = Path(tmp_path) / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    context = run_manager.start_run(
        case, Path(tmp_path), root, harness_seed=harness_seed
    )
    return case, root, context


def _output_metrics(
    result: run_manager.RunResult, context: run_manager.RunContext
) -> tuple[Path, Path]:
    """Return the finished run directory and its ``metrics.jsonl`` path."""
    base = (
        Path(result.output_dir)
        if result.output_dir is not None
        else Path(context.run_dir)
    )
    return base, Path(workspace.metrics_path(base))


def _read_metrics(path: Path) -> list[dict]:
    """Parse a ``metrics.jsonl`` file into a list of records."""
    text = Path(path).read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def _snapshot(root: Path) -> dict[str, bytes | None]:
    """Capture every path below ``root`` so an untouched tree can be asserted."""
    root = Path(root)
    snapshot: dict[str, bytes | None] = {}
    for path in sorted(root.rglob("*")):
        key = str(path.relative_to(root))
        snapshot[key] = path.read_bytes() if path.is_file() else None
    return snapshot


def test_happy_path_two_turns_writes_ordered_metrics(tmp_path):
    """A two-turn run completes, publishes and records one line per turn."""
    case, _root, context = _prepare(tmp_path)
    seen: list[tuple[str, str, str, int]] = []
    scored: list[tuple[str, int]] = []

    def executor(ctx, turn):
        seen.append((ctx.run_id, ctx.user_id, ctx.chat_id, turn.turn_number))
        return {"content": turn.content}

    def scorer(ctx, turn, record):
        scored.append((ctx.chat_id, turn.turn_number))
        return {"verdict": "ok"}

    result = run_manager.run_case(context, case, executor, scorer)

    assert result.status == STATUS_COMPLETED
    assert result.error is None
    assert result.output_dir is not None
    assert Path(result.output_dir).exists()
    assert [entry[3] for entry in seen] == [1, 2]
    assert [entry[1] for entry in scored] == [1, 2]
    assert len(result.metrics) == 2

    run_dir, metrics_file = _output_metrics(result, context)
    assert metrics_file.name == "metrics.jsonl"
    assert run_dir in metrics_file.parents
    assert metrics_file.exists()

    records = _read_metrics(metrics_file)
    assert [record["turn_number"] for record in records] == [1, 2]
    for record in records:
        assert record["run_id"] == context.run_id
        assert record["user_id"] == context.user_id
        assert record["chat_id"] == context.chat_id
        assert record["status"] == STATUS_COMPLETED
        assert record["attempts"] == 1
        assert record["score"] == {"verdict": "ok"}


def test_transient_failure_retries_and_succeeds(tmp_path):
    """One failed attempt is retried after the injected backoff delay."""
    case, _root, context = _prepare(tmp_path)
    failures = {"count": 0}
    delays: list[float] = []

    def flaky(ctx, turn):
        if turn.turn_number == 1 and failures["count"] == 0:
            failures["count"] += 1
            raise RuntimeError("transient backend hiccup")
        return {"content": turn.content}

    result = run_manager.run_case(context, case, flaky, sleep=delays.append)

    assert result.status == STATUS_COMPLETED
    assert result.error is None
    assert delays == pytest.approx([run_manager.RETRY_BACKOFF_SECONDS])

    _run_dir, metrics_file = _output_metrics(result, context)
    records = _read_metrics(metrics_file)
    assert [record["turn_number"] for record in records] == [1, 2]
    assert [record["attempts"] for record in records] == [2, 1]


def test_persistent_failure_is_recorded_without_escaping(tmp_path, monkeypatch):
    """A run that keeps failing ends as ``failed``; no exception escapes."""
    case, _root, context = _prepare(tmp_path)
    recorded: list[dict] = []
    real_append_metric = run_manager.append_metric

    def spy(path, record):
        recorded.append(dict(record))
        real_append_metric(path, record)

    monkeypatch.setattr(run_manager, "append_metric", spy)

    attempts = {"count": 0}

    def always_fails(ctx, turn):
        attempts["count"] += 1
        raise ValueError("model backend exploded")

    result = run_manager.run_case(
        context,
        case,
        always_fails,
        sleep=lambda _delay: None,
        max_attempts=2,
    )

    assert result.status == STATUS_FAILED
    assert result.error is not None
    assert "ValueError" in result.error
    assert result.metrics == ()
    assert attempts["count"] == 2
    assert recorded, "the failure must be recorded as a metric line"
    assert recorded[-1]["status"] == STATUS_FAILED
    assert recorded[-1]["attempts"] == 2
    assert recorded[-1]["run_id"] == context.run_id
    assert recorded[-1]["case_id"] == case.case_id

    if result.output_dir is not None:
        published = Path(workspace.metrics_path(Path(result.output_dir)))
        assert _read_metrics(published)[-1]["status"] == STATUS_FAILED


def test_completed_run_is_skipped_and_overwrite_reruns(tmp_path, monkeypatch):
    """A finished run is skipped untouched, and ``overwrite=True`` reruns it."""
    case, _root, context = _prepare(tmp_path)
    calls: list[int] = []

    def executor(ctx, turn):
        calls.append(turn.turn_number)
        return {"content": turn.content}

    real_detect_state = workspace.detect_state
    state = {"override": "complete"}

    def fake_detect_state(run_id, root=None):
        if state["override"] is None:
            return real_detect_state(run_id, root)
        return state["override"]

    monkeypatch.setattr(workspace, "detect_state", fake_detect_state)

    before = _snapshot(context.run_dir)
    skipped = run_manager.run_case(context, case, executor)

    assert skipped.status == STATUS_SKIPPED
    assert skipped.error is None
    assert skipped.metrics == ()
    assert calls == []
    assert _snapshot(context.run_dir) == before

    state["override"] = None
    rerun = run_manager.run_case(context, case, executor, overwrite=True)

    assert rerun.status == STATUS_COMPLETED
    assert rerun.error is None
    assert calls == [1, 2]

    _run_dir, metrics_file = _output_metrics(rerun, context)
    assert metrics_file.exists()
    assert len(_read_metrics(metrics_file)) == 2


def test_leftover_staging_state_is_discarded_before_republishing(tmp_path):
    """A crashed run's leftovers are cleared and the run is republished cleanly."""
    case = _make_case()
    base = Path(tmp_path)
    root = base / "workspace"
    root.mkdir(parents=True, exist_ok=True)

    crashed = run_manager.start_run(case, base, root)
    stale_metrics = Path(workspace.metrics_path(crashed.run_dir))
    stale_metrics.parent.mkdir(parents=True, exist_ok=True)
    stale_metrics.write_text('{"stale": true}\n', encoding="utf-8")

    context = run_manager.start_run(case, base, root)
    assert context.run_id == crashed.run_id

    def executor(ctx, turn):
        return {"content": turn.content}

    result = run_manager.run_case(context, case, executor)

    assert result.status == STATUS_COMPLETED
    _run_dir, metrics_file = _output_metrics(result, context)
    records = _read_metrics(metrics_file)
    assert [record["turn_number"] for record in records] == [1, 2]
    assert all("stale" not in record for record in records)


def test_append_metric_preserves_existing_lines(tmp_path):
    """Appending never truncates or rewrites records already on disk."""
    target = Path(tmp_path) / "metrics.jsonl"
    first = {"case_id": "case-alpha", "turn_number": 1, "status": STATUS_COMPLETED}
    second = {"case_id": "case-alpha", "turn_number": 2, "status": STATUS_COMPLETED}

    run_manager.append_metric(target, first)
    run_manager.append_metric(target, second)
    run_manager.append_metric(target, {"case_id": "case-beta", "turn_number": 1})

    assert len(target.read_text(encoding="utf-8").splitlines()) == 3
    records = _read_metrics(target)
    assert [record["case_id"] for record in records] == [
        "case-alpha",
        "case-alpha",
        "case-beta",
    ]
    assert records[0] == first
    assert records[1] == second


@pytest.mark.parametrize("bad_record", [[], "text", 5, None])
def test_append_metric_rejects_non_mapping_records(tmp_path, bad_record):
    """Only mappings can be written as metric lines."""
    with pytest.raises(RunManagerError):
        run_manager.append_metric(Path(tmp_path) / "metrics.jsonl", bad_record)


def test_append_metric_rejects_unserialisable_records(tmp_path):
    """A record that cannot be JSON encoded is refused, not half written."""
    with pytest.raises(RunManagerError):
        run_manager.append_metric(Path(tmp_path) / "metrics.jsonl", {"bad": object()})


def test_identities_are_deterministic_per_seed(tmp_path):
    """The identifier triple depends only on the case id and the harness seed."""
    base = Path(tmp_path)
    case = _make_case(run_config={"harness_seed": "7"})

    first = run_manager.start_run(case, base, base / "ws-one")
    second = run_manager.start_run(case, base, base / "ws-two")

    assert first.harness_seed == "7"
    assert second.harness_seed == "7"
    assert first.run_id == second.run_id
    assert first.user_id == second.user_id
    assert first.chat_id == second.chat_id

    other = run_manager.start_run(case, base, base / "ws-three", harness_seed="8")

    assert other.harness_seed == "8"
    assert other.run_id != first.run_id
    assert other.user_id != first.user_id
    assert other.chat_id != first.chat_id


def test_declared_fixture_is_copied_and_original_untouched(tmp_path):
    """Declared fixtures land in ``fixtures/``; the source file is left alone."""
    suite_dir = Path(tmp_path) / "suite"
    suite_dir.mkdir(parents=True, exist_ok=True)
    source = suite_dir / "input.txt"
    source.write_text("payload\n", encoding="utf-8")

    case = _make_case(
        fixtures={"input": "input.txt", "nested": {"files": ["input.txt"]}}
    )
    root = Path(tmp_path) / "workspace"
    root.mkdir(parents=True, exist_ok=True)
    context = run_manager.start_run(case, suite_dir, root)

    fixture_dir = Path(context.fixture_dir)
    assert fixture_dir.name == run_manager.FIXTURE_DIR_NAME

    assert context.fixtures is not None
    copied = Path(context.fixtures["input"])
    assert copied == fixture_dir / "input.txt"
    assert copied.read_text(encoding="utf-8") == "payload\n"
    assert Path(context.fixtures["nested"]["files"][0]) == copied

    assert source.exists()
    assert source.read_text(encoding="utf-8") == "payload\n"


@pytest.mark.parametrize("bad_overrides", [["planner"], "planner", 5])
def test_unresolvable_model_configuration_raises_before_run_dir_created(
    tmp_path, bad_overrides
):
    """A model configuration the manager cannot resolve fails fast, no run dir."""
    base = Path(tmp_path)
    root = base / "workspace"
    case = _make_case(
        run_config={"harness_seed": "3", "model_overrides": bad_overrides}
    )

    with pytest.raises(RunPreparationError):
        run_manager.start_run(case, base, root)

    assert not root.exists()


def test_model_override_roles_are_passed_through(tmp_path):
    """Role to model mappings are handed to the caller, not resolved here."""
    base = Path(tmp_path)
    overrides = {"planner": "model-a", "executor": "model-b"}
    case = _make_case(run_config={"model_overrides": overrides})

    context = run_manager.start_run(case, base, base / "workspace")

    assert context.model_overrides == overrides


def test_empty_harness_seed_is_rejected(tmp_path):
    """A blank seed cannot identify a run and is rejected before preparation."""
    base = Path(tmp_path)
    root = base / "workspace"
    case = _make_case(run_config={"harness_seed": "   "})

    with pytest.raises(RunPreparationError):
        run_manager.start_run(case, base, root)

    assert not root.exists()


def test_injected_callables_receive_the_identity_and_each_turn(tmp_path):
    """Every injected callable sees the identity triple and its own turn."""
    case, _root, context = _prepare(tmp_path, case=_make_case(case_id="case-beta"))
    executor_calls: list[tuple] = []
    scorer_calls: list[tuple] = []

    def executor(ctx, turn):
        executor_calls.append(
            (
                ctx.case_id,
                ctx.run_id,
                ctx.user_id,
                ctx.chat_id,
                turn.turn_number,
                turn.role,
            )
        )
        return {"content": turn.content}

    def scorer(ctx, turn, record):
        scorer_calls.append((ctx.chat_id, record["turn_number"], turn.turn_number))

    result = run_manager.run_case(context, case, executor, scorer)

    assert result.status == STATUS_COMPLETED
    expected = (case.case_id, context.run_id, context.user_id, context.chat_id)
    assert [call[:4] for call in executor_calls] == [expected, expected]
    assert [call[4] for call in executor_calls] == [1, 2]
    assert [call[5] for call in executor_calls] == ["user", "user"]
    assert [call[1] for call in scorer_calls] == [1, 2]
    assert [call[2] for call in scorer_calls] == [1, 2]

    _run_dir, metrics_file = _output_metrics(result, context)
    assert all("score" not in record for record in _read_metrics(metrics_file))


def test_model_overrides_and_endpoint_change_the_run_id(tmp_path):
    """A different model set or endpoint derives a different, non-skipped run."""
    base = Path(tmp_path)
    case = _make_case()

    default = run_manager.start_run(case, base, base / "ws-a")
    other_models = run_manager.start_run(
        case, base, base / "ws-b", model_overrides={"planner": "model-b"}
    )
    other_endpoint = run_manager.start_run(
        case, base, base / "ws-c", endpoint="http://backend-b:8000"
    )

    assert len({default.run_id, other_models.run_id, other_endpoint.run_id}) == 3
    assert default.run_id != other_models.run_id
    assert default.run_id != other_endpoint.run_id


def test_caller_model_overrides_merge_under_the_case(tmp_path):
    """Caller defaults are merged under the case's own ``model_overrides``."""
    base = Path(tmp_path)
    case = _make_case(run_config={"model_overrides": {"planner": "case-model"}})

    context = run_manager.start_run(
        case,
        base,
        base / "ws",
        model_overrides={"planner": "default-model", "executor": "exec-model"},
    )

    assert context.model_overrides == {
        "planner": "case-model",
        "executor": "exec-model",
    }
