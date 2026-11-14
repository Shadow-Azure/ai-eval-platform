"""M3-06 administrator operations status page API."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import (
    Credential,
    Dataset,
    ModelConfig,
    Project,
    ResourcePool,
    Runner,
    ServiceHeartbeat,
    Tenant,
    User,
    Workspace,
)
from app.services import identity
from app.services.object_store import ObjectStoreError
from app.services.operations import record_worker_heartbeat
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def add_viewer(session: Session) -> User:
    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    workspace = session.scalar(
        select(Workspace).where(Workspace.tenant_id == tenant.id)
    )
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


def service_map(payload: dict) -> dict[str, dict]:
    return {service["key"]: service for service in payload["services"]}


def test_operations_status_is_admin_only(
    client: TestClient, session: Session
) -> None:
    admin = client.get("/api/v1/operations/status")
    assert admin.status_code == 200

    add_viewer(session)
    login = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
    )
    assert login.status_code == 200
    viewer = client.get("/api/v1/operations/status")
    assert viewer.status_code == 403
    assert viewer.json()["detail"]["error"]["code"] == "forbidden"

    client.cookies.clear()
    anonymous = client.get("/api/v1/operations/status")
    assert anonymous.status_code == 401
    assert anonymous.json()["detail"]["error"]["code"] == "unauthenticated"


def test_operations_status_reports_services_resources_and_database(
    client: TestClient,
) -> None:
    response = client.get("/api/v1/operations/status")
    assert response.status_code == 200
    payload = response.json()

    assert payload["status"] == "ok"
    assert payload["environment"] == "development"
    assert payload["generated_at"]
    services = service_map(payload)
    assert list(services) == [
        "api",
        "database",
        "redis",
        "object_storage",
        "runner",
        "worker",
        "model_gateway",
    ]
    assert services["api"]["status"] == "ok"
    assert services["database"]["status"] == "ok"
    assert services["redis"]["status"] == "disabled"
    assert services["object_storage"]["status"] == "disabled"
    assert services["runner"]["status"] == "unknown"
    assert services["worker"]["status"] == "unknown"
    assert services["model_gateway"]["status"] == "unknown"

    database = payload["database"]
    assert database["status"] == "ok"
    assert database["dialect"] in {"sqlite", "postgresql"}
    assert database["revision"] == "0026_eval_run_max_turns"
    assert database["size_bytes"] > 0
    capacity = database["capacity"]
    assert set(capacity) == {
        "max_connections",
        "connections",
        "connection_usage_percent",
        "storage_limit_bytes",
        "storage_usage_percent",
    }
    assert capacity["storage_limit_bytes"] is None
    assert capacity["storage_usage_percent"] is None
    if database["dialect"] == "postgresql":
        assert capacity["max_connections"] > 0
        assert capacity["connections"] >= 1
        assert capacity["connection_usage_percent"] > 0
    else:
        assert capacity["max_connections"] is None
        assert capacity["connections"] is None
        assert capacity["connection_usage_percent"] is None
    tables = {table["name"]: table for table in database["tables"]}
    assert {"eval_run", "dataset", "runner", "outbox", "service_heartbeat", "deployment"} <= set(tables)
    assert tables["user"]["rows"] >= 1
    assert tables["deployment"]["rows"] == 0

    resources = payload["resources"]
    assert resources["runners"] == []
    assert resources["pools"] == []
    assert resources["evaluations"] == {"total": 0, "running": 0, "queued": 0, "failed": 0}
    assert resources["datasets"] == {"total": 0, "versions": 0, "cases": 0}
    assert resources["model_configs"] == {"total": 0, "credentials": 0, "usable": 0}
    assert resources["outbox"] == {"pending": 0, "processing": 0, "failed": 0}
    redis_detail = payload["redis"]
    assert redis_detail["configured"] is False
    assert redis_detail["status"] == "disabled"
    assert redis_detail["summary"]
    storage_detail = payload["object_storage"]
    assert storage_detail["configured"] is False
    assert storage_detail["status"] == "disabled"
    assert storage_detail["bucket"] is None
    assert "password" not in response.text.lower()


def test_operations_reports_worker_heartbeat_freshness(
    client: TestClient, session: Session
) -> None:
    assert service_map(client.get("/api/v1/operations/status").json())["worker"][
        "status"
    ] == "unknown"

    record_worker_heartbeat(session, pending_outbox=2)
    fresh = service_map(client.get("/api/v1/operations/status").json())["worker"]
    assert fresh["status"] == "ok"
    assert "最近心跳" in fresh["summary"]

    beat = session.get(ServiceHeartbeat, "worker")
    beat.beat_at = datetime.now(UTC) - timedelta(minutes=10)
    session.commit()
    stale = service_map(client.get("/api/v1/operations/status").json())["worker"]
    assert stale["status"] == "error"
    assert "过期" in stale["summary"]


def test_operations_reports_runner_pool_and_outbox(
    client: TestClient, session: Session
) -> None:
    heartbeat = client.post(
        "/runner/v1/runners/heartbeat",
        json={
            "runner_id": "fixed-linux-ops",
            "name": "fixed-linux-ops",
            "labels": ["self-hosted", "linux"],
            "status": "online",
            "current_case_attempt_id": None,
        },
    )
    assert heartbeat.status_code == 200

    tenant = session.scalar(select(Tenant).where(Tenant.slug == "default"))
    project = session.scalar(select(Project))
    session.add(
        Dataset(
            tenant_id=tenant.id,
            project_id=project.id,
            slug="ops-dataset",
            name="运营演示数据集",
            adapter_key="swebench",
            metadata_json={},
        )
    )
    config = ModelConfig(
        tenant_id=tenant.id,
        project_id=project.id,
        provider="openai",
        display_name="ops-model",
        base_url="https://api.example.test/v1",
        model_name="ops-model",
        api_style="openai",
        limits_json={},
        pricing_json={},
    )
    session.add(config)
    session.commit()

    unbound = client.get("/api/v1/operations/status").json()
    assert service_map(unbound)["model_gateway"]["status"] == "degraded"
    assert unbound["resources"]["model_configs"]["usable"] == 0

    credential = Credential(
        tenant_id=tenant.id,
        project_id=project.id,
        kind="model",
        provider="openai",
        ciphertext="dummy-ciphertext",
        key_version="test",
        secret_mask="sk-***",
    )
    session.add(credential)
    session.flush()
    config.credential_id = credential.id
    session.commit()

    payload = client.get("/api/v1/operations/status").json()
    services = service_map(payload)
    assert services["runner"]["status"] == "ok"
    assert services["runner"]["summary"] == "1 / 1 在线"
    assert services["model_gateway"]["status"] == "ok"
    resources = payload["resources"]
    assert resources["datasets"]["total"] == 1
    assert resources["model_configs"]["total"] == 1
    assert resources["model_configs"]["usable"] == 1
    assert len(resources["runners"]) == 1
    assert resources["runners"][0]["name"] == "fixed-linux-ops"
    assert resources["runners"][0]["pool"] == "fixed-linux"
    assert resources["pools"][0]["runners_online"] == 1
    assert resources["pools"][0]["runners_total"] == 1
    assert session.scalar(select(func.count()).select_from(Runner)) == 1
    assert session.scalar(select(func.count()).select_from(ResourcePool)) == 1


def test_operations_object_storage_probe_states(
    client: TestClient, monkeypatch
) -> None:
    class HealthyStore:
        bucket = "ops-bucket"
        endpoint = "https://oss.example.test"

        def head_bucket(self, *, timeout: float) -> None:
            assert timeout > 0

    monkeypatch.setattr(
        "app.api.operations.object_store_from_settings",
        lambda _settings: HealthyStore(),
    )
    payload = client.get("/api/v1/operations/status").json()
    healthy = service_map(payload)["object_storage"]
    assert healthy["status"] == "ok"
    assert "ops-bucket" in healthy["summary"]
    detail = payload["object_storage"]
    assert detail["status"] == "ok"
    assert detail["bucket"] == "ops-bucket"
    assert detail["endpoint_host"] == "oss.example.test"

    class FailingStore(HealthyStore):
        def head_bucket(self, *, timeout: float) -> None:
            raise ObjectStoreError("HEAD bucket 返回 HTTP 403")

    monkeypatch.setattr(
        "app.api.operations.object_store_from_settings",
        lambda _settings: FailingStore(),
    )
    failing_payload = client.get("/api/v1/operations/status").json()
    failing = service_map(failing_payload)["object_storage"]
    assert failing["status"] == "error"
    assert "HTTP 403" in failing["summary"]
    assert failing_payload["object_storage"]["status"] == "error"


def test_operations_reports_redis_capacity(
    client: TestClient, configure_settings, monkeypatch
) -> None:
    import redis

    class FakeRedis:
        def ping(self) -> bool:
            return True

        def info(self) -> dict[str, object]:
            return {
                "redis_version": "7.2.4",
                "used_memory": 1048576,
                "maxmemory": 20971520,
                "maxmemory_policy": "noeviction",
                "connected_clients": 3,
                "maxclients": 10000,
            }

    monkeypatch.setattr(
        redis.Redis,
        "from_url",
        classmethod(lambda cls, *args, **kwargs: FakeRedis()),
    )
    configure_settings(REDIS_URL="redis://redis.example.test:6379/0")

    payload = client.get("/api/v1/operations/status").json()
    assert service_map(payload)["redis"]["status"] == "ok"
    detail = payload["redis"]
    assert detail["configured"] is True
    assert detail["status"] == "ok"
    assert detail["version"] == "7.2.4"
    assert detail["used_memory_bytes"] == 1048576
    assert detail["maxmemory_bytes"] == 20971520
    assert detail["maxmemory_policy"] == "noeviction"
    assert detail["connected_clients"] == 3
    assert detail["maxclients"] == 10000
    assert "1.0 MB" in detail["summary"]


def test_operations_reports_configured_database_storage_limit(
    client: TestClient, configure_settings
) -> None:
    configure_settings(DATABASE_STORAGE_LIMIT_BYTES="1048576")

    response = client.get("/api/v1/operations/status")
    assert response.status_code == 200
    database = response.json()["database"]
    capacity = database["capacity"]
    assert capacity["storage_limit_bytes"] == 1048576
    assert capacity["storage_usage_percent"] is not None
    assert capacity["storage_usage_percent"] > 0
