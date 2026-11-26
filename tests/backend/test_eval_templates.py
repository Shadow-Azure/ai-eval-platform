"""M10 (#378): reusable evaluation templates and the three execution APIs.

The template itself only freezes selections. These tests pin the contract:
list filtering/pagination, save validation, one-click launch through the
existing prepare/create path, idempotent replay, reference validation, and the
stable status/results read models.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from decimal import Decimal
from uuid import uuid4

import pytest
from app.config import get_settings
from app.models import (
    AgentAssetSnapshot,
    AgentAssetSource,
    Credential,
    Dataset,
    DatasetCase,
    DatasetVersion,
    EvalRun,
    EvalTemplate,
    ModelConfig,
    ResourcePool,
    Runner,
)
from app.secrets import get_secret_box
from app.seed import ensure_default_scope
from app.services import agent_assets, identity
from app.services import eval_templates as template_service
from app.services.git_client import GitClientError, TreeEntry
from app.services.image_prep_health import record_image_prep_heartbeat
from conftest import TEST_ADMIN_EMAIL, TEST_ADMIN_PASSWORD
from fastapi.testclient import TestClient
from object_stub import StubObjectStore
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

SELF_EVOLUTION_TOKEN = "whk_" + "a1B2c3D4e5" * 3
SELF_EVOLUTION = {
    "enabled": True,
    "base_url": "https://harness.example.com",
    "workspace_id": "ws-prod-01",
    "token": SELF_EVOLUTION_TOKEN,
}


def _decrypt(ciphertext: str) -> str:
    box = get_secret_box()
    return box.decrypt(ciphertext, box.version)


def seed_launch(
    session: Session,
    *,
    case_count: int = 4,
    runner_count: int = 2,
    execution_image_digest: str | None = "sha256:" + "a" * 64,
    scoring_image_digest: str | None = "sha256:" + "b" * 64,
) -> dict:
    tenant, workspace, project = ensure_default_scope(session)
    suffix = uuid4().hex[:8]
    dataset = Dataset(
        tenant_id=tenant.id,
        project_id=project.id,
        slug=f"ds-{suffix}",
        name="SWE-bench Verified",
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
        execution_image_digest=execution_image_digest,
        scoring_image_digest=scoring_image_digest,
        agent_timeout_seconds=1800,
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
    pool_label = f"pool-{suffix}"
    pool = ResourcePool(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"Linux Spot {suffix}",
        provider_key="fixed",
        labels_json={
            "runs_on": ["self-hosted", "linux", "eval", pool_label]
        },
        status="active",
        max_capacity=4,
        price_cny=2.5,
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
                labels_json=["self-hosted", "linux", "eval", pool_label],
                status="online",
            )
        )
    session.commit()
    return {
        "tenant": tenant,
        "workspace": workspace,
        "project": project,
        "dataset_version_id": version.id,
        "model_config_id": config.id,
        "resource_pool_id": pool.id,
        "version": version,
        "config": config,
        "pool": pool,
        "dataset": dataset,
    }


def template_body(ids: dict, **overrides) -> dict:
    body = {
        "name": "日常回归 · Verified",
        "description": "固定数据集、Agent 资产、模型与资源池",
        "dataset_version_id": ids["dataset_version_id"],
        "model_config_id": ids["model_config_id"],
        "resource_pool_id": ids["resource_pool_id"],
        "runtime_key": "claude-code",
        "case_selection": {"mode": "all"},
        "execution": {
            "concurrency": 2,
            "case_timeout_seconds": 4200,
            "max_attempts": 2,
            "model_budget_cny": 5,
        },
        "max_turns": 120,
        "agent_timeout_seconds": 1200,
    }
    body.update(overrides)
    return body


def create_template(client: TestClient, ids: dict, **overrides) -> dict:
    response = client.post("/api/v1/eval-templates", json=template_body(ids, **overrides))
    assert response.status_code == 201, response.text
    return response.json()["template"]


def test_create_and_get_template_freezes_selection(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)

    assert template["name"] == "日常回归 · Verified"
    assert template["enabled"] is True
    assert template["version"] == 1
    assert template["launch_count"] == 0
    assert template["dataset_name"] == "SWE-bench Verified"
    assert template["dataset_version"] == ids["version"].version
    assert template["case_count"] == 4
    assert template["model_name"] == "MiniMax-M2.7-highspeed"
    assert template["resource_pool_name"] == ids["pool"].name
    assert template["execution"]["case_timeout_seconds"] == 4200
    assert template["estimated_cost"]["total_cny"]["low"]

    detail = client.get(f"/api/v1/eval-templates/{template['id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["template"]["recent_runs"] == []

    missing = client.get("/api/v1/eval-templates/does-not-exist")
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "template_not_found"


def test_template_list_filters_status_search_and_pagination(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    first = create_template(client, ids, name="GLM 夜间回归")
    second = create_template(client, ids, name="MiniMax 冒烟")
    third = create_template(client, ids, name="已停用模板", enabled=False)

    listing = client.get("/api/v1/eval-templates").json()
    assert listing["total"] == 3
    assert listing["limit"] == 20
    assert listing["offset"] == 0
    assert {item["id"] for item in listing["items"]} == {
        first["id"],
        second["id"],
        third["id"],
    }

    enabled = client.get("/api/v1/eval-templates", params={"status": "enabled"}).json()
    assert {item["id"] for item in enabled["items"]} == {first["id"], second["id"]}
    disabled = client.get("/api/v1/eval-templates", params={"status": "disabled"}).json()
    assert [item["id"] for item in disabled["items"]] == [third["id"]]

    by_name = client.get("/api/v1/eval-templates", params={"q": "GLM"}).json()
    assert [item["id"] for item in by_name["items"]] == [first["id"]]
    by_model = client.get("/api/v1/eval-templates", params={"q": "MiniMax"}).json()
    assert {item["id"] for item in by_model["items"]} == {
        first["id"],
        second["id"],
        third["id"],
    }
    by_dataset = client.get(
        "/api/v1/eval-templates", params={"q": "SWE-bench Verified"}
    ).json()
    assert by_dataset["total"] == 3
    by_creator = client.get("/api/v1/eval-templates", params={"q": "admin@test.local"}).json()
    assert by_creator["total"] == 3

    page = client.get(
        "/api/v1/eval-templates",
        params={"limit": 1, "offset": 1, "q": "MiniMax"},
    ).json()
    assert page["total"] == 3
    assert len(page["items"]) == 1

    invalid = client.get("/api/v1/eval-templates", params={"status": "unknown"})
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["error"]["code"] == "template_invalid"


def test_template_update_disable_and_version_conflict(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)

    updated = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        params={"version": 1},
        json={"name": "日常回归 · v2", "enabled": False, "prompt_suffix": "非交互"},
    )
    assert updated.status_code == 200, updated.text
    body = updated.json()["template"]
    assert body["name"] == "日常回归 · v2"
    assert body["enabled"] is False
    assert body["version"] == 2
    assert body["prompt_suffix"] == "非交互"

    conflict = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        params={"version": 1},
        json={"name": "过期版本"},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"]["code"] == "template_version_conflict"

    response = client.delete(f"/api/v1/eval-templates/{template['id']}")
    assert response.status_code == 204
    assert client.get(f"/api/v1/eval-templates/{template['id']}").status_code == 404


def test_template_save_rejects_empty_name_and_invalid_references(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    empty_name = client.post(
        "/api/v1/eval-templates", json=template_body(ids, name="   ")
    )
    assert empty_name.status_code == 422
    error = empty_name.json()["detail"]["error"]
    assert error["code"] == "template_invalid"
    assert error["details"]["fields"]["name"] == "invalid"

    missing_dataset = client.post(
        "/api/v1/eval-templates",
        json=template_body(ids, dataset_version_id="missing-dataset"),
    )
    assert missing_dataset.status_code == 422
    assert missing_dataset.json()["detail"]["error"]["details"]["fields"]["dataset_version_id"] == "invalid"

    missing_pool = client.post(
        "/api/v1/eval-templates",
        json=template_body(ids, resource_pool_id="missing-pool"),
    )
    assert missing_pool.status_code == 422

    tenant, _workspace, project = ensure_default_scope(session)
    source = AgentAssetSource(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"assets-{uuid4().hex[:8]}",
        repo_url="https://github.com/obra/superpowers",
        default_ref="",
        scan_paths_json=["skills"],
        selection_mode="explicit",
        runtime_prerequisites_json={},
        last_scan_json={},
        status="ready",
    )
    session.add(source)
    session.commit()
    unresolved = client.post(
        "/api/v1/eval-templates",
        json=template_body(ids, asset_source_id=source.id),
    )
    assert unresolved.status_code == 422
    assert unresolved.json()["detail"]["error"]["details"]["fields"]["asset_ref"] == "invalid"


def test_template_save_rejects_missing_image_digests(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(
        session, execution_image_digest=None, scoring_image_digest=None
    )

    response = client.post("/api/v1/eval-templates", json=template_body(ids))
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["code"] == "template_save_blocked"
    blockers = {blocker["code"]: blocker for blocker in error["details"]["blockers"]}
    assert set(blockers) == {"execution_image_missing", "scoring_image_missing"}
    assert blockers["execution_image_missing"]["field"] == "execution.execution_image_digest"
    assert blockers["scoring_image_missing"]["field"] == "execution.scoring_image_digest"
    assert error["details"]["fields"] == {
        "execution.execution_image_digest": "invalid",
        "execution.scoring_image_digest": "invalid",
    }
    assert "执行镜像 digest 缺失" in error["message"]
    assert client.get("/api/v1/eval-templates").json()["total"] == 0


def test_template_save_accepts_platform_default_digests(
    client: TestClient, session: Session, monkeypatch
) -> None:
    ids = seed_launch(
        session, execution_image_digest=None, scoring_image_digest=None
    )
    settings = get_settings().model_copy(
        update={
            "default_execution_image_digest": "sha256:" + "c" * 64,
            "default_scoring_image_digest": "sha256:" + "d" * 64,
        }
    )
    monkeypatch.setattr(template_service, "get_settings", lambda: settings)

    response = client.post("/api/v1/eval-templates", json=template_body(ids))
    assert response.status_code == 201, response.text
    template = response.json()["template"]
    assert template["preflight"] == {"status": "ready", "blockers": []}


def test_template_preflight_blocks_existing_template_and_recovers(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    assert template["preflight"] == {"status": "ready", "blockers": []}

    version = session.get(DatasetVersion, ids["dataset_version_id"])
    assert version is not None
    version.execution_image_digest = None
    version.scoring_image_digest = None
    session.commit()

    detail = client.get(f"/api/v1/eval-templates/{template['id']}").json()["template"]
    assert detail["preflight"]["status"] == "blocked"
    assert {blocker["code"] for blocker in detail["preflight"]["blockers"]} == {
        "execution_image_missing",
        "scoring_image_missing",
    }
    listing = client.get("/api/v1/eval-templates").json()["items"]
    stored = next(item for item in listing if item["id"] == template["id"])
    assert stored["preflight"]["status"] == "blocked"

    rejected = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={"dataset_version_id": ids["dataset_version_id"]},
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["error"]["code"] == "template_save_blocked"

    # A blocked template must still be renameable/disableable: those edits do
    # not create a new blocked launch configuration.
    renamed = client.patch(
        f"/api/v1/eval-templates/{template['id']}", json={"name": "阻塞中改名"}
    )
    assert renamed.status_code == 200, renamed.text
    disabled = client.patch(
        f"/api/v1/eval-templates/{template['id']}", json={"enabled": False}
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["template"]["enabled"] is False
    reenabled = client.patch(
        f"/api/v1/eval-templates/{template['id']}", json={"enabled": True}
    )
    assert reenabled.status_code == 200, reenabled.text

    blocked_launch = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "blocked-image-template"},
        json={},
    )
    assert blocked_launch.status_code == 422
    launch_error = blocked_launch.json()["detail"]["error"]
    assert launch_error["code"] == "template_launch_blocked"
    assert {blocker["code"] for blocker in launch_error["details"]["blockers"]} == {
        "execution_image_missing",
        "scoring_image_missing",
    }

    version.execution_image_digest = "sha256:" + "a" * 64
    version.scoring_image_digest = "sha256:" + "b" * 64
    session.commit()
    recovered = client.get(f"/api/v1/eval-templates/{template['id']}").json()["template"]
    assert recovered["preflight"] == {"status": "ready", "blockers": []}
    assert (
        client.patch(
            f"/api/v1/eval-templates/{template['id']}", json={"enabled": True}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/eval-templates/{template['id']}", json={"name": "恢复后可保存"}
        ).status_code
        == 200
    )


def test_launch_creates_run_with_template_provenance_and_replays(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)

    first = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "nightly-20261122"},
        json={"name": "夜间回归"},
    )
    assert first.status_code == 202, first.text
    body = first.json()
    assert body["template_id"] == template["id"]
    assert body["status"] == "queued"
    assert body["status_url"] == f"/api/v1/eval-runs/{body['run_id']}/status"
    assert body["result_url"] == f"/api/v1/eval-runs/{body['run_id']}/results"
    assert body["created"] is True

    stored = session.get(EvalRun, body["run_id"])
    assert stored is not None
    assert stored.template_id == template["id"]
    assert stored.template_version == 1
    assert stored.template_snapshot_json["name"] == template["name"]
    assert stored.dataset_version_id == ids["dataset_version_id"]

    detail = client.get(f"/api/v1/evaluations/{body['run_id']}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["template"]["id"] == template["id"]
    assert detail.json()["template_version"] == 1

    replay = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "nightly-20261122"},
        json={"name": "夜间回归"},
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == body["run_id"]
    assert replay.json()["created"] is False

    # Replay still wins after the template is disabled: a lost response must
    # never turn into a second run or a misleading 409.
    disabled = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    after_disable = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "nightly-20261122"},
        json={"name": "夜间回归"},
    )
    assert after_disable.status_code == 202, after_disable.text
    assert after_disable.json()["run_id"] == body["run_id"]
    client.patch(f"/api/v1/eval-templates/{template['id']}", json={"enabled": True})

    refreshed = client.get(f"/api/v1/eval-templates/{template['id']}").json()["template"]
    assert refreshed["launch_count"] == 1
    assert refreshed["last_launched_at"] is not None
    assert refreshed["recent_runs"][0]["id"] == body["run_id"]


def test_evaluation_list_filters_by_template_history(
    client: TestClient, session: Session
) -> None:
    """M10 (#394): the read-only history reuses the evaluation list contract."""

    ids = seed_launch(session)
    template = create_template(client, ids)
    other = create_template(client, ids, name="另一个模板")

    first = client.post(f"/api/v1/eval-templates/{template['id']}/launch", json={})
    second = client.post(f"/api/v1/eval-templates/{template['id']}/launch", json={})
    unrelated = client.post(f"/api/v1/eval-templates/{other['id']}/launch", json={})
    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert unrelated.status_code == 202, unrelated.text

    history = client.get(
        "/api/v1/evaluations",
        params={"template_id": template["id"]},
    )
    assert history.status_code == 200, history.text
    body = history.json()
    assert body["total"] == 2
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert body["status_counts"]["queued"] == 2
    assert body["status_counts"]["completed"] == 0
    assert sum(body["status_counts"].values()) == 2
    assert {item["id"] for item in body["items"]} == {
        first.json()["run_id"],
        second.json()["run_id"],
    }
    assert all(item["template_id"] == template["id"] for item in body["items"])
    assert all(item["template_version"] == 1 for item in body["items"])
    # M10 (#410): each row carries the resolved template name for the list
    # origin line, not just the opaque id.
    assert all(item["template"]["id"] == template["id"] for item in body["items"])
    assert all(item["template"]["name"] == template["name"] for item in body["items"])
    assert all(item["template"]["version"] == 1 for item in body["items"])
    assert body["stats"]["total"] == 2

    page = client.get(
        "/api/v1/evaluations",
        params={"template_id": template["id"], "limit": 1, "offset": 1},
    ).json()
    assert page["total"] == 2
    assert page["limit"] == 1
    assert page["offset"] == 1
    assert len(page["items"]) == 1
    assert page["items"][0]["id"] in {first.json()["run_id"], second.json()["run_id"]}

    queued = client.get(
        "/api/v1/evaluations",
        params={"template_id": template["id"], "status": "queued"},
    ).json()
    assert queued["total"] == 2

    completed = client.get(
        "/api/v1/evaluations",
        params={"template_id": template["id"], "status": "completed"},
    ).json()
    assert completed["total"] == 0
    # Counts stay unfiltered by status so the selector keeps every option.
    assert completed["status_counts"]["queued"] == 2

    other_history = client.get(
        "/api/v1/evaluations", params={"template_id": other["id"]}
    ).json()
    assert other_history["total"] == 1
    assert other_history["items"][0]["id"] == unrelated.json()["run_id"]

    missing = client.get("/api/v1/evaluations", params={"template_id": "missing"})
    assert missing.status_code == 404
    assert missing.json()["detail"]["error"]["code"] == "template_not_found"


def test_launch_rejects_disabled_missing_and_reused_idempotency(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    other = create_template(client, ids, name="另一个模板")

    missing = client.post("/api/v1/eval-templates/missing/launch", json={})
    assert missing.status_code == 404

    disabled = create_template(client, ids, name="停用模板", enabled=False)
    blocked = client.post(f"/api/v1/eval-templates/{disabled['id']}/launch", json={})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["error"]["code"] == "template_disabled"

    created = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "shared-key"},
        json={},
    )
    assert created.status_code == 202, created.text
    conflict = client.post(
        f"/api/v1/eval-templates/{other['id']}/launch",
        headers={"Idempotency-Key": "shared-key"},
        json={},
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["error"]["code"] == "idempotency_conflict"


def test_delete_disabled_template_preserves_started_runs(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    created = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "delete-keeps-run"},
        json={},
    )
    assert created.status_code == 202, created.text
    run_id = created.json()["run_id"]

    disabled = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        params={"version": template["version"]},
        json={"enabled": False},
    )
    assert disabled.status_code == 200, disabled.text

    deleted = client.delete(f"/api/v1/eval-templates/{template['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/eval-templates/{template['id']}").status_code == 404

    run = session.get(EvalRun, run_id)
    assert run is not None
    assert run.template_id == template["id"]
    assert run.template_version == template["version"]
    detail = client.get(f"/api/v1/evaluations/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["template"] is None


def test_launch_rejects_invalid_reference(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    # Simulate a pool removed outside this domain: the template row keeps the
    # stale id and the launch must fail with an explicit 422 before any run is
    # created.
    session.execute(
        text("UPDATE eval_template SET resource_pool_id = 'gone' WHERE id = :id"),
        {"id": template["id"]},
    )
    session.commit()

    response = client.post(f"/api/v1/eval-templates/{template['id']}/launch", json={})
    assert response.status_code == 422
    error = response.json()["detail"]["error"]
    assert error["code"] == "template_launch_blocked"
    assert error["details"]["fields"]["resource_pool_id"] == "invalid"
    assert session.scalar(
        text("SELECT count(*) FROM eval_run WHERE template_id = :id"), {"id": template["id"]}
    ) == 0


def test_launch_maps_missing_image_tarball_to_conflict(
    client: TestClient, session: Session
) -> None:
    """A template launch must surface the existing 409 instead of a 500."""

    ids = seed_launch(session)
    version = ids["version"]
    cases = list(
        session.scalars(
            select(DatasetCase)
            .where(DatasetCase.dataset_version_id == version.id)
            .order_by(DatasetCase.ordinal)
        )
    )
    for index, case in enumerate(cases):
        case.case_key = f"django__django-{10000 + index}"
    session.commit()
    template = create_template(client, ids, case_selection={"mode": "count", "count": 1})

    offline = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "image-gate-offline"},
        json={},
    )
    assert offline.status_code == 503, offline.text
    assert offline.json()["detail"]["error"]["code"] == "image_prep_unavailable"

    # A live worker turns the same launch into the documented 409.
    record_image_prep_heartbeat(session)
    response = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "image-gate"},
        json={},
    )
    assert response.status_code == 409, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "image_not_ready"
    assert error["details"]["counts"]["ready"] == 0


