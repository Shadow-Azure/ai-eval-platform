"""M5-18 delivery pipeline page and controlled rollback API."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.models import AuditLog, Deployment
from app.services import deployments as deployment_service
from app.services.github_dispatch import GitHubDispatchError
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

REPO_ROOT = Path(__file__).resolve().parents[2]


def add_viewer(session: Session):
    from app.models import Tenant, Workspace
    from app.services import identity

    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    workspace = session.scalar(select(Workspace).where(Workspace.tenant_id == tenant.id))
    user = identity.upsert_member(
        session,
        email=TEST_VIEWER_EMAIL,
        password=TEST_VIEWER_PASSWORD,
        tenant=tenant,
        workspace=workspace,
        role=identity.ROLE_VIEWER,
        is_default=True,
    )
    session.commit()
    return user


def revision(session: Session) -> str:
    return str(session.scalar(text("SELECT version_num FROM alembic_version")))


def add_deployment(
    session: Session,
    *,
    status: str,
    minutes_ago: int,
    revision_value: str | None = None,
    commit: str = "a" * 40,
    stages: list[dict] | None = None,
    rollback_of_id: str | None = None,
) -> Deployment:
    started = datetime.now(UTC) - timedelta(minutes=minutes_ago)
    finished = started + timedelta(minutes=4, seconds=32) if status != "deploying" else None
    detail: dict = {
        "api_image": f"registry.example/api@sha256:{'1' * 64}",
        "web_image": f"registry.example/web@sha256:{'2' * 64}",
        "git_commit": commit,
        "run_url": f"https://github.com/Shadow-Azure/ai-eval-platform/actions/runs/{minutes_ago}",
    }
    if stages is not None:
        detail["stages"] = stages
    row = Deployment(
        environment="production",
        git_commit=commit,
        image_digest=f"registry.example/api@sha256:{'0' * 64}",
        migration_revision=revision_value or revision(session),
        status=status,
        triggered_by="octocat",
        started_at=started,
        finished_at=finished,
        rollback_of_id=rollback_of_id,
        detail_json=detail,
    )
    session.add(row)
    session.commit()
    return row


def seed_successful_pair(session: Session) -> tuple[Deployment, Deployment]:
    current = add_deployment(
        session,
        status="succeeded",
        minutes_ago=10,
        commit="c" * 40,
        stages=[
            {
                "key": "build",
                "status": "success",
                "started_at": None,
                "finished_at": None,
                "detail": "GitHub Actions 构建成功后触发部署",
            },
            {
                "key": "database_migration",
                "status": "success",
                "started_at": "2026-11-11T01:00:00Z",
                "finished_at": "2026-11-11T01:00:18Z",
                "detail": "Alembic head",
            },
            {
                "key": "rolling_update",
                "status": "success",
                "started_at": "2026-11-11T01:00:18Z",
                "finished_at": "2026-11-11T01:01:00Z",
                "detail": "API/Web 容器替换完成",
            },
            {
                "key": "health_check",
                "status": "success",
                "started_at": "2026-11-11T01:01:00Z",
                "finished_at": "2026-11-11T01:01:10Z",
                "detail": "/readyz 通过",
            },
            {
                "key": "deployment_record",
                "status": "success",
                "started_at": "2026-11-11T01:01:10Z",
                "finished_at": "2026-11-11T01:01:11Z",
                "detail": "部署结果已写入记录",
            },
        ],
    )
    previous = add_deployment(
        session,
        status="succeeded",
        minutes_ago=60,
        commit="b" * 40,
    )
    return current, previous


def test_deployments_requires_login_and_returns_pipeline(
    client: TestClient, session: Session
) -> None:
    current, previous = seed_successful_pair(session)
    add_deployment(session, status="failed", minutes_ago=5, commit="d" * 40)

    response = client.get("/api/v1/deployments")
    assert response.status_code == 200
    payload = response.json()

    assert payload["environment"] == "production"
    assert payload["current"]["id"] == current.id
    assert payload["previous"]["id"] == previous.id
    assert payload["latest"]["status"] == "failed"
    assert [item["short_commit"] for item in payload["items"]] == ["ddddddd", "ccccccc", "bbbbbbb"]
    assert payload["database"]["revision"] == revision(session)
    assert payload["database"]["in_sync"] is True
    assert payload["github"]["actions_url"].endswith("/actions/workflows/deploy.yml")
    assert payload["current"]["commit_url"].endswith("/commit/" + "c" * 40)
    assert payload["current"]["run_url"].endswith("/actions/runs/10")

    stages = {stage["key"]: stage for stage in payload["current"]["stages"]}
    assert list(stages) == [
        "build",
        "database_migration",
        "rolling_update",
        "health_check",
        "deployment_record",
    ]
    assert stages["database_migration"]["duration_seconds"] == 18
    assert stages["health_check"]["status"] == "success"

    failed_stages = {stage["key"]: stage for stage in payload["latest"]["stages"]}
    assert failed_stages["build"]["status"] == "success"
    assert failed_stages["health_check"]["status"] == "unknown"

    components = {item["key"]: item for item in payload["health"]["components"]}
    assert {"api", "database", "redis", "object_storage", "worker"} <= set(components)
    assert components["database"]["status"] == "ok"
    assert payload["health"]["healthy"] >= 2

    # No GitHub credential and no previous-version confirmation yet.
    assert payload["rollback"]["target"]["id"] == previous.id
    assert payload["rollback"]["available"] is False
    assert payload["rollback"]["reason"] == "github_not_configured"

    client.cookies.clear()
    anonymous = client.get("/api/v1/deployments")
    assert anonymous.status_code == 401


def test_rollback_is_admin_only_and_requires_confirmation(
    client: TestClient, session: Session, configure_settings, monkeypatch
) -> None:
    _, previous = seed_successful_pair(session)
    configure_settings(GITHUB_TOKEN="test-token")

    add_viewer(session)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
    )
    assert login.status_code == 200
    forbidden = client.post(
        f"/api/v1/deployments/{previous.id}/rollback", json={"confirm": True}
    )
    assert forbidden.status_code == 403
    assert forbidden.json()["detail"]["error"]["code"] == "forbidden"

    login = client.post(
        "/api/v1/auth/login",
        json={"email": "admin@test.local", "password": "test-admin-password"},
    )
    assert login.status_code == 200

    not_confirmed = client.post(
        f"/api/v1/deployments/{previous.id}/rollback", json={"confirm": False}
    )
    assert not_confirmed.status_code == 422
    assert not_confirmed.json()["detail"]["error"]["code"] == "confirmation_required"

    calls: list[dict] = []

    def fake_dispatch(**kwargs):
        calls.append(kwargs)
        return {
            "ok": True,
            "status": 204,
            "dispatched_at": "2026-11-11T01:02:03+00:00",
            "event_type": "deployment-rollback",
        }

    monkeypatch.setattr(deployment_service, "dispatch_rollback", fake_dispatch)
    accepted = client.post(
        f"/api/v1/deployments/{previous.id}/rollback",
        json={"confirm": True, "reason": "health degraded"},
    )
    assert accepted.status_code == 202, accepted.text
    body = accepted.json()
    assert body["requested"] is True
    assert body["target"]["id"] == previous.id
    assert body["dispatched_at"] == "2026-11-11T01:02:03+00:00"
    assert calls and calls[0]["target"].id == previous.id
    assert calls[0]["requested_by"] == "admin@test.local"

    audit = session.scalar(
        select(AuditLog).where(AuditLog.action == "deployment.rollback_requested")
    )
    assert audit is not None
    assert audit.target_id == previous.id
    assert audit.metadata_json["current_deployment_id"]
    assert audit.metadata_json["reason"] == "health degraded"


def test_rollback_rejects_stale_target_and_incompatible_database(
    client: TestClient, session: Session, configure_settings, monkeypatch
) -> None:
    current, previous = seed_successful_pair(session)
    configure_settings(GITHUB_TOKEN="test-token")
    monkeypatch.setattr(
        deployment_service,
        "dispatch_rollback",
        lambda **_: {"dispatched_at": "now", "event_type": "deployment-rollback"},
    )

    stale = client.post(
        f"/api/v1/deployments/{current.id}/rollback", json={"confirm": True}
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["error"]["code"] == "rollback_target_changed"

    previous.migration_revision = "0001_old_revision"
    session.commit()
    incompatible = client.post(
        f"/api/v1/deployments/{previous.id}/rollback", json={"confirm": True}
    )
    assert incompatible.status_code == 409
    assert incompatible.json()["detail"]["error"]["code"] == "rollback_unavailable"
    assert incompatible.json()["detail"]["error"]["details"]["reason"] == "database_revision_mismatch"

    unknown = client.post(
        "/api/v1/deployments/does-not-exist/rollback", json={"confirm": True}
    )
    assert unknown.status_code == 404


def test_rollback_dispatch_failure_is_reported(
    client: TestClient, session: Session, configure_settings, monkeypatch
) -> None:
    _, previous = seed_successful_pair(session)
    configure_settings(GITHUB_TOKEN="test-token")

    def fail(**_: object) -> None:
        raise GitHubDispatchError("GitHub 派发失败", status=500)

    monkeypatch.setattr(deployment_service, "dispatch_rollback", fail)
    response = client.post(
        f"/api/v1/deployments/{previous.id}/rollback", json={"confirm": True}
    )
    assert response.status_code == 502
    assert response.json()["detail"]["error"]["code"] == "github_dispatch_failed"


def test_record_deployment_parses_stage_lines() -> None:
    path = REPO_ROOT / "backend" / "migrations" / "record_deployment.py"
    spec = importlib.util.spec_from_file_location("record_deployment", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)

    stages = module.parse_stages(
        "build|success|||GitHub Actions 构建成功\n"
        "database_migration|success|2026-11-11T01:00:00Z|2026-11-11T01:00:18Z|Alembic head\n"
        "broken line\n"
    )
    assert [stage["key"] for stage in stages] == ["build", "database_migration"]
    assert stages[1]["detail"] == "Alembic head"
    assert stages[0]["started_at"] is None
    assert stages[1]["finished_at"] == "2026-11-11T01:00:18Z"
