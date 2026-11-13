"""M5-04 launch preparations: frozen snapshot, TTL, idempotent create.

The wizard's confirmation step passes a ``preparation_id`` instead of the raw
configuration. These tests pin the contract: the snapshot freezes the case
subset and execution policy, create is idempotent per key, expired snapshots
are rejected, and any drift in the dataset/asset/model/pool identity surfaces
as ``configuration_changed`` instead of launching with different inputs.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.models import (
    AgentAssetSnapshot,
    AgentAssetSource,
    Credential,
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalPreparation,
    EvalRun,
    ModelConfig,
    ResourcePool,
    Runner,
)
from app.seed import ensure_default_scope
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def seed_launch(
    session: Session,
    *,
    case_count: int = 3,
    runner_count: int = 2,
    pool_max_capacity: int | None = None,
) -> dict:
    tenant, _workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug=f"ds-{suffix}",
        name="SWE-bench",
        adapter_key="swebench",
    )
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        tenant_id=tenant.id,
        dataset_id=dataset.id,
        version=f"v-{suffix}",
        source_uri="fixture://swebench",
        source_revision="40-char-commit",
        adapter_version="1.0",
        case_count=case_count,
        status="active",
        execution_image_digest="sha256:" + "a" * 64,
        scoring_image_digest="sha256:" + "b" * 64,
    )
    session.add(version)
    session.flush()
    for index in range(case_count):
        session.add(
            DatasetCase(
                tenant_id=tenant.id,
                dataset_version_id=version.id,
                case_key=f"case-{suffix}-{index}",
                ordinal=index,
                repo="django/django",
                base_commit="c" * 40,
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
        pricing_json={"input_per_1m": 0.27, "output_per_1m": 1.1},
    )
    session.add(config)
    pool = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"pool-{suffix}",
        provider_key="fixed",
        labels_json={"kind": "fixed"},
        status="active",
        max_capacity=pool_max_capacity,
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
                labels_json=["self-hosted", "linux", "eval"],
                status="online",
            )
        )
    session.commit()
    return {
        "dataset_version_id": version.id,
        "model_config_id": config.id,
        "resource_pool_id": pool.id,
        "version": version,
        "config": config,
        "credential": credential,
        "pool": pool,
        "dataset": dataset,
    }


def seed_asset_snapshot(session: Session) -> AgentAssetSnapshot:
    tenant, _workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    source = AgentAssetSource(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"assets-{suffix}",
        repo_url="https://github.com/obra/superpowers",
        default_ref="main",
        scan_paths_json=["skills"],
        selection_mode="explicit",
        runtime_prerequisites_json={},
        last_scan_json={},
        status="ready",
    )
    session.add(source)
    session.flush()
    snapshot = AgentAssetSnapshot(
        tenant_id=tenant.id,
        source_id=source.id,
        requested_ref="main",
        resolved_commit="d" * 40,
        selection_mode="explicit",
        scan_paths_json=["skills"],
        selection_json=[{"asset_type": "skill", "path": "skills/brainstorming", "name": "brainstorming"}],
        selection_digest="sha256:" + "e" * 64,
        tree_digest="sha256:" + "f" * 64,
        bundle_key=f"agent-assets/{tenant.id}/{suffix}/bundle.json.gz",
        bundle_digest="sha256:" + "1" * 64,
        bundle_size=1024,
        file_count=2,
        asset_count=1,
        runtime_key="claude-code",
        manifest_json={
            "validation": {"ok": True, "runtime_key": "claude-code"},
            "assets": [
                {
                    "asset_type": "skill",
                    "name": "brainstorming",
                    "path": "skills/brainstorming",
                    "content_digest": "sha256:" + "2" * 64,
                    "metadata": {},
                }
            ],
        },
        created_by="tester",
    )
    session.add(snapshot)
    session.commit()
    return snapshot


def prepare_body(ids: dict, **overrides) -> dict:
    body = {
        "dataset_version_id": ids["dataset_version_id"],
        "model_config_id": ids["model_config_id"],
        "resource_pool_id": ids["resource_pool_id"],
        "runtime_key": "claude-code",
        "case_selection": {"mode": "all"},
        "execution": {
            "concurrency": 2,
            "case_timeout_seconds": 1800,
            "max_attempts": 3,
            "model_budget_cny": 5,
        },
    }
    body.update(overrides)
    return body


def test_prepare_freezes_snapshot_with_ttl(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, case_selection={"mode": "count", "count": 2}),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["preparation_id"]
    assert body["case_count"] == 2
    assert body["estimated_job_count"] == 2
    assert body["snapshot_digest"].startswith("sha256:")
    assert body["expires_at"].endswith("Z")
    assert body["summary"]["execution"]["case_timeout_seconds"] == 1800
    assert body["summary"]["execution"]["max_attempts"] == 3
    assert body["cost_estimate"]["total_cny"]["low"]

    preparation = session.get(EvalPreparation, body["preparation_id"])
    assert preparation is not None
    assert preparation.status == "active"
    assert preparation.created_by
    assert preparation.snapshot_json["case_count"] == 2
    # SQLite returns naive timestamps and PostgreSQL aware ones; normalize.
    expires_at = preparation.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    assert expires_at > datetime.now(UTC)


def test_prepare_freezes_prompt_suffix_and_create_returns_it(
    client: TestClient, session: Session
) -> None:
    """M8-07: the suffix is normalized, frozen and exposed by run responses."""

    ids = seed_launch(session)
    suffix = "非交互评测：请使用 superpowers 技能；不要向用户提问，自主决策并继续。"
    empty = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, prompt_suffix=f"  {suffix}\n"),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["prompt_suffix"] == suffix
    assert body["summary"]["prompt_suffix"] == suffix

    preparation = session.get(EvalPreparation, body["preparation_id"])
    assert preparation is not None
    assert preparation.snapshot_json["prompt_suffix"] == suffix
    assert preparation.snapshot_json["identity"]["prompt_suffix"] == suffix
    # The suffix participates in the frozen configuration identity/digest.
    assert preparation.snapshot_digest != empty["snapshot_digest"]

    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": body["preparation_id"], "name": "附加指令评测"},
        headers={"Idempotency-Key": "idem-prompt-suffix"},
    )
    assert created.status_code == 201, created.text
    run_body = created.json()
    assert run_body["prompt_suffix"] == suffix
    stored = session.get(EvalRun, run_body["id"])
    assert stored is not None
    assert stored.prompt_suffix == suffix

    detail = client.get(f"/api/v1/evaluations/{run_body['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["prompt_suffix"] == suffix


def test_prompt_suffix_defaults_to_empty_and_whitespace_is_ignored(
    client: TestClient, session: Session
) -> None:
    """Empty keeps today's behaviour: the run stores and returns an empty string."""

    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, prompt_suffix="   \n\t "),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["prompt_suffix"] == ""
    assert body["summary"]["prompt_suffix"] == ""

    preparation = session.get(EvalPreparation, body["preparation_id"])
    assert preparation is not None
    assert preparation.snapshot_json["prompt_suffix"] == ""
    assert preparation.snapshot_json["identity"]["prompt_suffix"] == ""

    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": body["preparation_id"]},
        headers={"Idempotency-Key": "idem-empty-prompt-suffix"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["prompt_suffix"] == ""
    stored = session.get(EvalRun, created.json()["id"])
    assert stored is not None
    assert stored.prompt_suffix == ""


def test_prepare_and_create_without_agent_assets(
    client: TestClient, session: Session
) -> None:
    """Assets are optional: the runtime always runs, the snapshot may be absent."""

    ids = seed_launch(session)
    response = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True
    assert body["asset_snapshot"] is None
    assert body["resolved_commit"] is None
    assert body["summary"]["asset_snapshot"] is None
    assert [warning["code"] for warning in body["warnings"]] == ["assets_empty"]

    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": body["preparation_id"], "name": "无资产评测"},
        headers={"Idempotency-Key": "idem-no-assets"},
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["asset_snapshot_id"] is None
    stored = session.get(EvalRun, run["id"])
    assert stored.asset_snapshot_id is None
    assert stored.asset_snapshot_json == {}
    assert stored.runtime_key == "claude-code"


def test_create_from_preparation_freezes_asset_bundle_metadata(
    client: TestClient, session: Session
) -> None:
    """The wizard path must keep the runner bundle contract on the frozen run."""

    ids = seed_launch(session)
    snapshot = seed_asset_snapshot(session)
    prepared = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, asset_snapshot_id=snapshot.id),
    ).json()
    assert prepared["ready"] is True, prepared
    assert prepared["asset_snapshot"]["snapshot_id"] == snapshot.id
    assert prepared["asset_snapshot"]["bundle_key"] == snapshot.bundle_key

    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"], "name": "资产评测"},
        headers={"Idempotency-Key": "idem-asset-bundle"},
    )
    assert created.status_code == 201, created.text
    stored = session.get(EvalRun, created.json()["id"])
    frozen = stored.asset_snapshot_json
    assert frozen["snapshot_id"] == snapshot.id
    assert frozen["bundle_key"] == snapshot.bundle_key
    assert frozen["bundle_digest"] == snapshot.bundle_digest
    assert frozen["bundle_size"] == snapshot.bundle_size
    assert frozen["file_count"] == snapshot.file_count


