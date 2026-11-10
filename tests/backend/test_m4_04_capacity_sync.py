"""M4-04 integration points between the capacity controller and providers."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.config import get_settings
from app.models import Connection, Credential
from app.seed import ensure_default_scope
from app.services import capacity_control
from app.services.provisioning import ProvisionerError, register_provisioner, reset_provisioners
from app.services.provisioning.fake import FakeProvisioner
from app.services.resources import verify_connection
from sqlalchemy.orm import Session


def _scope(session: Session) -> tuple[str, str]:
    tenant, _workspace, project = ensure_default_scope(session)
    return tenant.id, project.id


def _connection(session: Session) -> Connection:
    tenant_id, project_id = _scope(session)
    credential = Credential(
        tenant_id=tenant_id,
        project_id=project_id,
        kind="resource",
        provider="fake",
        ciphertext="unused",
        key_version="local:1",
        secret_mask="****",
    )
    session.add(credential)
    session.flush()
    connection = Connection(
        tenant_id=tenant_id,
        project_id=project_id,
        name="sync-connection",
        provider="fake",
        region="cn-hangzhou",
        credential_id=credential.id,
        capabilities_json={"capabilities": ["ecs:spot", "ecs:inventory"]},
        status="verified",
    )
    session.add(connection)
    session.flush()
    return connection


def _pool(session: Session, connection: Connection):
    from app.models import ResourcePool

    tenant_id, project_id = _scope(session)
    pool = ResourcePool(
        tenant_id=tenant_id,
        project_id=project_id,
        connection_id=connection.id,
        name="sync-pool",
        provider_key="fake",
        provisioning_mode="spot",
        min_capacity=0,
        max_capacity=2,
        instance_config_json={"instance_type": "ecs.g7.large"},
        price_threshold_usd=Decimal("0.5000"),
        labels_json={"runs_on": ["self-hosted", "linux", "eval"]},
        status="active",
    )
    session.add(pool)
    session.flush()
    return pool


def test_reconcile_surfaces_provider_sync_counts(session: Session) -> None:
    reset_provisioners()
    try:
        connection = _connection(session)
        pool = _pool(session, connection)
        fake = FakeProvisioner(sync_counts={"checked": 2, "online": 1, "interrupted": 0})
        state = capacity_control.reconcile_pool(
            session,
            pool,
            settings=get_settings(),
            now=datetime(2026, 11, 10, 2, 0, tzinfo=UTC),
            provisioner=fake,
        )
        assert state["provider_sync"] == {"checked": 2, "online": 1, "interrupted": 0}
        assert state["scaled_up"] == 0
    finally:
        reset_provisioners()


class _FailingVerify(FakeProvisioner):
    provider = "fake"

    def verify(self, *, session, connection):  # type: ignore[override]
        raise ProvisionerError("aliyun_auth_failed", "bad key")


def test_connection_verification_fails_on_provider_error(session: Session) -> None:
    connection = _connection(session)
    register_provisioner(_FailingVerify())
    try:
        payload = verify_connection(
            session, connection, provisioner=_FailingVerify()
        )
    finally:
        reset_provisioners()
    assert payload["status"] == "failed"
    assert payload["last_verified_at"] is not None
