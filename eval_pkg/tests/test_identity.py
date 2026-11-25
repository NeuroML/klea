"""Tests for deterministic run identity derivation (ADR-0057, D6).

The harness must derive ``run_id``, ``user_id`` and ``chat_id`` from the case
inputs alone, never randomly, so re-runs of the same case and seed are
comparable and resumable.  These tests pin exact UUID strings for two fixed
inputs, assert that the same inputs always give the same ids, and assert that
changing the seed or the case changes every derived id.

Only the standard library and ``pytest`` are used: the identity module must
stay cheap to import and must not drag in the model client or HTTP stack.
"""

from __future__ import annotations

import uuid

import pytest
from klea_eval.identity import (
    CHAT_NAMESPACE,
    RUN_NAMESPACE,
    USER_NAMESPACE,
    Identity,
    derive_chat_id,
    derive_identity,
    derive_run_id,
    derive_user_id,
)

# Pinned vectors: the derivation must never change these values, otherwise
# every stored run identifier silently changes meaning. These are pinned to
# the length-prefixed, collision-free name form (``<len>:<value>:<len>:<value>``).
PINNED_CASE_ID = "case-1"
PINNED_SEED = "seed-alpha"
PINNED_RUN_ID = "fae8469a-e18d-5358-a96d-9bb9cca808a7"
PINNED_USER_ID = "c0790b31-9d05-54c7-92c9-cbd2969d10cd"
PINNED_CHAT_ID = "2c641184-c380-5f66-bfe1-e5881b8174eb"

SECOND_CASE_ID = "case-2"
SECOND_SEED = 42
SECOND_RUN_ID = "3b91e57f-429a-5bbc-b2cb-f4f8a03be641"
SECOND_USER_ID = "ec2666ab-69fe-5abd-a41c-0ee8bf3e070a"
SECOND_CHAT_ID = "7bfc9f16-4fb7-5eb6-94ce-20a5e5fd10ce"


# ---------------------------------------------------------------------------
# Pinned vectors
# ---------------------------------------------------------------------------


def test_identity_pins_first_vector() -> None:
    """A fixed case id and string seed derive the pinned identifier triple."""
    identity = derive_identity(PINNED_CASE_ID, PINNED_SEED)

    assert identity == Identity(
        case_id=PINNED_CASE_ID,
        harness_seed=PINNED_SEED,
        run_id=PINNED_RUN_ID,
        user_id=PINNED_USER_ID,
        chat_id=PINNED_CHAT_ID,
    )


def test_identity_pins_second_vector() -> None:
    """A fixed case id and integer seed derive the pinned identifier triple."""
    identity = derive_identity(SECOND_CASE_ID, SECOND_SEED)

    assert identity.run_id == SECOND_RUN_ID
    assert identity.user_id == SECOND_USER_ID
    assert identity.chat_id == SECOND_CHAT_ID
    assert identity.harness_seed == str(SECOND_SEED)


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


def test_same_inputs_give_identical_ids() -> None:
    """Repeating the derivation with the same inputs yields identical ids."""
    first = derive_identity(PINNED_CASE_ID, PINNED_SEED)
    second = derive_identity(PINNED_CASE_ID, PINNED_SEED)

    assert first == second
    assert first.run_id == second.run_id
    assert first.user_id == second.user_id
    assert first.chat_id == second.chat_id


def test_integer_and_string_seed_are_distinct_names() -> None:
    """``42`` and ``"42"`` normalise to the same name and therefore the same ids."""
    assert derive_run_id("case-x", 42) == derive_run_id("case-x", "42")
    assert derive_user_id("case-x", 42) == derive_user_id("case-x", "42")


# ---------------------------------------------------------------------------
# Seed and case sensitivity
# ---------------------------------------------------------------------------


def test_different_seeds_give_different_ids() -> None:
    """Changing only the harness seed changes every derived identifier."""
    base = derive_identity(PINNED_CASE_ID, "seed-alpha")
    other = derive_identity(PINNED_CASE_ID, "seed-beta")

    assert base.run_id != other.run_id
    assert base.user_id != other.user_id
    assert base.chat_id != other.chat_id