def test_launch_requires_admin(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    tenant = ids["tenant"]
    identity.upsert_member(
        session,
        email="viewer@test.local",
        password="test-viewer-password",
        tenant=tenant,
        workspace=ids["workspace"],
        role=identity.ROLE_VIEWER,
    )
    session.commit()

    client.cookies.clear()
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "viewer@test.local", "password": "test-viewer-password"},
    )
    assert login.status_code == 200, login.text

    readable = client.get("/api/v1/eval-templates")
    assert readable.status_code == 200
    denied = client.post(f"/api/v1/eval-templates/{template['id']}/launch", json={})
    assert denied.status_code == 403
    assert denied.json()["detail"]["error"]["code"] == "forbidden"


def test_status_and_results_read_models(client: TestClient, session: Session) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    launched = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "status-key"},
        json={},
    )
    assert launched.status_code == 202, launched.text
    run_id = launched.json()["run_id"]

    status_response = client.get(f"/api/v1/eval-runs/{run_id}/status")
    assert status_response.status_code == 200, status_response.text
    status = status_response.json()
    assert status["run_id"] == run_id
    assert status["template_id"] == template["id"]
    assert status["status"] == "queued"
    assert status["terminal"] is False
    assert status["total_cases"] == 4
    assert status["progress"]["total"] == 4
    assert status["status_url"].endswith(f"/{run_id}/status")

    results_response = client.get(f"/api/v1/eval-runs/{run_id}/results")
    assert results_response.status_code == 200, results_response.text
    results = results_response.json()
    assert results["run_id"] == run_id
    assert results["template_id"] == template["id"]
    assert results["is_final"] is False
    assert all(item["is_final"] is False for item in results["items"])
    assert "token_summary" in results

    run = session.get(EvalRun, run_id)
    assert run is not None
    run.status = "completed"
    run.finished_at = run.created_at
    session.commit()

    terminal = client.get(f"/api/v1/eval-runs/{run_id}/status").json()
    assert terminal["terminal"] is True
    final_results = client.get(f"/api/v1/eval-runs/{run_id}/results").json()
    assert final_results["is_final"] is True
    assert all(item["is_final"] is True for item in final_results["items"])

    missing = client.get("/api/v1/eval-runs/missing/status")
    assert missing.status_code == 404