def test_create_from_preparation_uses_frozen_subset_and_policy(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    prepared = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, case_selection={"mode": "count", "count": 2}),
    ).json()
    headers = {"Idempotency-Key": "idem-prep-1"}
    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"], "name": "冻结评测"},
        headers=headers,
    )
    assert created.status_code == 201, created.text
    run = created.json()
    assert run["total_cases"] == 2
    assert run["name"] == "冻结评测"
    stored = session.get(EvalRun, run["id"])
    assert stored.case_timeout_seconds == 1800
    assert stored.max_attempts == 3
    assert float(stored.model_budget_cny) == 5.0
    assert stored.consistency_json["preparation_id"] == prepared["preparation_id"]
    preparation = session.get(EvalPreparation, prepared["preparation_id"])
    assert preparation.status == "consumed"
    assert preparation.consumed_eval_run_id == run["id"]

    replay = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers=headers,
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["id"] == run["id"]
    assert session.query(EvalRun).filter(EvalRun.id == run["id"]).count() == 1


def test_create_replay_with_new_key_is_conflict(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    headers = {"Idempotency-Key": "idem-a"}
    assert (
        client.post(
            "/api/v1/evaluations",
            json={"preparation_id": prepared["preparation_id"]},
            headers=headers,
        ).status_code
        == 201
    )
    conflict = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-b"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"]["code"] == "preparation_consumed"