def test_different_case_ids_give_different_ids() -> None:
    """Changing only the case id changes every derived identifier."""
    base = derive_identity("case-a", PINNED_SEED)
    other = derive_identity("case-b", PINNED_SEED)

    assert base.run_id != other.run_id
    assert base.user_id != other.user_id
    assert base.chat_id != other.chat_id


# ---------------------------------------------------------------------------
# Derivation shape
# ---------------------------------------------------------------------------


def test_ids_are_uuid_version_five() -> None:
    """Every identifier is a UUID v5 string, never a random uuid4."""
    identity = derive_identity(PINNED_CASE_ID, PINNED_SEED)

    for identifier in (identity.run_id, identity.user_id, identity.chat_id):
        assert uuid.UUID(identifier).version == 5


def test_chat_id_is_uuid5_over_run_and_user() -> None:
    """``chat_id`` is the UUID v5 of ``<run_id>:<user_id>`` in the chat namespace."""
    identity = derive_identity(PINNED_CASE_ID, PINNED_SEED)
    expected = str(uuid.uuid5(CHAT_NAMESPACE, f"{identity.run_id}:{identity.user_id}"))

    assert identity.chat_id == expected
    assert derive_chat_id(identity.run_id, identity.user_id) == identity.chat_id


def test_namespaces_are_distinct_uuids() -> None:
    """The namespace constants are stable, documented UUID literals."""
    namespaces = {RUN_NAMESPACE, USER_NAMESPACE, CHAT_NAMESPACE}

    assert len(namespaces) == 3
    for namespace in namespaces:
        assert isinstance(namespace, uuid.UUID)


def test_run_id_is_uuid5_in_run_namespace() -> None:
    """``run_id`` is the UUID v5 of ``<len>:<case_id>:<len>:<seed>`` in the run namespace."""
    expected = str(
        uuid.uuid5(
            RUN_NAMESPACE,
            f"{len(PINNED_CASE_ID)}:{PINNED_CASE_ID}:{len(PINNED_SEED)}:{PINNED_SEED}",
        )
    )

    assert derive_run_id(PINNED_CASE_ID, PINNED_SEED) == expected
    assert expected == PINNED_RUN_ID


def test_user_id_is_uuid5_in_user_namespace() -> None:
    """``user_id`` is the UUID v5 of ``<len>:<case_id>:<len>:<seed>`` in the user namespace."""
    expected = str(
        uuid.uuid5(
            USER_NAMESPACE,
            f"{len(PINNED_CASE_ID)}:{PINNED_CASE_ID}:{len(PINNED_SEED)}:{PINNED_SEED}",
        )
    )

    assert derive_user_id(PINNED_CASE_ID, PINNED_SEED) == expected
    assert expected == PINNED_USER_ID


def test_adjacent_field_split_does_not_collide() -> None:
    """A different split of adjacent fields must derive different identifiers."""
    first_run_id = derive_run_id("a:b", "c")
    second_run_id = derive_run_id("a", "b:c")

    assert first_run_id != second_run_id
    assert first_run_id == derive_run_id("a:b", "c")
    assert derive_user_id("a:b", "c") != derive_user_id("a", "b:c")


def test_adjacent_field_split_does_not_collide_on_pipe() -> None:
    """A different split of adjacent fields on a pipe must not collide either."""
    assert derive_run_id("a|b", "c") != derive_run_id("a", "b|c")
    assert derive_user_id("a|b", "c") != derive_user_id("a", "b|c")


# ---------------------------------------------------------------------------
# Input validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("blank_case_id", ["", "   "])
def test_blank_case_id_is_rejected(blank_case_id: str) -> None:
    """An empty or whitespace-only case id raises ``ValueError``."""
    with pytest.raises(ValueError, match="case_id must be a non-empty string"):
        derive_run_id(blank_case_id, PINNED_SEED)