def test_create_template_self_evolution_is_write_only(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/eval-templates",
        json=template_body(ids, self_evolution=SELF_EVOLUTION),
    )
    assert response.status_code == 201, response.text
    assert SELF_EVOLUTION_TOKEN not in response.text
    template = response.json()["template"]
    assert template["self_evolution"] == {
        "enabled": True,
        "base_url": "https://harness.example.com",
        "workspace_id": "ws-prod-01",
        "has_token": True,
    }
    assert "token" not in template["self_evolution"]

    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_enabled is True
    assert stored.self_evolution_base_url == "https://harness.example.com"
    assert stored.self_evolution_workspace_id == "ws-prod-01"
    assert stored.self_evolution_token_encrypted
    assert SELF_EVOLUTION_TOKEN not in stored.self_evolution_token_encrypted
    assert _decrypt(stored.self_evolution_token_encrypted) == SELF_EVOLUTION_TOKEN


def test_create_template_rejects_incomplete_self_evolution(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    invalid_configs = [
        {"enabled": True},
        {
            "enabled": True,
            "base_url": "https://harness.example.com",
            "workspace_id": "ws-prod-01",
        },
        {"enabled": True, "base_url": "https://harness.example.com", "workspace_id": "ws-1"},
        {**SELF_EVOLUTION, "base_url": "ftp://harness.example.com"},
        {**SELF_EVOLUTION, "workspace_id": "-ws"},
        {**SELF_EVOLUTION, "token": "gk_abc123"},
        {**SELF_EVOLUTION, "token": "whk_"},
    ]
    for config in invalid_configs:
        response = client.post(
            "/api/v1/eval-templates",
            json=template_body(ids, self_evolution=config),
        )
        assert response.status_code == 422, (config, response.text)
        error = response.json()["detail"]["error"]
        assert error["code"] == "template_invalid", config
        assert error["details"]["fields"]["self_evolution"] == "invalid", config
        assert SELF_EVOLUTION_TOKEN not in response.text


def test_create_template_rejects_disabled_self_evolution_with_fields(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    response = client.post(
        "/api/v1/eval-templates",
        json=template_body(
            ids,
            self_evolution={
                "enabled": False,
                "base_url": "https://harness.example.com",
                "workspace_id": "ws-prod-01",
                "token": SELF_EVOLUTION_TOKEN,
            },
        ),
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "template_invalid"
    assert error["details"]["fields"]["self_evolution"] == "invalid"
    assert SELF_EVOLUTION_TOKEN not in response.text


def test_update_self_evolution_keeps_token_when_omitted(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids, self_evolution=SELF_EVOLUTION)
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    original_ciphertext = stored.self_evolution_token_encrypted

    response = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={
            "self_evolution": {
                "enabled": True,
                "base_url": "https://harness-2.example.com",
                "workspace_id": "ws-prod-02",
            }
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()["template"]["self_evolution"]
    assert body == {
        "enabled": True,
        "base_url": "https://harness-2.example.com",
        "workspace_id": "ws-prod-02",
        "has_token": True,
    }

    session.expire_all()
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_token_encrypted == original_ciphertext
    assert _decrypt(stored.self_evolution_token_encrypted) == SELF_EVOLUTION_TOKEN


def test_update_self_evolution_replaces_token_and_disable_clears(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids, self_evolution=SELF_EVOLUTION)
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    original_ciphertext = stored.self_evolution_token_encrypted
    replacement = "whk_" + "z9Y8x7W6v5" * 2

    replaced = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={"self_evolution": {**SELF_EVOLUTION, "token": replacement}},
    )
    assert replaced.status_code == 200, replaced.text
    assert replaced.json()["template"]["self_evolution"]["has_token"] is True
    assert replacement not in replaced.text

    session.expire_all()
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_token_encrypted != original_ciphertext
    assert _decrypt(stored.self_evolution_token_encrypted) == replacement

    disabled = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={"self_evolution": {"enabled": False}},
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["template"]["self_evolution"] == {
        "enabled": False,
        "base_url": None,
        "workspace_id": None,
        "has_token": False,
    }

    session.expire_all()
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_enabled is False
    assert stored.self_evolution_base_url is None
    assert stored.self_evolution_workspace_id is None
    assert stored.self_evolution_token_encrypted is None


def test_update_without_self_evolution_keeps_config(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids, self_evolution=SELF_EVOLUTION)
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    original_ciphertext = stored.self_evolution_token_encrypted

    response = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={"name": "改名后的模板"},
    )
    assert response.status_code == 200, response.text
    body = response.json()["template"]
    assert body["name"] == "改名后的模板"
    assert body["self_evolution"] == {
        "enabled": True,
        "base_url": "https://harness.example.com",
        "workspace_id": "ws-prod-01",
        "has_token": True,
    }

    session.expire_all()
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_token_encrypted == original_ciphertext


def test_update_self_evolution_rejects_invalid_config(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids, self_evolution=SELF_EVOLUTION)
    response = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={"self_evolution": {"enabled": True}},
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "template_invalid"
    assert error["details"]["fields"]["self_evolution"] == "invalid"

    # The rejected update must not have touched the stored target.
    session.expire_all()
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_enabled is True
    assert stored.self_evolution_token_encrypted is not None


def test_update_enabling_self_evolution_requires_a_token(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    response = client.patch(
        f"/api/v1/eval-templates/{template['id']}",
        json={
            "self_evolution": {
                "enabled": True,
                "base_url": "https://harness.example.com",
                "workspace_id": "ws-prod-01",
            }
        },
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "template_invalid"
    assert error["details"]["fields"]["self_evolution"] == "invalid"

    # A rejected enable must leave the template disabled with no ciphertext.
    session.expire_all()
    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_enabled is False
    assert stored.self_evolution_base_url is None
    assert stored.self_evolution_workspace_id is None
    assert stored.self_evolution_token_encrypted is None


def test_launch_rejects_broken_self_evolution_target(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    missing = create_template(client, ids, name="缺少令牌", self_evolution=SELF_EVOLUTION)
    corrupted = create_template(client, ids, name="令牌损坏", self_evolution=SELF_EVOLUTION)
    session.execute(
        text(
            "UPDATE eval_template SET self_evolution_token_encrypted = NULL "
            "WHERE id = :id"
        ),
        {"id": missing["id"]},
    )
    session.execute(
        text(
            "UPDATE eval_template SET self_evolution_token_encrypted = 'not-a-token' "
            "WHERE id = :id"
        ),
        {"id": corrupted["id"]},
    )
    session.commit()

    for template in (missing, corrupted):
        response = client.post(
            f"/api/v1/eval-templates/{template['id']}/launch",
            headers={"Idempotency-Key": f"broken-{template['id']}"},
            json={},
        )
        assert response.status_code == 422, response.text
        error = response.json()["detail"]["error"]
        assert error["code"] == "template_invalid"
        assert error["details"]["fields"]["self_evolution"] == "invalid"
        assert session.scalar(
            text("SELECT count(*) FROM eval_run WHERE template_id = :id"),
            {"id": template["id"]},
        ) == 0


def test_launch_from_reporting_template_sets_run_self_evolution(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids, self_evolution=SELF_EVOLUTION)

    response = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "self-evolution-launch"},
        json={},
    )
    assert response.status_code == 202, response.text
    run = session.get(EvalRun, response.json()["run_id"])
    assert run is not None
    assert run.self_evolution_enabled is True
    assert run.self_evolution_base_url == "https://harness.example.com"
    assert run.self_evolution_workspace_id == "ws-prod-01"
    assert run.self_evolution_token_encrypted
    assert _decrypt(run.self_evolution_token_encrypted) == SELF_EVOLUTION_TOKEN

    snapshot = run.template_snapshot_json
    assert snapshot["self_evolution"] == {
        "enabled": True,
        "base_url": "https://harness.example.com",
        "workspace_id": "ws-prod-01",
        "has_token": True,
    }
    assert "token" not in snapshot["self_evolution"]
    assert SELF_EVOLUTION_TOKEN not in json.dumps(snapshot)
    assert SELF_EVOLUTION_TOKEN not in response.text


# ---------------------------------------------------------------------------
# M10 (#429): candidate commit injection + service-token launches
# ---------------------------------------------------------------------------

SOURCE_REPO = "https://github.com/acme/candidate-skills"
CANDIDATE_REPO = "https://github.com/acme/candidate-repo"
CANDIDATE_COMMIT = "c" * 40
BASE_COMMIT = "a" * 40
SKILL_PATH = "skills/candidate"
SKILL_MANIFEST = f"{SKILL_PATH}/SKILL.md"
SKILL_CONTENT = (
    b"---\nname: candidate\ndescription: Candidate skill\n---\n# Candidate\n"
)


@dataclass
class FakeRepo:
    """A tiny commit tree served by :class:`FakeGitHubClient` (no network)."""

    commit: str = CANDIDATE_COMMIT
    files: dict[str, bytes] | None = None
    # Every ref the server asked to resolve, in order. A commit-only launch
    # must never ask the provider to resolve the display ref.
    resolved_refs: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.files is None:
            self.files = {
                SKILL_MANIFEST: SKILL_CONTENT,
                f"{SKILL_PATH}/reference.md": b"reference\n",
            }


class FakeGitHubClient:
    """Minimal GitHub surface with failure and commit-drift injection."""

    def __init__(
        self,
        repo: FakeRepo,
        token: str | None = None,
        *,
        resolve_error: Exception | None = None,
        resolved_commit: str | None = None,
        ref_commit: str | None = None,
    ) -> None:
        self.repo = repo
        self.token = token
        self.resolve_error = resolve_error
        self.resolved_commit = resolved_commit
        self.ref_commit = ref_commit
        self.blobs = {
            hashlib.sha1(path.encode()).hexdigest(): content
            for path, content in repo.files.items()
        }

    def close(self) -> None:
        return None

    def resolve_commit(self, owner: str, repo: str, ref: str) -> str:
        if self.resolve_error is not None:
            raise self.resolve_error
        self.repo.resolved_refs.append(ref)
        if ref == self.repo.commit:
            # A full SHA resolves to itself; ``resolved_commit`` can force a
            # drift so the candidate_commit_mismatch assertion is exercised.
            return self.resolved_commit or self.repo.commit
        # A non-SHA ref resolves to the configured branch commit: a test that
        # supplies both ref and commit can prove the ref was never consulted.
        return self.ref_commit or self.repo.commit

    def tree(self, owner: str, repo: str, commit: str) -> list[TreeEntry]:
        return [
            TreeEntry(
                path=path,
                entry_type="blob",
                sha=hashlib.sha1(path.encode()).hexdigest(),
                size=len(content),
            )
            for path, content in self.repo.files.items()
        ]

    def blob(self, owner: str, repo: str, sha: str) -> bytes:
        return self.blobs[sha]


def install_fake_github(
    monkeypatch: pytest.MonkeyPatch,
    repo: FakeRepo | None = None,
    **client_kwargs: object,
) -> FakeRepo:
    repo = repo or FakeRepo()
    monkeypatch.setattr(
        agent_assets,
        "GitHubClient",
        lambda token=None, **_kwargs: FakeGitHubClient(repo, token, **client_kwargs),
    )
    return repo


@pytest.fixture
def asset_store(configure_settings):
    """Local S3-shaped store so ``resolve_assets`` exercises the real path."""

    with StubObjectStore() as store:
        configure_settings(
            S3_ENDPOINT=store.endpoint,
            S3_BUCKET="eval-artifacts",
            S3_ACCESS_KEY="ak",
            S3_SECRET_KEY="sk",
            S3_ADDRESSING_STYLE="path",
        )
        yield store


def create_asset_source(
    session: Session,
    repo_url: str = SOURCE_REPO,
    *,
    default_ref: str = "main",
) -> AgentAssetSource:
    tenant, _workspace, project = ensure_default_scope(session)
    source = AgentAssetSource(
        tenant_id=tenant.id,
        project_id=project.id,
        name=f"assets-{uuid4().hex[:8]}",
        repo_url=repo_url,
        provider="github",
        default_ref=default_ref,
        scan_paths_json=[],
        selection_mode="explicit",
        runtime_prerequisites_json={},
        last_scan_json={},
        status="ready",
    )
    session.add(source)
    session.commit()
    return source


def seed_candidate_template(
    client: TestClient, session: Session, *, repo_url: str = SOURCE_REPO
) -> tuple[dict, AgentAssetSource, dict]:
    ids = seed_launch(session)
    source = create_asset_source(session, repo_url)
    template = create_template(
        client,
        ids,
        asset_source_id=source.id,
        asset_ref="main",
        asset_paths=[SKILL_PATH],
    )
    return ids, source, template


def launch_override(
    client: TestClient,
    template_id: str,
    *,
    key: str,
    override: dict | None = None,
    lineage: dict | None = None,
    headers: dict | None = None,
):
    body: dict = {}
    if override is not None:
        body["asset_override"] = override
    if lineage is not None:
        body["lineage"] = lineage
    request_headers = {"Idempotency-Key": key}
    request_headers.update(headers or {})
    return client.post(
        f"/api/v1/eval-templates/{template_id}/launch",
        headers=request_headers,
        json=body,
    )


def _run_count(session: Session, template_id: str) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(EvalRun)
            .where(EvalRun.template_id == template_id)
        )
        or 0
    )


def _create_service_token(client: TestClient, **overrides: object) -> tuple[dict, str]:
    payload = {"name": f"bot-{uuid4().hex[:8]}", "scopes": ["eval:launch"]}
    payload.update(overrides)
    response = client.post("/api/v1/service-tokens", json=payload)
    assert response.status_code == 201, response.text
    body = response.json()
    return body["service_token"], body["token"]


def _record_spend(
    session: Session, *, created_by: str, budget_spent_cny: Decimal
) -> None:
    tenant, _workspace, project = ensure_default_scope(session)
    session.add(
        EvalRun(
            tenant_id=tenant.id,
            project_id=project.id,
            name="spend-record",
            idempotency_key=f"spend-{uuid4().hex[:8]}",
            created_by=created_by,
            budget_spent_cny=budget_spent_cny,
            status="completed",
        )
    )
    session.commit()


def test_launch_asset_override_pins_candidate_commit_and_echoes(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    install_fake_github(monkeypatch)
    _ids, source, template = seed_candidate_template(client, session)
    lineage = {
        "evolutionRunId": "evo-1",
        "evolutionRoundId": "round-7",
        "roundSeq": 7,
        "parentCommit": BASE_COMMIT,
    }

    response = launch_override(
        client,
        template["id"],
        key="candidate-launch",
        override={
            "repo_url": SOURCE_REPO,
            "ref": "refs/heads/candidate",
            "commit": CANDIDATE_COMMIT,
            "asset_paths": [SKILL_PATH],
            "asset_types": {SKILL_PATH: "skill"},
        },
        lineage=lineage,
    )
    assert response.status_code == 202, response.text
    body = response.json()
    asset = body["asset"]
    assert asset["repo_url"] == SOURCE_REPO
    # ``ref`` is display/audit only and must echo verbatim.
    assert asset["requested_ref"] == "refs/heads/candidate"
    assert asset["requested_commit"] == CANDIDATE_COMMIT
    assert asset["resolved_commit"] == CANDIDATE_COMMIT
    assert asset["asset_snapshot_id"]
    assert asset["bundle_digest"].startswith("sha256:")
    assert "warnings" not in body

    run = session.get(EvalRun, body["run_id"])
    assert run is not None
    assert run.asset_snapshot_id == asset["asset_snapshot_id"]
    assert run.asset_override_json["requested"]["commit"] == CANDIDATE_COMMIT
    assert run.asset_override_json["requested"]["ref"] == "refs/heads/candidate"
    assert run.asset_override_json["effective"] == {
        "repo_url": SOURCE_REPO,
        "source_id": source.id,
        "requested_ref": "refs/heads/candidate",
        "requested_commit": CANDIDATE_COMMIT,
        "resolved_commit": CANDIDATE_COMMIT,
        "asset_snapshot_id": asset["asset_snapshot_id"],
        "bundle_digest": asset["bundle_digest"],
        "asset_paths": [SKILL_PATH],
        "asset_types": {SKILL_PATH: "skill"},
    }
    assert run.evolution_lineage_json == lineage

    status = client.get(f"/api/v1/eval-runs/{body['run_id']}/status").json()
    assert status["asset"] == {
        "resolved_commit": CANDIDATE_COMMIT,
        "asset_snapshot_id": asset["asset_snapshot_id"],
        "requested_ref": "refs/heads/candidate",
    }
    results = client.get(f"/api/v1/eval-runs/{body['run_id']}/results").json()
    assert results["asset"] == status["asset"]
    assert results["lineage"] == lineage
    assert _run_count(session, template["id"]) == 1

    # The replay echoes the stored identity and never resolves assets again.
    replay = launch_override(
        client,
        template["id"],
        key="candidate-launch",
        override={"commit": CANDIDATE_COMMIT},
        lineage=lineage,
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["created"] is False
    assert replay.json()["run_id"] == body["run_id"]
    assert replay.json()["asset"] == asset
    assert _run_count(session, template["id"]) == 1


def test_launch_without_override_keeps_the_console_response_shape(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    install_fake_github(monkeypatch)
    _ids, _source, template = seed_candidate_template(client, session)

    response = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "console-launch"},
        json={"name": "控制台一键启动"},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert "asset" not in body
    assert "warnings" not in body

    run = session.get(EvalRun, body["run_id"])
    assert run is not None
    assert run.asset_snapshot_id
    assert run.asset_override_json == {}
    assert run.evolution_lineage_json == {}

    # The read models still echo the frozen legacy asset identity.
    status = client.get(f"/api/v1/eval-runs/{body['run_id']}/status").json()
    assert status["asset"] == {
        "resolved_commit": CANDIDATE_COMMIT,
        "asset_snapshot_id": run.asset_snapshot_id,
        "requested_ref": "main",
    }

    replay = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "console-launch"},
        json={"name": "控制台一键启动"},
    )
    assert replay.status_code == 202
    assert replay.json()["created"] is False
    assert "asset" not in replay.json()


def test_empty_asset_override_is_the_console_contract(
    client: TestClient, session: Session
) -> None:
    """``asset_override: {}`` has no intent: it must not demand an asset source."""

    ids = seed_launch(session)
    template = create_template(client, ids)

    response = launch_override(
        client, template["id"], key="empty-override", override={}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert "asset" not in body
    run = session.get(EvalRun, body["run_id"])
    assert run is not None
    assert run.asset_override_json == {}
    # Still a replayable idempotent launch with the console response shape.
    assert body["created"] is True


def test_commit_is_authoritative_even_when_ref_is_supplied(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    # ``main`` resolves to the base commit; only the candidate SHA may win.
    repo = install_fake_github(monkeypatch, ref_commit=BASE_COMMIT)
    _ids, _source, template = seed_candidate_template(client, session)

    response = launch_override(
        client,
        template["id"],
        key="commit-beats-ref",
        override={"ref": "main", "commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 202, response.text
    asset = response.json()["asset"]
    assert asset["resolved_commit"] == CANDIDATE_COMMIT
    assert asset["requested_ref"] == "main"
    # The provider was asked to resolve the commit, never the display ref.
    assert repo.resolved_refs == [CANDIDATE_COMMIT]
    status = client.get(f"/api/v1/eval-runs/{response.json()['run_id']}/status").json()
    assert status["asset"]["resolved_commit"] == CANDIDATE_COMMIT
    assert status["asset"]["requested_ref"] == "main"


def test_routes_that_dropped_router_dependencies_require_auth(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No route lost its guard when the router-level dependencies were removed."""

    install_fake_github(monkeypatch)
    ids = seed_launch(session)
    template = create_template(client, ids)
    launched = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "anonymous-guard"},
        json={},
    )
    assert launched.status_code == 202, launched.text
    run_id = launched.json()["run_id"]

    client.cookies.clear()
    protected = [
        ("GET", "/api/v1/eval-templates"),
        ("GET", f"/api/v1/eval-templates/{template['id']}"),
        ("PATCH", f"/api/v1/eval-templates/{template['id']}"),
        ("DELETE", f"/api/v1/eval-templates/{template['id']}"),
        ("POST", f"/api/v1/eval-templates/{template['id']}/launch"),
        ("GET", f"/api/v1/eval-runs/{run_id}/status"),
        ("GET", f"/api/v1/eval-runs/{run_id}/results"),
        ("GET", f"/api/v1/eval-runs/{run_id}/compare"),
        ("GET", "/api/v1/eval-case-runs/missing/tests"),
        ("GET", "/api/v1/service-tokens"),
        ("POST", "/api/v1/service-tokens"),
        ("DELETE", "/api/v1/service-tokens/missing"),
    ]
    for method, path in protected:
        response = client.request(method, path, json={})
        assert response.status_code == 401, (method, path, response.text)
        assert response.json()["detail"]["error"]["code"] == "unauthenticated"


def test_override_repo_url_must_be_a_registered_source(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    install_fake_github(monkeypatch)
    _ids, _source, template = seed_candidate_template(client, session)
    override = {"repo_url": CANDIDATE_REPO, "commit": CANDIDATE_COMMIT}

    unregistered = launch_override(
        client, template["id"], key="cross-repo", override=override
    )
    assert unregistered.status_code == 422, unregistered.text
    error = unregistered.json()["detail"]["error"]
    assert error["code"] == "asset_source_unresolved"
    assert error["details"]["fields"]["asset_override"] == "invalid"
    assert error["details"]["repo_url"] == CANDIDATE_REPO
    assert _run_count(session, template["id"]) == 0

    registered = create_asset_source(session, CANDIDATE_REPO)
    # The exact same request succeeds once the source is registered: the
    # override chooses the source by exact repo_url.
    retried = launch_override(
        client, template["id"], key="cross-repo", override=override
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["asset"]["repo_url"] == CANDIDATE_REPO
    run = session.get(EvalRun, retried.json()["run_id"])
    assert run is not None
    assert run.asset_override_json["effective"]["source_id"] == registered.id


def test_override_prefers_the_template_source_when_repo_url_has_candidates(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    """Same repo_url registered twice: the template's own source wins."""

    install_fake_github(monkeypatch)
    ids = seed_launch(session)
    create_asset_source(session, SOURCE_REPO)  # older, not bound to the template
    bound = create_asset_source(session, SOURCE_REPO)
    template = create_template(
        client,
        ids,
        asset_source_id=bound.id,
        asset_ref="main",
        asset_paths=[SKILL_PATH],
    )

    response = launch_override(
        client,
        template["id"],
        key="multi-source-template",
        override={"repo_url": SOURCE_REPO, "commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 202, response.text
    run = session.get(EvalRun, response.json()["run_id"])
    assert run is not None
    assert run.asset_override_json["effective"]["source_id"] == bound.id


def test_override_rejects_ambiguous_repo_url_without_a_template_source(
    client: TestClient, session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two candidates and no bound source: refuse to guess, never pick silently."""

    install_fake_github(monkeypatch)
    ids = seed_launch(session)
    create_asset_source(session, SOURCE_REPO)
    create_asset_source(session, SOURCE_REPO)
    template = create_template(client, ids)

    response = launch_override(
        client,
        template["id"],
        key="ambiguous-source",
        override={"repo_url": SOURCE_REPO, "commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "asset_source_unresolved"
    assert len(error["details"]["candidates"]) == 2
    assert _run_count(session, template["id"]) == 0


def test_override_unresolvable_commit_is_a_ref_error(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_github(
        monkeypatch,
        resolve_error=GitClientError("ref_unresolved", "无法解析 Ref"),
    )
    _ids, _source, template = seed_candidate_template(client, session)

    response = launch_override(
        client,
        template["id"],
        key="unresolvable-commit",
        override={"commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "asset_ref_unresolved"
    assert error["details"]["fields"]["asset_override"] == "invalid"
    assert error["details"]["requested_ref"] == CANDIDATE_COMMIT
    assert _run_count(session, template["id"]) == 0


def test_override_credential_failure_maps_to_ref_unresolved(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    """A broken source credential is a reference-resolution failure (issue 429)."""

    install_fake_github(monkeypatch)
    _ids, _source, template = seed_candidate_template(client, session)

    def fail_resolve(*_args: object, **_kwargs: object):
        raise agent_assets.AgentAssetError("credential_invalid", "Git 访问令牌无效")

    monkeypatch.setattr(template_service.asset_service, "resolve_assets", fail_resolve)
    response = launch_override(
        client,
        template["id"],
        key="credential-invalid",
        override={"commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "asset_ref_unresolved"
    assert error["details"]["asset_code"] == "credential_invalid"
    assert _run_count(session, template["id"]) == 0


def test_override_selection_errors_keep_the_existing_asset_codes(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_fake_github(monkeypatch)
    _ids, _source, template = seed_candidate_template(client, session)

    missing_path = launch_override(
        client,
        template["id"],
        key="missing-path",
        override={
            "commit": CANDIDATE_COMMIT,
            "asset_paths": ["missing/command.md"],
            "asset_types": {"missing/command.md": "command"},
        },
    )
    assert missing_path.status_code == 422, missing_path.text
    assert missing_path.json()["detail"]["error"]["code"] == "asset_path_not_found"

    # An explicitly empty list replaces the template selection wholesale.
    empty_selection = launch_override(
        client,
        template["id"],
        key="empty-selection",
        override={"commit": CANDIDATE_COMMIT, "asset_paths": []},
    )
    assert empty_selection.status_code == 422, empty_selection.text
    assert (
        empty_selection.json()["detail"]["error"]["code"] == "asset_selection_empty"
    )
    assert _run_count(session, template["id"]) == 0


def test_override_commit_mismatch_never_creates_a_run(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    # The source resolves the requested commit to a different object: the
    # authoritative assertion must fail the launch instead of measuring drift.
    install_fake_github(monkeypatch, resolved_commit=BASE_COMMIT)
    _ids, _source, template = seed_candidate_template(client, session)

    response = launch_override(
        client,
        template["id"],
        key="commit-mismatch",
        override={"commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 422, response.text
    error = response.json()["detail"]["error"]
    assert error["code"] == "candidate_commit_mismatch"
    assert error["details"]["requested"] == CANDIDATE_COMMIT
    assert error["details"]["resolved"] == BASE_COMMIT
    assert _run_count(session, template["id"]) == 0


def test_override_snapshot_manifest_proves_golden_injection(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    repo = install_fake_github(monkeypatch)
    _ids, _source, template = seed_candidate_template(client, session)

    response = launch_override(
        client,
        template["id"],
        key="golden-injection",
        override={"ref": "candidate", "commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 202, response.text
    snapshot_id = response.json()["asset"]["asset_snapshot_id"]
    snapshot = session.get(AgentAssetSnapshot, snapshot_id)
    assert snapshot is not None
    assert snapshot.resolved_commit == CANDIDATE_COMMIT

    expected = "sha256:" + hashlib.sha256(SKILL_CONTENT).hexdigest()
    manifest = {
        item["path"]: item for item in (snapshot.manifest_json or {}).get("files", [])
    }
    assert manifest[SKILL_MANIFEST]["sha256"] == expected

    # The manifest path is the same blob the pinned commit's tree serves.
    reader = FakeGitHubClient(repo)
    entries = {
        entry.path: entry
        for entry in reader.tree("acme", "candidate-skills", CANDIDATE_COMMIT)
    }
    assert entries[SKILL_MANIFEST].sha == hashlib.sha1(
        SKILL_MANIFEST.encode()
    ).hexdigest()
    assert (
        reader.blob("acme", "candidate-skills", entries[SKILL_MANIFEST].sha)
        == SKILL_CONTENT
    )


def test_override_on_reporting_template_warns_without_blocking(
    client: TestClient,
    session: Session,
    monkeypatch: pytest.MonkeyPatch,
    asset_store: StubObjectStore,
) -> None:
    install_fake_github(monkeypatch)
    ids = seed_launch(session)
    source = create_asset_source(session)
    template = create_template(
        client,
        ids,
        asset_source_id=source.id,
        asset_ref="main",
        asset_paths=[SKILL_PATH],
        self_evolution=SELF_EVOLUTION,
    )

    response = launch_override(
        client,
        template["id"],
        key="reporting-override",
        override={"commit": CANDIDATE_COMMIT},
    )
    assert response.status_code == 202, response.text
    warnings = response.json()["warnings"]
    assert len(warnings) == 1
    assert "自进化" in warnings[0]

    # The console one-click launch of the same reporting template stays quiet.
    console = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "reporting-console"},
        json={},
    )
    assert console.status_code == 202, console.text
    assert "warnings" not in console.json()


def test_service_token_launch_records_machine_actor_and_replays(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    token, plaintext = _create_service_token(
        client, name="launch-bot", scopes=["eval:launch"]
    )
    headers = {"Authorization": f"Bearer {plaintext}", "Idempotency-Key": "svc-key"}

    first = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch", headers=headers, json={}
    )
    assert first.status_code == 202, first.text
    run_id = first.json()["run_id"]
    run = session.get(EvalRun, run_id)
    assert run is not None
    assert run.created_by == "svc:launch-bot"

    detail = client.get(f"/api/v1/evaluations/{run_id}")
    assert detail.status_code == 200, detail.text
    assert detail.json()["created_by"] == "svc:launch-bot"
    assert detail.json()["template"]["id"] == template["id"]

    replay = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch", headers=headers, json={}
    )
    assert replay.status_code == 202, replay.text
    assert replay.json()["run_id"] == run_id
    assert replay.json()["created"] is False
    # The machine identity is isolated from the console actor's idempotency.
    assert token["name"] == "launch-bot"
    assert _run_count(session, template["id"]) == 1


def test_service_token_scope_and_allowlist_are_enforced(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    other = create_template(client, ids, name="另一个模板")

    _read_token, read_plaintext = _create_service_token(
        client, name="scope-read-bot", scopes=["eval:read"]
    )
    wrong_scope = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Authorization": f"Bearer {read_plaintext}"},
        json={},
    )
    assert wrong_scope.status_code == 403, wrong_scope.text
    assert wrong_scope.json()["detail"]["error"]["code"] == "forbidden"

    _scoped, scoped_plaintext = _create_service_token(
        client,
        name="allowlist-bot",
        scopes=["eval:launch"],
        template_allowlist=[other["id"]],
    )
    allowed = client.post(
        f"/api/v1/eval-templates/{other['id']}/launch",
        headers={
            "Authorization": f"Bearer {scoped_plaintext}",
            "Idempotency-Key": "allowlisted",
        },
        json={},
    )
    assert allowed.status_code == 202, allowed.text
    denied = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Authorization": f"Bearer {scoped_plaintext}"},
        json={},
    )
    assert denied.status_code == 403, denied.text
    assert _run_count(session, template["id"]) == 0


def test_revoked_service_token_cannot_launch(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    token, plaintext = _create_service_token(
        client, name="revoked-bot", scopes=["eval:launch"]
    )
    assert client.delete(f"/api/v1/service-tokens/{token['id']}").status_code == 204

    response = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={},
    )
    assert response.status_code == 401, response.text
    assert response.json()["detail"]["error"]["code"] == "unauthenticated"
    assert _run_count(session, template["id"]) == 0


def test_service_token_concurrency_cap_blocks_launch(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    _token, plaintext = _create_service_token(
        client,
        name="cap-bot",
        scopes=["eval:launch"],
        max_concurrent_runs=1,
    )
    auth = {"Authorization": f"Bearer {plaintext}"}

    first = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={**auth, "Idempotency-Key": "cap-1"},
        json={},
    )
    assert first.status_code == 202, first.text

    blocked = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={**auth, "Idempotency-Key": "cap-2"},
        json={},
    )
    assert blocked.status_code == 429, blocked.text
    error = blocked.json()["detail"]["error"]
    assert error["code"] == "service_token_limit_exceeded"
    assert error["details"]["limit"] == "max_concurrent_runs"
    assert _run_count(session, template["id"]) == 1


def test_service_token_daily_budget_cap_blocks_launch(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    _token, plaintext = _create_service_token(
        client,
        name="budget-bot",
        scopes=["eval:launch"],
        daily_budget_cny=5,
    )
    _record_spend(session, created_by="svc:budget-bot", budget_spent_cny=Decimal("5"))

    blocked = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Authorization": f"Bearer {plaintext}"},
        json={},
    )
    assert blocked.status_code == 429, blocked.text
    error = blocked.json()["detail"]["error"]
    assert error["code"] == "service_token_limit_exceeded"
    assert error["details"]["limit"] == "daily_budget_cny"
    assert Decimal(str(error["details"]["spent"])) == Decimal("5")
    assert _run_count(session, template["id"]) == 0


def test_eval_read_token_reads_status_results_and_compare_stays_session_only(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    launched = client.post(
        f"/api/v1/eval-templates/{template['id']}/launch",
        headers={"Idempotency-Key": "read-scope"},
        json={},
    )
    assert launched.status_code == 202, launched.text
    run_id = launched.json()["run_id"]

    _read_token, read_plaintext = _create_service_token(
        client, name="reader-bot", scopes=["eval:read"]
    )
    read_auth = {"Authorization": f"Bearer {read_plaintext}"}
    status = client.get(f"/api/v1/eval-runs/{run_id}/status", headers=read_auth)
    assert status.status_code == 200, status.text
    results = client.get(f"/api/v1/eval-runs/{run_id}/results", headers=read_auth)
    assert results.status_code == 200, results.text

    _launch_token, launch_plaintext = _create_service_token(
        client, name="launch-only-bot", scopes=["eval:launch"]
    )
    launch_auth = {"Authorization": f"Bearer {launch_plaintext}"}
    assert (
        client.get(f"/api/v1/eval-runs/{run_id}/status", headers=launch_auth).status_code
        == 403
    )
    assert (
        client.get(
            f"/api/v1/eval-runs/{run_id}/results", headers=launch_auth
        ).status_code
        == 403
    )

    # compare/case-tests kept their session-only dependency: a bearer token
    # alone (no cookie) must not reach them, while a console session still can.
    client.cookies.clear()
    denied = client.get(f"/api/v1/eval-runs/{run_id}/compare", headers=read_auth)
    assert denied.status_code == 401, denied.text
    assert denied.json()["detail"]["error"]["code"] == "unauthenticated"
    login = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_ADMIN_EMAIL, "password": TEST_ADMIN_PASSWORD},
    )
    assert login.status_code == 200, login.text
    assert client.get(f"/api/v1/eval-runs/{run_id}/compare").status_code == 200
    assert client.get(f"/api/v1/eval-runs/{run_id}/status").status_code == 200


def test_existing_templates_default_to_self_evolution_disabled(
    client: TestClient, session: Session
) -> None:
    ids = seed_launch(session)
    template = create_template(client, ids)
    assert template["self_evolution"] == {
        "enabled": False,
        "base_url": None,
        "workspace_id": None,
        "has_token": False,
    }

    stored = session.get(EvalTemplate, template["id"])
    assert stored is not None
    assert stored.self_evolution_enabled is False
    assert stored.self_evolution_base_url is None
    assert stored.self_evolution_workspace_id is None
    assert stored.self_evolution_token_encrypted is None
