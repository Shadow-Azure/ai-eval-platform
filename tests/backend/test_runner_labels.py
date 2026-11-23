"""Runner label selection shared by spot provisioning and shard dispatch (#414)."""

import pytest
from app.models import ResourcePool
from app.services.runner_labels import (
    DEFAULT_RUNS_ON,
    RunnerLabelsMissing,
    default_pool_runs_on,
    derive_pool_label,
    pool_runner_labels,
)


def make_pool(**overrides) -> ResourcePool:
    values = {
        "id": "pool-labels",
        "tenant_id": "tenant",
        "project_id": "project",
        "name": "labels",
        "provider_key": "aliyun",
        "provisioning_mode": "spot",
    }
    values.update(overrides)
    return ResourcePool(**values)


def test_only_pool_less_synthetic_runs_use_default_labels() -> None:
    assert pool_runner_labels(None) == list(DEFAULT_RUNS_ON)
    with pytest.raises(RunnerLabelsMissing):
        pool_runner_labels(make_pool())


def test_legacy_kind_marker_is_not_a_runner_label() -> None:
    """A fixed pool's ``{"kind": "fixed"}`` is metadata, not a fallback."""

    legacy = make_pool(
        provider_key="fixed",
        provisioning_mode="fixed",
        labels_json={"kind": "fixed"},
    )
    with pytest.raises(RunnerLabelsMissing):
        pool_runner_labels(legacy)

    explicit = make_pool(
        provider_key="fixed",
        provisioning_mode="fixed",
        labels_json={"runs_on": ["self-hosted", "gpu"]},
    )
    assert pool_runner_labels(explicit) == ["self-hosted", "gpu"]


def test_accepts_string_and_list_forms() -> None:
    assert pool_runner_labels(make_pool(labels_json={"runs_on": "self-hosted, gpu"})) == [
        "self-hosted",
        "gpu",
    ]
    assert pool_runner_labels(
        make_pool(labels_json={"runner_labels": ["self-hosted", "linux"]})
    ) == ["self-hosted", "linux"]
    assert pool_runner_labels(make_pool(labels_json={"labels": ["self-hosted", "arm64"]})) == [
        "self-hosted",
        "arm64",
    ]


def test_accepts_console_mapping_labels_and_ignores_kind() -> None:
    """The console stores labels as ``{label: true}``; use the keys."""

    pool = make_pool(
        labels_json={
            "kind": "spot",
            "self-hosted": True,
            "linux": True,
            "eval": True,
            "m5-08-spot": True,
        }
    )
    assert pool_runner_labels(pool) == ["self-hosted", "linux", "eval", "m5-08-spot"]


def test_instance_config_wins_and_truncates_to_contract_limit() -> None:
    pool = make_pool(
        instance_config_json={"runs_on": [f"label-{index}" for index in range(80)]},
        labels_json={"runs_on": ["self-hosted", "ignored"]},
    )
    labels = pool_runner_labels(pool)
    assert labels == [f"label-{index}" for index in range(64)]


def test_derives_stable_pool_label_and_default_set() -> None:
    assert derive_pool_label("fixed-linux") == "pool-fixed-linux"
    assert derive_pool_label("Aliyun ECS contended") == "pool-aliyun-ecs-contended"
    assert derive_pool_label("  ") == "pool-default"
    assert default_pool_runs_on("fixed-linux") == [
        "self-hosted",
        "linux",
        "eval",
        "pool-fixed-linux",
    ]