def test_create_requires_idempotency_header(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["error"]["code"] == "idempotency_key_required"


def test_expired_preparation_is_rejected(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    preparation = session.get(EvalPreparation, prepared["preparation_id"])
    preparation.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    session.commit()

    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-expired"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "preparation_expired"
    session.expire_all()
    assert session.get(EvalPreparation, prepared["preparation_id"]).status == "expired"


def test_asset_commit_drift_returns_configuration_changed(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    snapshot = seed_asset_snapshot(session)
    prepared = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, asset_snapshot_id=snapshot.id),
    ).json()
    assert prepared["ready"] is True, prepared

    snapshot.resolved_commit = "9" * 40
    session.commit()
    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-drift"},
    )
    assert response.status_code == 409
    error = response.json()["detail"]["error"]
    assert error["code"] == "configuration_changed"
    assert "asset_snapshot.resolved_commit" in error["details"]["changed"]


LOAD_TEST_PREREQUISITES = {
    "install_command": "npm install -g @fission-ai/openspec",
    "init_command": "openspec init",
}


def freeze_load_test(
    snapshot: AgentAssetSnapshot,
    *,
    status: str,
    prerequisites: dict | None = None,
) -> None:
    """Rewrite the frozen manifest as a resolve against that load-test result."""

    manifest = dict(snapshot.manifest_json or {})
    prerequisites = dict(prerequisites or {})
    manifest["runtime_prerequisites"] = prerequisites
    # M8-22: CLI-generated sources freeze the resolved commit archive instead
    # of relying solely on the load-test `.claude` snapshot.
    manifest["source_mode"] = bool(prerequisites)
    manifest["repo_url"] = "https://github.com/chenmr0/OpenSpec-cn" if prerequisites else None
    manifest["source_archive_key"] = (
        "agent-source-archives/tenant/chenmr0/OpenSpec-cn/" + "d" * 40 + ".tar.gz"
        if prerequisites
        else None
    )
    manifest["source_archive_digest"] = "sha256:" + "3" * 64 if prerequisites else None
    manifest["source_archive_size"] = 123 if prerequisites else None
    if status == "passed":
        manifest["load_test"] = {
            "status": "passed",
            "install_command": prerequisites.get("install_command"),
            "init_command": prerequisites.get("init_command"),
            "generated": {"skill": 1, "command": 0, "subagent": 0},
            "assets": [
                {
                    "asset_type": "skill",
                    "name": "openspec-explore",
                    "path": ".claude/skills/openspec-explore",
                }
            ],
        }
        manifest["runtime_setup"] = {
            "install_command": prerequisites["install_command"],
            "init_command": prerequisites["init_command"],
        }
    else:
        manifest["load_test"] = {}
        manifest["runtime_setup"] = {}
    snapshot.manifest_json = manifest


