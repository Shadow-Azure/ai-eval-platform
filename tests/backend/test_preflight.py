"""M3-03 preparation consistency checks: blocker vs capacity warning."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models import (
    Credential,
    Dataset,
    DatasetCase,
    DatasetVersion,
    ModelConfig,
    ResourcePool,
    Runner,
)
from app.runtimes.base import AssetSnapshot
from app.seed import ensure_default_scope
from app.services.preflight import check_preparation
from sqlalchemy.orm import Session


def _seed(
    session: Session,
    *,
    adapter_key: str = "swebench",
    source_revision: str = "40-char-commit",
    case_count: int = 1,
    dataset_status: str = "active",
    credential_revoked: bool = False,
    runner_labels: tuple[str, ...] = ("self-hosted", "linux", "eval"),
    runner_status: str = "online",
    runner_count: int = 1,
    pool_status: str = "active",
) -> dict:
    tenant, _workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug=f"ds-{suffix}",
        name="SWE-bench",
        adapter_key=adapter_key,
    )
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        tenant_id=tenant.id,
        dataset_id=dataset.id,
        version=f"v-{suffix}",
        source_uri="fixture://swebench",
        source_revision=source_revision,
        adapter_version="1.0",
        case_count=case_count,
        status=dataset_status,
    )
    session.add(version)
    session.flush()
    if case_count:
        session.add(
            DatasetCase(
                tenant_id=tenant.id,
                dataset_version_id=version.id,
                case_key=f"case-{suffix}",
                ordinal=0,
                problem_statement="fix it",
            )
        )
    credential = Credential(
        tenant_id=tenant.id,
        project_id=project.id,
        kind="model",
        provider="minimax",
        ciphertext="ciphertext",
        key_version="local:1",
        secret_mask="****1234",
        revoked_at=datetime.now(UTC) if credential_revoked else None,
    )
    session.add(credential)
    session.flush()
    config = ModelConfig(
        tenant_id=tenant.id,
        project_id=project.id,
        credential_id=credential.id,
        provider="minimax",
        display_name="MiniMax M2.7",
        base_url="https://api.minimaxi.com/anthropic",
        model_name="MiniMax-M2.7-highspeed",
        api_style="anthropic",
        limits_json={},
        pricing_json={},
    )
    session.add(config)
    pool = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"pool-{suffix}",
        provider_key="fixed",
        labels_json={"kind": "fixed"},
        status=pool_status,
    )
    session.add(pool)
    session.flush()
    for index in range(runner_count):
        session.add(
            Runner(
                tenant_id=tenant.id,
                resource_pool_id=pool.id,
                provider_runner_id=f"runner-{suffix}-{index}",
                name=f"fixed-linux-{suffix}-{index}",
                labels_json=list(runner_labels),
                status=runner_status,
            )
        )
    session.commit()
    return {
        "dataset_version_id": version.id,
        "model_config_id": config.id,
        "resource_pool_id": pool.id,
    }


def _check(session: Session, ids: dict, **overrides):
    values = {
        "dataset_version_id": ids["dataset_version_id"],
        "model_config_id": ids["model_config_id"],
        "runtime_key": "claude-code",
        "resource_pool_id": ids["resource_pool_id"],
        "requested_concurrency": 1,
        "scoring_image_digest": "sha256:" + "a" * 64,
        "execution_image_digest": "sha256:" + "c" * 64,
        "assets": AssetSnapshot(files={}),
    }
    values.update(overrides)
    return check_preparation(session, **values)


def _codes(result) -> list[str]:
    return [issue.code for issue in result.blockers]


def test_valid_preparation_is_ready(session: Session) -> None:
    ids = _seed(session)
    result = _check(session, ids)
    assert result.ready
    assert result.blockers == ()


def test_missing_dataset_version_blocks(session: Session) -> None:
    ids = _seed(session)
    result = _check(session, ids, dataset_version_id="missing")
    assert "dataset_version_not_found" in _codes(result)


def test_inactive_empty_or_unresolved_dataset_blocks(session: Session) -> None:
    ids = _seed(session, dataset_status="archived")
    assert "dataset_version_inactive" in _codes(_check(session, ids))

    ids = _seed(session, case_count=0)
    assert "dataset_version_empty" in _codes(_check(session, ids))

    ids = _seed(session, source_revision="")
    assert "ref_unresolved" in _codes(_check(session, ids))


def test_dataset_adapter_mismatch_blocks(session: Session) -> None:
    ids = _seed(session, adapter_key="human-eval")
    assert "capability_mismatch" in _codes(_check(session, ids))


def test_missing_or_revoked_model_credential_blocks(session: Session) -> None:
    ids = _seed(session)
    assert "model_config_missing" in _codes(_check(session, ids, model_config_id="missing"))

    ids = _seed(session, credential_revoked=True)
    assert "credential_unavailable" in _codes(_check(session, ids))


def test_unknown_runtime_blocks(session: Session) -> None:
    ids = _seed(session)
    result = _check(session, ids, runtime_key="unknown-runtime")
    assert _codes(result) == ["runtime_unsupported"]


def test_missing_pool_and_runner_labels_block(session: Session) -> None:
    ids = _seed(session)
    assert "resource_pool_missing" in _codes(_check(session, ids, resource_pool_id="missing"))

    ids = _seed(session, runner_labels=("windows",))
    assert "runner_labels_mismatch" in _codes(_check(session, ids))


def test_scoring_image_and_assets_block(session: Session) -> None:
    ids = _seed(session)
    assert "scoring_image_missing" in _codes(_check(session, ids, scoring_image_digest=""))
    assert "execution_image_missing" in _codes(_check(session, ids, execution_image_digest=""))
    assert "asset_unresolved" in _codes(_check(session, ids, assets_resolved=False))

    bad_assets = AssetSnapshot(files={"../evil": b"x"})
    assert "asset_invalid" in _codes(_check(session, ids, assets=bad_assets))


def test_capacity_shortage_is_only_a_warning(session: Session) -> None:
    ids = _seed(session, runner_status="offline")
    result = _check(session, ids)
    assert result.ready
    assert [issue.code for issue in result.warnings] == ["capacity_queue_expected"]

    ids = _seed(session, runner_count=1)
    result = _check(session, ids, requested_concurrency=4)
    assert result.ready
    assert [issue.code for issue in result.warnings] == ["capacity_queue_expected"]


def test_prepare_api_returns_blockers(client, session: Session) -> None:
    ids = _seed(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json={
            "dataset_version_id": ids["dataset_version_id"],
            "model_config_id": ids["model_config_id"],
            "resource_pool_id": ids["resource_pool_id"],
            "scoring_image_digest": "sha256:" + "b" * 64,
            "execution_image_digest": "sha256:" + "d" * 64,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["warnings"][0]["code"] == "assets_empty"

    blocked = client.post(
        "/api/v1/evaluations/prepare",
        json={
            "dataset_version_id": "missing",
            "model_config_id": ids["model_config_id"],
            "resource_pool_id": ids["resource_pool_id"],
            "scoring_image_digest": "sha256:" + "b" * 64,
            "execution_image_digest": "sha256:" + "d" * 64,
        },
    )
    assert blocked.status_code == 200
    assert blocked.json()["ready"] is False
    assert blocked.json()["blockers"][0]["code"] == "dataset_version_not_found"


def test_prepare_api_requires_session(anonymous_client, session: Session) -> None:
    ids = _seed(session)
    response = anonymous_client.post(
        "/api/v1/evaluations/prepare",
        json={
            "dataset_version_id": ids["dataset_version_id"],
            "model_config_id": ids["model_config_id"],
            "resource_pool_id": ids["resource_pool_id"],
        },
    )
    assert response.status_code == 401


def test_preflight_reports_effective_concurrency(session: Session) -> None:
    ids = _seed(session, runner_count=2)
    result = _check(session, ids, requested_concurrency=8)
    assert result.effective_concurrency == 2
    assert result.payload()["effective_concurrency"] == 2


def test_preflight_effective_concurrency_is_zero_without_runners(session: Session) -> None:
    ids = _seed(session, runner_count=0)
    result = _check(session, ids)
    assert result.effective_concurrency == 0
    assert result.payload()["effective_concurrency"] == 0
