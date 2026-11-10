"""Runner label selection shared by spot provisioning and shard dispatch (M5-08)."""

from app.models import ResourcePool
from app.services.runner_labels import DEFAULT_RUNS_ON, pool_runner_labels


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


def test_defaults_to_fixed_runner_labels() -> None:
    assert pool_runner_labels(None) == list(DEFAULT_RUNS_ON)
    assert pool_runner_labels(make_pool()) == ["self-hosted", "linux", "eval"]


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


def test_instance_config_wins_and_truncates_to_contract_limit() -> None:
    pool = make_pool(
        instance_config_json={"runs_on": [f"label-{index}" for index in range(20)]},
        labels_json={"runs_on": ["self-hosted", "ignored"]},
    )
    labels = pool_runner_labels(pool)
    assert labels == [f"label-{index}" for index in range(16)]


def test_uses_fallback_when_pool_has_no_labels() -> None:
    pool = make_pool(labels_json={})
    assert pool_runner_labels(pool, fallback=["self-hosted", "spot"]) == [
        "self-hosted",
        "spot",
    ]