def test_prepare_uses_frozen_snapshot_after_source_retest_failure(
    client: TestClient, session: Session
) -> None:
    """M8-10: a snapshot frozen after a pass stays launchable after a re-test fails."""

    ids = seed_launch(session)
    snapshot = seed_asset_snapshot(session)
    freeze_load_test(snapshot, status="passed", prerequisites=LOAD_TEST_PREREQUISITES)
    source = session.get(AgentAssetSource, snapshot.source_id)
    assert source is not None
    source.runtime_prerequisites_json = dict(LOAD_TEST_PREREQUISITES)
    # The live source now fails a re-test; launchable state comes from the
    # frozen snapshot, never from this row.
    source.status = "requires_setup"
    source.load_test_json = {"status": "failed", "error": "install_command_failed"}
    session.commit()

    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, asset_snapshot_id=snapshot.id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is True, body
    assert body["asset_snapshot"]["id"] == snapshot.id
    assert body["asset_snapshot"]["load_test"]["status"] == "passed"
    assert body["asset_snapshot"]["runtime_setup"] == {
        "install_command": LOAD_TEST_PREREQUISITES["install_command"],
        "init_command": LOAD_TEST_PREREQUISITES["init_command"],
    }
    assert body["asset_snapshot"]["source_mode"] is True
    assert body["asset_snapshot"]["repo_url"] == "https://github.com/chenmr0/OpenSpec-cn"
    assert body["asset_snapshot"]["source_archive_key"].endswith(".tar.gz")

    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": body["preparation_id"], "name": "冻结快照评测"},
        headers={"Idempotency-Key": "idem-frozen-load-test"},
    )
    assert created.status_code == 201, created.text
    stored = session.get(EvalRun, created.json()["id"])
    assert stored is not None
    # The runner receives the frozen setup, not the failed live source state.
    assert stored.asset_snapshot_json["runtime_setup"] == {
        "install_command": LOAD_TEST_PREREQUISITES["install_command"],
        "init_command": LOAD_TEST_PREREQUISITES["init_command"],
    }
    assert stored.asset_snapshot_json["source_mode"] is True
    assert stored.asset_snapshot_json["source_archive_key"].endswith(".tar.gz")
    assert stored.asset_snapshot_json["load_test"]["status"] == "passed"


def test_prepare_blocks_snapshot_frozen_before_load_test_pass(
    client: TestClient, session: Session
) -> None:
    """M8-10: a snapshot resolved without a pass stays blocked even if the source passes later."""

    ids = seed_launch(session)
    snapshot = seed_asset_snapshot(session)
    freeze_load_test(snapshot, status="not_run", prerequisites=LOAD_TEST_PREREQUISITES)
    source = session.get(AgentAssetSource, snapshot.source_id)
    assert source is not None
    source.runtime_prerequisites_json = dict(LOAD_TEST_PREREQUISITES)
    # The live source passes afterwards; the frozen snapshot still has no pass.
    source.status = "ready"
    source.load_test_json = {"status": "passed", "generated": []}
    session.commit()

    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, asset_snapshot_id=snapshot.id),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ready"] is False
    assert body["preparation_id"] is None
    blocker = next(
        item for item in body["blockers"] if item["code"] == "asset_runtime_setup_required"
    )
    assert "加载测试" in blocker["message"]
    assert blocker["field"] == "asset_snapshot_id"
    assert blocker["details"]["source_id"] == source.id
    assert blocker["details"]["load_test_status"] == "not_run"


