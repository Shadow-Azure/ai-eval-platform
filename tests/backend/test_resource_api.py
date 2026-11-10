"""M4-03 B: resource connection/pool API and capacity read models.

Admin writes, member reads. The API is the only place that turns
``ResourceValidationError`` into the stable ``422`` envelope with a ``fields``
map, so an incompatible connection/mode pair is rejected before a pool or an
instance exists. Credential material (ciphertext / key version) must never
appear in a response.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models import (
    AuditLog,
    Connection,
    Credential,
    ProvisionedInstance,
    ResourceEvent,
    ResourcePool,
    Runner,
    Tenant,
    User,
    Workspace,
)
from app.seed import ensure_default_scope
from app.services import identity
from app.services.provisioning import register_provisioner, reset_provisioners
from app.services.provisioning.fake import FakeProvisioner, reset_fake_instance_counter
from conftest import TEST_VIEWER_EMAIL, TEST_VIEWER_PASSWORD
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

SECRET_CIPHERTEXT = "ciphertext-must-never-leak"
SECRET_KEY_VERSION = "local:test-key"


@pytest.fixture(autouse=True)
def _reset_provisioning() -> object:
    reset_provisioners()
    reset_fake_instance_counter()
    yield
    reset_provisioners()
    reset_fake_instance_counter()


@pytest.fixture
def scope(session: Session) -> dict[str, str]:
    tenant, _workspace, project = ensure_default_scope(session)
    return {"tenant_id": tenant.id, "project_id": project.id}


def _credential(
    session: Session,
    scope: dict[str, str],
    *,
    provider: str = "fake",
    kind: str = "resource",
    revoked: bool = False,
) -> Credential:
    credential = Credential(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        kind=kind,
        provider=provider,
        ciphertext=SECRET_CIPHERTEXT,
        key_version=SECRET_KEY_VERSION,
        secret_mask="ak-***mask",
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    session.add(credential)
    session.commit()
    return credential


def _register_fake(**kwargs: object) -> FakeProvisioner:
    fake = FakeProvisioner(**kwargs)
    register_provisioner(fake)
    return fake


def _error(response) -> dict:
    return response.json()["detail"]["error"]


def _connection_body(credential_id: str, **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": f"conn-{uuid4().hex[:8]}",
        "provider": "fake",
        "region": "cn-hangzhou",
        "credential_id": credential_id,
        "capabilities": ["ecs:spot", "ecs:inventory"],
    }
    body.update(overrides)
    return body


def _create_connection(client: TestClient, credential_id: str, **overrides: object) -> dict:
    response = client.post("/api/v1/connections", json=_connection_body(credential_id, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _pool_body(connection_id: str | None, **overrides: object) -> dict:
    body: dict[str, object] = {
        "name": f"pool-{uuid4().hex[:8]}",
        "connection_id": connection_id,
        "provisioning_mode": "spot",
        "min_capacity": 0,
        "max_capacity": 3,
        "price_threshold_usd": "0.5000",
        "instance_config": {
            "instance_type": "ecs.g7.large",
            "runners_per_instance": 1,
        },
        "runner_group": "spot-linux",
        "labels": {"kind": "spot"},
    }
    body.update(overrides)
    return body


def _create_pool(client: TestClient, connection_id: str | None, **overrides: object) -> dict:
    response = client.post("/api/v1/resource-pools", json=_pool_body(connection_id, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


def _add_runner(
    session: Session,
    scope: dict[str, str],
    pool_id: str,
    *,
    name: str | None = None,
    status: str = "online",
    busy_case_attempt_id: str | None = None,
) -> Runner:
    runner = Runner(
        tenant_id=scope["tenant_id"],
        resource_pool_id=pool_id,
        provider_runner_id=name or f"runner-{uuid4().hex[:8]}",
        name=name or "spot-runner",
        labels_json=["self-hosted", "spot"],
        status=status,
        busy_case_attempt_id=busy_case_attempt_id,
        last_heartbeat_at=datetime.now(UTC) - timedelta(seconds=5),
        registered_at=datetime.now(UTC) - timedelta(minutes=1),
    )
    session.add(runner)
    session.commit()
    return runner


def _add_viewer(session: Session) -> User:
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


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


def test_admin_creates_connection_and_reads_masked_payload(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    credential = _credential(session, scope)

    created = _create_connection(client, credential.id, name="aliyun-prod")

    assert created["name"] == "aliyun-prod"
    assert created["provider"] == "fake"
    assert created["region"] == "cn-hangzhou"
    assert created["credential_mask"] == "ak-***mask"
    assert created["capabilities"] == ["ecs:inventory", "ecs:spot"]
    assert created["status"] == "unverified"

    listing = client.get("/api/v1/connections")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [item["id"] for item in items] == [created["id"]]
    assert SECRET_CIPHERTEXT not in listing.text
    assert SECRET_KEY_VERSION not in listing.text
    assert "ciphertext" not in listing.text


def test_connection_credential_must_be_live_resource_credential(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    model_credential = _credential(session, scope, kind="model")
    response = client.post("/api/v1/connections", json=_connection_body(model_credential.id))
    assert response.status_code == 422
    error = _error(response)
    assert error["code"] == "connection_credential_invalid"
    assert error["fields"]["credential_id"]
    assert error["details"]["reason"] == "kind"

    revoked = _credential(session, scope, revoked=True)
    response = client.post("/api/v1/connections", json=_connection_body(revoked.id))
    assert response.status_code == 422
    assert _error(response)["code"] == "connection_credential_invalid"
    assert _error(response)["details"]["reason"] == "revoked"

    unknown = client.post("/api/v1/connections", json=_connection_body(str(uuid4())))
    assert unknown.status_code == 422
    assert _error(unknown)["details"]["reason"] == "missing"

    assert session.scalar(select(func.count()).select_from(Connection)) == 0


def test_connection_test_marks_it_verified(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake()
    connection = _create_connection(client, _credential(session, scope).id)

    response = client.post(f"/api/v1/connections/{connection['id']}/test")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["status"] == "verified"
    assert payload["last_verified_at"] is not None
    stored = session.get(Connection, connection["id"])
    session.refresh(stored)
    assert stored.status == "verified"


def test_connection_test_without_provisioner_is_rejected(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    connection = _create_connection(client, _credential(session, scope).id)

    response = client.post(f"/api/v1/connections/{connection['id']}/test")

    assert response.status_code == 422
    assert _error(response)["code"] == "provisioner_unavailable"


# ---------------------------------------------------------------------------
# Pool creation and validation
# ---------------------------------------------------------------------------


def test_admin_creates_spot_pool_with_capacity_policy(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake()
    connection = _create_connection(client, _credential(session, scope).id)

    pool = _create_pool(client, connection["id"])

    assert pool["provider_key"] == "fake"
    assert pool["provisioning_mode"] == "spot"
    assert pool["connection_id"] == connection["id"]
    assert pool["min_capacity"] == 0
    assert pool["max_capacity"] == 3
    assert pool["price_threshold_usd"] == "0.5000"
    assert pool["instance_config"]["instance_type"] == "ecs.g7.large"
    assert pool["runner_group"] == "spot-linux"
    assert pool["labels"] == {"kind": "spot"}
    assert pool["status"] == "active"
    assert pool["capacity_reason"] is None
    assert SECRET_CIPHERTEXT not in str(pool)


def test_incompatible_mode_is_rejected_before_pool_creation(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake()
    inventory_only = _create_connection(
        client,
        _credential(session, scope).id,
        capabilities=["ecs:inventory"],
    )

    mismatch = client.post(
        "/api/v1/resource-pools",
        json=_pool_body(inventory_only["id"], name="incompatible"),
    )
    assert mismatch.status_code == 422
    error = _error(mismatch)
    assert error["code"] == "connection_provider_mismatch"
    assert "connection_id" in error["fields"]
    assert error["details"]["missing"] == ["ecs:spot"]

    missing = client.post(
        "/api/v1/resource-pools",
        json=_pool_body(None, name="no-connection"),
    )
    assert missing.status_code == 422
    assert _error(missing)["code"] == "connection_missing"
    assert "connection_id" in _error(missing)["fields"]

    assert session.scalar(select(func.count()).select_from(ResourcePool)) == 0


def test_pool_creation_rejects_invalid_capacity_and_price(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    pool = client.post(
        "/api/v1/resource-pools",
        json=_pool_body(None, name="fixed-invalid", provisioning_mode="fixed", min_capacity=3, max_capacity=2),
    )
    assert pool.status_code == 422
    assert _error(pool)["code"] == "pool_capacity_invalid"
    assert "min_capacity" in _error(pool)["fields"]

    price = client.post(
        "/api/v1/resource-pools",
        json=_pool_body(None, name="fixed-price", provisioning_mode="fixed", price_threshold_usd="0"),
    )
    assert price.status_code == 422
    assert _error(price)["code"] == "price_threshold_invalid"

    assert session.scalar(select(func.count()).select_from(ResourcePool)) == 0


def test_fixed_pool_needs_no_connection(
    client: TestClient, session: Session
) -> None:
    pool = _create_pool(
        client,
        None,
        name="fixed-linux",
        provisioning_mode="fixed",
        min_capacity=0,
        max_capacity=1,
        price_threshold_usd=None,
        instance_config={},
        labels={"kind": "fixed"},
    )
    assert pool["provider_key"] == "fixed"
    assert pool["connection_id"] is None


# ---------------------------------------------------------------------------
# Reads: pools, state, events, runners
# ---------------------------------------------------------------------------


def test_pool_list_and_detail_expose_capacity_state(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake(price_usd=Decimal("0.1000"))
    connection = _create_connection(client, _credential(session, scope).id)
    pool = _create_pool(client, connection["id"])

    listing = client.get("/api/v1/resource-pools")
    assert listing.status_code == 200
    items = listing.json()["items"]
    assert [item["id"] for item in items] == [pool["id"]]
    state = items[0]["capacity_state"]
    assert state["capacity_gap"] == 0
    assert state["queued_attempts"] == 0
    assert state["desired_capacity"] == 0
    assert state["checked_at"]

    detail = client.get(f"/api/v1/resource-pools/{pool['id']}")
    assert detail.status_code == 200, detail.text
    payload = detail.json()
    assert payload["id"] == pool["id"]
    assert payload["capacity_state"]["checked_at"]
    assert payload["events"] == []
    assert payload["runners"] == {
        "total": 0,
        "online": 0,
        "busy": 0,
        "draining": 0,
        "offline": 0,
    }


def test_pool_detail_lists_recent_events_and_runners(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake(price_usd=Decimal("0.1000"))
    connection = _create_connection(client, _credential(session, scope).id)
    pool = _create_pool(client, connection["id"])
    runner = _add_runner(session, scope, pool["id"], name="spot-runner-1", status="busy")
    session.add(
        ResourceEvent(
            tenant_id=scope["tenant_id"],
            resource_pool_id=pool["id"],
            event_type="resource.capacity.updated",
            payload_json={"reason": "capacity_scale_up"},
        )
    )
    session.commit()

    detail = client.get(f"/api/v1/resource-pools/{pool['id']}").json()

    assert [event["event_type"] for event in detail["events"]] == [
        "resource.capacity.updated"
    ]
    assert detail["events"][0]["payload"] == {"reason": "capacity_scale_up"}
    assert detail["runners"] == {
        "total": 1,
        "online": 0,
        "busy": 1,
        "draining": 0,
        "offline": 0,
    }

    runners = client.get(f"/api/v1/resource-pools/{pool['id']}/runners")
    assert runners.status_code == 200
    items = runners.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == runner.id
    assert items[0]["name"] == "spot-runner-1"
    assert items[0]["status"] == "busy"
    assert items[0]["labels"] == ["self-hosted", "spot"]
    assert items[0]["last_heartbeat_at"]
    assert items[0]["busy_case_attempt_id"] is None


def test_unknown_pool_is_404(client: TestClient) -> None:
    response = client.get(f"/api/v1/resource-pools/{uuid4()}")
    assert response.status_code == 404
    assert _error(response)["code"] == "resource_pool_not_found"


# ---------------------------------------------------------------------------
# PATCH
# ---------------------------------------------------------------------------


def test_pool_patch_updates_capacity_and_revalidates(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake()
    connection = _create_connection(client, _credential(session, scope).id)
    pool = _create_pool(client, connection["id"])

    invalid = client.patch(
        f"/api/v1/resource-pools/{pool['id']}",
        json={"min_capacity": 5, "max_capacity": 2},
    )
    assert invalid.status_code == 422
    assert _error(invalid)["code"] == "pool_capacity_invalid"

    updated = client.patch(
        f"/api/v1/resource-pools/{pool['id']}",
        json={"max_capacity": 6, "price_threshold_usd": "0.2500"},
    )
    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["max_capacity"] == 6
    assert payload["price_threshold_usd"] == "0.2500"
    assert payload["min_capacity"] == 0
    assert payload["connection_id"] == connection["id"]


# ---------------------------------------------------------------------------
# Test provisioning
# ---------------------------------------------------------------------------


def test_test_provisioning_requires_cost_confirmation(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake()
    connection = _create_connection(client, _credential(session, scope).id)
    pool = _create_pool(client, connection["id"])

    response = client.post(
        f"/api/v1/resource-pools/{pool['id']}/test-provisioning",
        json={"confirm_cost": False},
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "cost_confirmation_required"
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0


def test_test_provisioning_creates_one_instance_and_audit_event(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _register_fake(price_usd=Decimal("0.1000"))
    connection = _create_connection(client, _credential(session, scope).id)
    pool = _create_pool(client, connection["id"], price_threshold_usd="0.5000")

    response = client.post(
        f"/api/v1/resource-pools/{pool['id']}/test-provisioning",
        json={"confirm_cost": True},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["instance"]["status"] == "requested"
    assert payload["instance"]["resource_pool_id"] == pool["id"]
    assert payload["price_usd"] == "0.1000"
    assert payload["price_threshold_usd"] == "0.5000"
    assert SECRET_CIPHERTEXT not in response.text
    assert SECRET_KEY_VERSION not in response.text

    instances = list(session.scalars(select(ProvisionedInstance)))
    assert len(instances) == 1
    assert instances[0].id == payload["instance"]["id"]

    events = list(
        session.scalars(
            select(ResourceEvent).where(ResourceEvent.event_type == "resource.provisioning_tested")
        )
    )
    assert len(events) == 1
    assert events[0].payload_json["instance_id"] == instances[0].id
    assert events[0].payload_json["price_usd"] == "0.1000"
    assert events[0].payload_json["threshold"] == "0.5000"

    audits = list(
        session.scalars(
            select(AuditLog).where(AuditLog.action == "resource.provisioning_tested")
        )
    )
    assert len(audits) == 1
    assert audits[0].target_type == "resource_pool"
    assert audits[0].target_id == pool["id"]


def test_test_provisioning_rejects_fixed_pool(
    client: TestClient, session: Session
) -> None:
    _register_fake()
    pool = _create_pool(
        client,
        None,
        name="fixed-test",
        provisioning_mode="fixed",
        min_capacity=0,
        max_capacity=1,
        price_threshold_usd=None,
        instance_config={},
    )

    response = client.post(
        f"/api/v1/resource-pools/{pool['id']}/test-provisioning",
        json={"confirm_cost": True},
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "pool_provisioning_mode_invalid"
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0


def test_test_provisioning_without_provisioner_is_rejected(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    connection = _create_connection(client, _credential(session, scope).id)
    pool = _create_pool(client, connection["id"])

    response = client.post(
        f"/api/v1/resource-pools/{pool['id']}/test-provisioning",
        json={"confirm_cost": True},
    )

    assert response.status_code == 422
    assert _error(response)["code"] == "provisioner_unavailable"


# ---------------------------------------------------------------------------
# Access control
# ---------------------------------------------------------------------------


def test_member_reads_but_cannot_write(
    client: TestClient, session: Session, scope: dict[str, str]
) -> None:
    _add_viewer(session)
    connection = _create_connection(client, _credential(session, scope).id)
    _create_pool(client, connection["id"])

    login = client.post(
        "/api/v1/auth/login",
        json={"email": TEST_VIEWER_EMAIL, "password": TEST_VIEWER_PASSWORD},
    )
    assert login.status_code == 200

    assert client.get("/api/v1/connections").status_code == 200
    assert client.get("/api/v1/resource-pools").status_code == 200

    denied = client.post(
        "/api/v1/connections",
        json=_connection_body(_credential(session, scope).id),
    )
    assert denied.status_code == 403
    assert _error(denied)["code"] == "forbidden"

    denied_pool = client.post(
        "/api/v1/resource-pools",
        json=_pool_body(connection["id"], name="member-pool"),
    )
    assert denied_pool.status_code == 403

    client.cookies.clear()
    assert client.get("/api/v1/connections").status_code == 401