@pytest.mark.parametrize("blank_seed", ["", "   "])
def test_blank_seed_is_rejected(blank_seed: str) -> None:
    """An empty or whitespace-only harness seed raises ``ValueError``."""
    with pytest.raises(ValueError, match="harness_seed must be a non-empty"):
        derive_run_id(PINNED_CASE_ID, blank_seed)


@pytest.mark.parametrize("run_id, user_id", [("", PINNED_USER_ID), (PINNED_RUN_ID, "")])
def test_chat_id_requires_both_inputs(run_id: str, user_id: str) -> None:
    """``derive_chat_id`` rejects a blank ``run_id`` or ``user_id``."""
    with pytest.raises(ValueError, match="run_id and user_id must be non-empty"):
        derive_chat_id(run_id, user_id)


# ---------------------------------------------------------------------------
# Result-affecting configuration
# ---------------------------------------------------------------------------


def test_different_model_sets_give_different_run_ids() -> None:
    """Two model sets for one case and seed derive two distinct run ids."""
    first = derive_identity(
        PINNED_CASE_ID, PINNED_SEED, model_overrides={"planner": "model-a"}
    )
    second = derive_identity(
        PINNED_CASE_ID, PINNED_SEED, model_overrides={"planner": "model-b"}
    )

    assert first.run_id != second.run_id
    assert first.user_id == second.user_id
    assert first.chat_id != second.chat_id


def test_different_endpoints_give_different_run_ids() -> None:
    """Two endpoints for one case and seed derive two distinct run ids."""
    first = derive_identity(
        PINNED_CASE_ID, PINNED_SEED, endpoint="http://backend-a:8000"
    )
    second = derive_identity(
        PINNED_CASE_ID, PINNED_SEED, endpoint="http://backend-b:8000"
    )

    assert first.run_id != second.run_id


def test_resolved_models_give_different_run_ids() -> None:
    """The resolved model names also participate in the run id."""
    first = derive_identity(
        PINNED_CASE_ID, PINNED_SEED, resolved_models={"planner": "model-a"}
    )
    second = derive_identity(
        PINNED_CASE_ID, PINNED_SEED, resolved_models={"planner": "model-b"}
    )

    assert first.run_id != second.run_id


def test_model_override_order_does_not_change_the_run_id() -> None:
    """The configuration is serialised with sorted keys, so order is irrelevant."""
    first = derive_run_id(
        PINNED_CASE_ID,
        PINNED_SEED,
        model_overrides={"planner": "model-a", "executor": "model-b"},
    )
    second = derive_run_id(
        PINNED_CASE_ID,
        PINNED_SEED,
        model_overrides={"executor": "model-b", "planner": "model-a"},
    )

    assert first == second


def test_empty_configuration_keeps_the_plain_pinned_vector() -> None:
    """No model set and no endpoint leave the pinned run id unchanged."""
    identity = derive_identity(PINNED_CASE_ID, PINNED_SEED)

    assert identity.run_id == PINNED_RUN_ID


def test_attempts_and_revisions_get_fresh_chat_ids() -> None:
    """A retry and an overwrite keep the run id but change the chat id."""
    base = derive_identity(PINNED_CASE_ID, PINNED_SEED)
    retried = derive_identity(PINNED_CASE_ID, PINNED_SEED, attempt=1)
    overwritten = derive_identity(PINNED_CASE_ID, PINNED_SEED, revision=1)

    assert base.run_id == retried.run_id == overwritten.run_id
    assert base.chat_id == PINNED_CHAT_ID
    assert len({base.chat_id, retried.chat_id, overwritten.chat_id}) == 3
    assert (retried.attempt, retried.revision) == (1, 0)
    assert (overwritten.attempt, overwritten.revision) == (0, 1)


def test_derive_chat_id_rejects_negative_counters() -> None:
    """A negative attempt or revision cannot identify a backend chat."""
    with pytest.raises(ValueError, match="non-negative"):
        derive_chat_id(PINNED_RUN_ID, PINNED_USER_ID, attempt=-1)
    with pytest.raises(ValueError, match="non-negative"):
        derive_chat_id(PINNED_RUN_ID, PINNED_USER_ID, revision=-1)
