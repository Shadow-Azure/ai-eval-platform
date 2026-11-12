"""M3-04 real evaluation creation and runner task view."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from app.models import (
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalCaseRun,
    EvalRun,
    Outbox,
    ResourcePool,
    Runner,
)
from app.seed import ensure_default_scope
from case_helpers import auth
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_shard_tasks_returns_frozen_cases(eval_env, session: Session, client) -> None:
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == eval_env["run_id"]))
    assert outbox is not None
    response = client.get(
        f"/runner/v1/shards/{eval_env['shard']}/tasks",
        params={
            "eval_run_id": eval_env["run_id"],
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
        headers=auth(eval_env),
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["eval_run_id"] == eval_env["run_id"]
    assert len(body["cases"]) == 2
    assert body["runtime"]["key"] == "claude-code"
    assert {case["case_key"] for case in body["cases"]} == {"case-a", "case-b"}


def test_shard_tasks_requires_attempt_token(eval_env, session: Session, client) -> None:
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == eval_env["run_id"]))
    assert outbox is not None
    response = client.get(
        f"/runner/v1/shards/{eval_env['shard']}/tasks",
        params={
            "eval_run_id": eval_env["run_id"],
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
    )
    assert response.status_code == 401


def _seed_dataset(
    session: Session,
    *,
    execution_image_digest: str | None = None,
    scoring_image_digest: str | None = None,
) -> DatasetVersion:
    tenant, _workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug=f"ds-{suffix}",
        name="SWE-bench real",
        adapter_key="swebench",
    )
    session.add(dataset)
    session.flush()
    version = DatasetVersion(
        tenant_id=tenant.id,
        dataset_id=dataset.id,
        version=f"v-{suffix}",
        source_uri="fixture://swebench",
        source_revision="abcdef123456",
        adapter_version="1.0",
        case_count=1,
        status="active",
        execution_image_digest=execution_image_digest,
        scoring_image_digest=scoring_image_digest,
    )
    session.add(version)
    session.flush()
    session.add(
        DatasetCase(
            tenant_id=tenant.id,
            dataset_version_id=version.id,
            case_key="astropy__astropy-12907",
            ordinal=1,
            repo="astropy/astropy",
            base_commit="d16bfe05",
            problem_statement="Fix the separable model bug.",
            test_patch="diff --git a/tests b/tests\n",
            expected_metrics_json={"FAIL_TO_PASS": ["a"]},
        )
    )
    pool = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"pool-{suffix}",
        provider_key="fixed",
        labels_json={"kind": "fixed"},
        status="active",
    )
    session.add(pool)
    session.flush()
    session.add(
        Runner(
            tenant_id=tenant.id,
            resource_pool_id=pool.id,
            provider_runner_id=f"runner-{suffix}",
            name="fixed-linux-eval",
            labels_json=["self-hosted", "linux", "eval"],
            status="online",
            last_heartbeat_at=datetime.now(UTC),
        )
    )
    session.commit()
    return version


def test_create_real_evaluation_freezes_case_task(client, session: Session) -> None:
    version = _seed_dataset(session)
    config = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "minimax",
            "display_name": "MiniMax M2.7",
            "base_url": "https://api.minimaxi.com/anthropic",
            "model_name": "MiniMax-M2.7-highspeed",
            "api_style": "anthropic",
            "secret": "sk-real-test-123456",
        },
    ).json()
    pool = session.scalar(select(ResourcePool).where(ResourcePool.name.like("pool-%")))
    assert pool is not None
    response = client.post(
        "/api/v1/evaluations",
        json={
            "name": "真实评测",
            "dataset_version_id": version.id,
            "model_config_id": config["id"],
            "model_name": "MiniMax-M2.7-highspeed",
            "runtime_key": "claude-code",
            "resource_pool_id": pool.id,
            "scoring_image_digest": "sha256:" + "a" * 64,
            "execution_image_digest": "sha256:" + "b" * 64,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["dataset_version_id"] == version.id
    assert body["runtime_key"] == "claude-code"
    assert body["execution_image_digest"] == "sha256:" + "b" * 64

    case = session.scalar(select(EvalCaseRun).where(EvalCaseRun.eval_run_id == body["id"]))
    assert case is not None
    assert case.dataset_case_id != case.case_key
    assert case.task_json["repo"] == "astropy/astropy"
    assert case.task_json["problem_statement"].startswith("Fix the separable")


def test_create_real_evaluation_inherits_dataset_version_images(
    client, session: Session
) -> None:
    execution = "sha256:" + "c" * 64
    scoring = "sha256:" + "d" * 64
    version = _seed_dataset(
        session,
        execution_image_digest=execution,
        scoring_image_digest=scoring,
    )
    config = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "minimax",
            "display_name": "MiniMax M2.7",
            "base_url": "https://api.minimaxi.com/anthropic",
            "model_name": "MiniMax-M2.7-highspeed",
            "secret": "sk-real-test-123456",
        },
    ).json()
    pool = session.scalar(select(ResourcePool).where(ResourcePool.name.like("pool-%")))
    assert pool is not None
    response = client.post(
        "/api/v1/evaluations",
        json={
            "dataset_version_id": version.id,
            "model_config_id": config["id"],
            "resource_pool_id": pool.id,
        },
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["execution_image_digest"] == execution
    assert body["scoring_image_digest"] == scoring


def test_create_real_evaluation_blocks_without_image(client, session: Session) -> None:
    version = _seed_dataset(session)
    config = client.post(
        "/api/v1/model-configs",
        json={
            "provider": "minimax",
            "display_name": "MiniMax M2.7",
            "base_url": "https://api.minimaxi.com/anthropic",
            "model_name": "MiniMax-M2.7-highspeed",
            "secret": "sk-real-test-123456",
        },
    ).json()
    pool = session.scalar(select(ResourcePool).where(ResourcePool.name.like("pool-%")))
    response = client.post(
        "/api/v1/evaluations",
        json={
            "dataset_version_id": version.id,
            "model_config_id": config["id"],
            "resource_pool_id": pool.id,
        },
    )
    assert response.status_code == 422
    detail = response.json()["detail"]["error"]
    assert detail["code"] == "preparation_blocked"
    codes = {item["code"] for item in detail["details"]["blockers"]}
    assert {"scoring_image_missing", "execution_image_missing"} <= codes


def test_shard_tasks_includes_frozen_asset_snapshot(eval_env, session: Session, client) -> None:
    run = session.get(EvalRun, eval_env["run_id"])
    assert run is not None
    run.asset_snapshot_json = {
        "snapshot_id": "snap-1",
        "resolved_commit": "a" * 40,
        "tree_digest": "sha256:" + "b" * 64,
        "bundle_key": "agent-assets/default/snap-1/bundle.json.gz",
        "bundle_digest": "sha256:" + "c" * 64,
        "asset_count": 1,
        "file_count": 1,
        "runtime_setup": {
            "install_command": "npm install -g @fission-ai/openspec",
            "init_command": "openspec init",
        },
        "assets": [{"asset_type": "skill", "name": "brainstorming", "path": "skills/brainstorming"}],
    }
    session.commit()
    outbox = session.scalar(select(Outbox).where(Outbox.aggregate_id == eval_env["run_id"]))
    assert outbox is not None
    response = client.get(
        f"/runner/v1/shards/{eval_env['shard']}/tasks",
        params={
            "eval_run_id": eval_env["run_id"],
            "attempt": 1,
            "dispatch_key": outbox.dispatch_key,
        },
        headers=auth(eval_env),
    )
    assert response.status_code == 200, response.text
    snapshot = response.json()["asset_snapshot"]
    assert snapshot["snapshot_id"] == "snap-1"
    assert snapshot["resolved_commit"] == "a" * 40
    assert snapshot["runtime_setup"] == {
        "install_command": "npm install -g @fission-ai/openspec",
        "init_command": "openspec init",
    }
    assert snapshot["bundle_url"].startswith("http://")