def test_revoked_credential_returns_configuration_changed(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    ids["credential"].revoked_at = datetime.now(UTC)
    session.commit()
    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-revoked"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["error"]["code"] == "configuration_changed"


def test_case_selection_unknown_key_blocks_prepare(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(
            ids,
            case_selection={"mode": "cases", "case_keys": ["missing-case"]},
        ),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ready"] is False
    assert body["blockers"][0]["field"] == "case_selection"
    assert body["preparation_id"] is None


def test_out_of_range_execution_policy_blocks_prepare(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(
            ids,
            execution={"concurrency": 2, "case_timeout_seconds": 5, "max_attempts": 9},
        ),
    )
    body = response.json()
    assert body["ready"] is False
    codes = {blocker["code"] for blocker in body["blockers"]}
    assert codes == {"case_timeout_invalid", "max_attempts_invalid"}


def test_concurrency_over_pool_capacity_surfaces_capacity_invalid(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session, pool_max_capacity=2)
    blocked = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, execution={"concurrency": 4}),
    ).json()
    assert blocked["ready"] is False
    assert blocked["blockers"][0]["code"] == "capacity_invalid"

    prepared = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, execution={"concurrency": 2}),
    ).json()
    assert prepared["ready"] is True
    ids["pool"].max_capacity = 1
    session.commit()

    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-capacity"},
    )
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["code"] == "capacity_invalid"
    assert error["details"]["fields"]["requested_concurrency"] == "invalid"


def test_preparation_belongs_to_its_creator(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    prepared = client.post("/api/v1/evaluations/prepare", json=prepare_body(ids)).json()
    preparation = session.get(EvalPreparation, prepared["preparation_id"])
    preparation.created_by = "someone-else@test.local"
    session.commit()

    response = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-owner"},
    )
    assert response.status_code == 404
    assert response.json()["detail"]["error"]["code"] == "preparation_not_found"


@pytest.mark.parametrize(
    ("selection", "expected"),
    [
        ({"mode": "count", "count": 3}, 3),
        ({"mode": "count", "count": 1}, 1),
        ({"mode": "cases", "case_keys": ["case-{suffix}-0", "case-{suffix}-2"]}, 2),
    ],
)
def test_case_subset_shapes_create_the_same_number_of_cases(
    client: TestClient, session: Session, selection: dict, expected: int
) -> None:
    ids = seed_launch(session)
    suffix = session.get(DatasetVersion, ids["dataset_version_id"]).version.split("-")[-1]
    resolved = {
        "mode": selection["mode"],
        "count": selection.get("count"),
        "case_keys": [
            key.format(suffix=suffix) for key in (selection.get("case_keys") or [])
        ]
        or None,
    }
    prepared = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, case_selection=resolved),
    ).json()
    assert prepared["ready"] is True, prepared
    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": f"idem-{selection['mode']}-{expected}"},
    )
    assert created.status_code == 201, created.text
    assert created.json()["total_cases"] == expected


def test_run_detail_exposes_frozen_preparation_inputs(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    prepared = client.post(
        "/api/v1/evaluations/prepare",
        json=prepare_body(ids, case_selection={"mode": "count", "count": 2}),
    ).json()
    created = client.post(
        "/api/v1/evaluations",
        json={"preparation_id": prepared["preparation_id"]},
        headers={"Idempotency-Key": "idem-trace"},
    )
    assert created.status_code == 201, created.text
    detail = client.get(f"/api/v1/evaluations/{created.json()['id']}").json()
    assert detail["case_timeout_seconds"] == 1800
    assert detail["max_attempts"] == 3
    assert detail["consistency"]["preparation_id"] == prepared["preparation_id"]
    assert detail["consistency"]["summary"]["execution"]["concurrency"] == 2
    assert detail["consistency"]["summary"]["case_count"] == 2
