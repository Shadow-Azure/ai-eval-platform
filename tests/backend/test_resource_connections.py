"""M4-03 A: resource connections, pool configuration and the provisioner seam.

Covers the acceptance surface of issue #80 that does not need the API or the
capacity controller (part B):

- ``connection`` is a tenant-level credential reference with a capability
  list and verification state, and the migration exposes it;
- ``resource_pool`` stores connection, provisioning mode, capacity bounds,
  instance parameters and price policy, with DB-level guards;
- compatibility validation returns stable codes *before* a pool or instance
  is created;
- payloads never leak credential ciphertext or key versions;
- the fake provisioner covers quote/create/release/health incl. failures,
  deterministically and without new dependencies.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import pytest
from app.models import Connection, Credential, ProvisionedInstance, ResourcePool
from app.seed import ensure_default_scope
from app.services.provisioning import (
    CapabilityError,
    InstanceSpec,
    PriceQuote,
    Provisioner,
    ProvisionerError,
    get_provisioner,
    register_provisioner,
    reset_provisioners,
)
from app.services.provisioning.fake import FakeProvisioner, reset_fake_instance_counter
from app.services.resources import (
    ResourceValidationError,
    connection_payload,
    pool_payload,
    validate_connection_credential,
    validate_pool_configuration,
    verify_connection,
)
from sqlalchemy import func, inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


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
    provider: str = "aliyun",
    kind: str = "resource",
    revoked: bool = False,
    secret_mask: str = "ak-***mask",
) -> Credential:
    credential = Credential(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        kind=kind,
        provider=provider,
        ciphertext="ciphertext-must-never-leak",
        key_version="local:test-key",
        secret_mask=secret_mask,
        revoked_at=datetime.now(UTC) if revoked else None,
    )
    session.add(credential)
    session.flush()
    return credential


def _connection(
    session: Session,
    scope: dict[str, str],
    *,
    provider: str = "aliyun",
    capabilities: tuple[str, ...] = ("ecs:spot", "ecs:inventory"),
    status: str = "unverified",
    region: str | None = "cn-hangzhou",
    credential: Credential | None = None,
    name: str | None = None,
) -> Connection:
    credential = credential or _credential(session, scope, provider=provider)
    connection = Connection(
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
        name=name or f"conn-{uuid4().hex[:8]}",
        provider=provider,
        region=region,
        credential_id=credential.id,
        capabilities_json={"capabilities": list(capabilities)},
        status=status,
    )
    session.add(connection)
    session.flush()
    return connection


def _pool(
    session: Session,
    scope: dict[str, str],
    **overrides: object,
) -> ResourcePool:
    values: dict[str, object] = {
        "tenant_id": scope["tenant_id"],
        "project_id": scope["project_id"],
        "name": f"pool-{uuid4().hex[:8]}",
        "provider_key": "aliyun",
        "labels_json": {"kind": "spot"},
        "status": "active",
    }
    values.update(overrides)
    pool = ResourcePool(**values)
    session.add(pool)
    session.flush()
    return pool


# ---------------------------------------------------------------------------
# Schema: migration + model round-trip and DB guards
# ---------------------------------------------------------------------------


def test_migration_exposes_connection_and_pool_capacity_columns(session: Session) -> None:
    inspector = inspect(session.get_bind())
    assert "connection" in inspector.get_table_names()

    connection_columns = {column["name"] for column in inspector.get_columns("connection")}
    assert {
        "id",
        "tenant_id",
        "project_id",
        "name",
        "provider",
        "region",
        "credential_id",
        "capabilities_json",
        "status",
        "last_verified_at",
    } <= connection_columns

    pool_columns = {column["name"] for column in inspector.get_columns("resource_pool")}
    assert {
        "connection_id",
        "provisioning_mode",
        "min_capacity",
        "max_capacity",
        "instance_config_json",
        "price_threshold_usd",
        "price_usd",
        "capacity_reason",
        "last_checked_at",
        "next_check_at",
    } <= pool_columns


def test_connection_and_pool_round_trip(session: Session, scope: dict[str, str]) -> None:
    connection = _connection(session, scope, status="verified")
    connection.last_verified_at = datetime.now(UTC)
    checked_at = datetime.now(UTC)
    pool = _pool(
        session,
        scope,
        connection_id=connection.id,
        provisioning_mode="spot",
        min_capacity=1,
        max_capacity=3,
        instance_config_json={"instance_type": "ecs.g7.large", "image_id": "img-1"},
        price_threshold_usd=Decimal("0.5000"),
        price_usd=Decimal("0.3100"),
        capacity_reason="waiting_for_price",
        last_checked_at=checked_at,
        next_check_at=checked_at,
    )
    pool_id = pool.id
    session.commit()
    session.expire_all()

    stored_pool = session.get(ResourcePool, pool_id)
    assert stored_pool is not None
    assert stored_pool.connection_id == connection.id
    assert stored_pool.provisioning_mode == "spot"
    assert stored_pool.min_capacity == 1
    assert stored_pool.max_capacity == 3
    assert stored_pool.instance_config_json["instance_type"] == "ecs.g7.large"
    assert stored_pool.price_threshold_usd == Decimal("0.5000")
    assert stored_pool.price_usd == Decimal("0.3100")
    assert stored_pool.capacity_reason == "waiting_for_price"
    assert stored_pool.last_checked_at is not None
    assert stored_pool.next_check_at is not None

    stored_connection = session.get(Connection, connection.id)
    assert stored_connection is not None
    assert stored_connection.capabilities_json == {
        "capabilities": ["ecs:spot", "ecs:inventory"]
    }
    assert stored_connection.status == "verified"


def test_new_pool_defaults_to_fixed_unbounded_capacity(
    session: Session, scope: dict[str, str]
) -> None:
    pool = _pool(session, scope, provider_key="fixed")
    session.commit()
    session.expire_all()
    stored = session.get(ResourcePool, pool.id)
    assert stored is not None
    assert stored.provisioning_mode == "fixed"
    assert stored.min_capacity == 0
    assert stored.max_capacity == 50
    assert stored.instance_config_json == {}
    assert stored.connection_id is None
    assert stored.price_threshold_usd is None


def test_pool_rejects_unknown_provisioning_mode(
    session: Session, scope: dict[str, str]
) -> None:
    with pytest.raises(IntegrityError):
        _pool(session, scope, provisioning_mode="reserved")
    session.rollback()


@pytest.mark.parametrize(
    ("min_capacity", "max_capacity"),
    [(0, 0), (2, 1), (-1, 1)],
)
def test_pool_rejects_invalid_capacity_bounds(
    session: Session, scope: dict[str, str], min_capacity: int, max_capacity: int
) -> None:
    with pytest.raises(IntegrityError):
        _pool(
            session,
            scope,
            min_capacity=min_capacity,
            max_capacity=max_capacity,
        )
    session.rollback()


def test_connection_rejects_unknown_status(session: Session, scope: dict[str, str]) -> None:
    with pytest.raises(IntegrityError):
        _connection(session, scope, status="pending")
    session.rollback()


def test_connection_name_is_unique_per_tenant(session: Session, scope: dict[str, str]) -> None:
    _connection(session, scope, name="shared-name")
    with pytest.raises(IntegrityError):
        _connection(session, scope, name="shared-name")
    session.rollback()


# ---------------------------------------------------------------------------
# Compatibility / capability validation (stable codes before creation)
# ---------------------------------------------------------------------------


def test_spot_requires_a_connection(scope: dict[str, str]) -> None:
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(None, "spot", 0, 2, None)
    assert caught.value.code == "connection_missing"
    assert caught.value.field == "connection_id"
    assert caught.value.as_dict()["code"] == "connection_missing"


def test_spot_requires_ecs_spot_capability(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope, capabilities=("ecs:inventory",))
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(connection, "spot", 1, 2, None)
    assert caught.value.code == "connection_provider_mismatch"
    assert caught.value.field == "connection_id"
    assert caught.value.details == {"missing": ["ecs:spot"]}


def test_spot_pool_provider_must_match_connection(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope, provider="aliyun")
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(
            connection, "spot", 1, 2, None, provider_key="github"
        )
    assert caught.value.code == "connection_provider_mismatch"
    assert caught.value.details == {"expected": "aliyun"}


def test_fixed_pool_does_not_need_a_connection() -> None:
    validate_pool_configuration(None, "fixed", 0, 1, None)


def test_fixed_pool_ignores_a_supplied_connection(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope, capabilities=())
    validate_pool_configuration(connection, "fixed", 0, 1, None, provider_key="fixed")


def test_connection_checks_win_over_capacity_checks() -> None:
    """Compatibility is answered before numeric bounds (design 04 §1)."""

    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(None, "spot", 3, 2, Decimal("-1"))
    assert caught.value.code == "connection_missing"


def test_capacity_bounds_are_validated(session: Session, scope: dict[str, str]) -> None:
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(None, "fixed", 3, 2, None)
    assert caught.value.code == "pool_capacity_invalid"
    assert caught.value.details == {"min_capacity": 3, "max_capacity": 2}

    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(None, "fixed", 0, 0, None)
    assert caught.value.code == "pool_capacity_invalid"
    assert caught.value.field == "max_capacity"

    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(None, "fixed", -1, 1, None)
    assert caught.value.code == "pool_capacity_invalid"


@pytest.mark.parametrize("threshold", [Decimal("0"), Decimal("-0.1"), "0"])
def test_price_threshold_must_be_positive(threshold: object) -> None:
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(None, "fixed", 0, 1, threshold)  # type: ignore[arg-type]
    assert caught.value.code == "price_threshold_invalid"
    assert caught.value.field == "price_threshold_usd"


def test_positive_price_threshold_is_accepted() -> None:
    validate_pool_configuration(None, "fixed", 0, 1, Decimal("0.25"))


def test_connection_credential_must_be_a_live_resource_credential(
    session: Session, scope: dict[str, str]
) -> None:
    revoked = _credential(session, scope, revoked=True)
    connection = _connection(session, scope, credential=revoked)
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(connection, "spot", 1, 2, None, session=session)
    assert caught.value.code == "connection_credential_invalid"
    assert caught.value.field == "credential_id"
    assert caught.value.details["reason"] == "revoked"


def test_connection_credential_must_have_resource_kind(
    session: Session, scope: dict[str, str]
) -> None:
    model_credential = _credential(session, scope, kind="model")
    connection = _connection(session, scope, credential=model_credential)
    with pytest.raises(ResourceValidationError) as caught:
        validate_pool_configuration(connection, "spot", 1, 2, None, session=session)
    assert caught.value.code == "connection_credential_invalid"
    assert caught.value.details["reason"] == "kind"


def test_valid_spot_configuration_passes(session: Session, scope: dict[str, str]) -> None:
    connection = _connection(session, scope)
    validate_pool_configuration(
        connection,
        "spot",
        1,
        4,
        Decimal("0.75"),
        provider_key="aliyun",
        session=session,
        tenant_id=scope["tenant_id"],
    )


def test_validate_connection_credential_resolves_resource_credential(
    session: Session, scope: dict[str, str]
) -> None:
    credential = _credential(session, scope)
    resolved = validate_connection_credential(
        session,
        credential.id,
        tenant_id=scope["tenant_id"],
        project_id=scope["project_id"],
    )
    assert resolved is credential


@pytest.mark.parametrize(
    ("kind", "revoked", "reason"),
    [("model", False, "kind"), ("resource", True, "revoked")],
)
def test_validate_connection_credential_rejects_unusable_credential(
    session: Session,
    scope: dict[str, str],
    kind: str,
    revoked: bool,
    reason: str,
) -> None:
    credential = _credential(session, scope, kind=kind, revoked=revoked)
    with pytest.raises(ResourceValidationError) as caught:
        validate_connection_credential(
            session, credential.id, tenant_id=scope["tenant_id"]
        )
    assert caught.value.code == "connection_credential_invalid"
    assert caught.value.field == "credential_id"
    assert caught.value.details["reason"] == reason


def test_validate_connection_credential_reports_missing(
    session: Session, scope: dict[str, str]
) -> None:
    with pytest.raises(ResourceValidationError) as caught:
        validate_connection_credential(
            session, str(uuid4()), tenant_id=scope["tenant_id"]
        )
    assert caught.value.details["reason"] == "missing"


def test_validate_connection_credential_reports_project_mismatch(
    session: Session, scope: dict[str, str]
) -> None:
    credential = _credential(session, scope)
    with pytest.raises(ResourceValidationError) as caught:
        validate_connection_credential(
            session,
            credential.id,
            tenant_id=scope["tenant_id"],
            project_id=str(uuid4()),
        )
    assert caught.value.details["reason"] == "project"


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------


def test_connection_payload_never_exposes_secrets(
    session: Session, scope: dict[str, str]
) -> None:
    credential = _credential(session, scope, secret_mask="ak-***mask")
    connection = _connection(session, scope, credential=credential)
    payload = connection_payload(connection, session=session)

    assert set(payload) == {
        "id",
        "name",
        "provider",
        "region",
        "capabilities",
        "status",
        "last_verified_at",
        "credential_mask",
    }
    assert payload["credential_mask"] == "ak-***mask"
    assert payload["capabilities"] == ["ecs:inventory", "ecs:spot"]
    serialized = str(payload)
    assert "ciphertext" not in serialized
    assert "key_version" not in serialized
    assert "ciphertext-must-never-leak" not in serialized


def test_pool_payload_carries_capacity_policy(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope)
    checked_at = datetime.now(UTC)
    pool = _pool(
        session,
        scope,
        connection_id=connection.id,
        provisioning_mode="spot",
        min_capacity=1,
        max_capacity=5,
        instance_config_json={"instance_type": "ecs.g7.large"},
        price_threshold_usd=Decimal("0.5000"),
        price_usd=Decimal("0.4000"),
        capacity_reason="waiting_for_price",
        last_checked_at=checked_at,
        next_check_at=checked_at,
    )
    payload = pool_payload(pool)

    assert payload["id"] == pool.id
    assert payload["connection_id"] == connection.id
    assert payload["provisioning_mode"] == "spot"
    assert payload["min_capacity"] == 1
    assert payload["max_capacity"] == 5
    assert payload["instance_config"] == {"instance_type": "ecs.g7.large"}
    assert payload["price_threshold_usd"] == "0.5000"
    assert payload["price_usd"] == "0.4000"
    assert payload["capacity_reason"] == "waiting_for_price"
    assert payload["last_checked_at"] == checked_at.isoformat(timespec="microseconds")
    assert payload["next_check_at"] == checked_at.isoformat(timespec="microseconds")


# ---------------------------------------------------------------------------
# Provisioner interface, registry and fake provider
# ---------------------------------------------------------------------------


def test_fake_provisioner_satisfies_interface() -> None:
    fake = FakeProvisioner()
    assert isinstance(fake, Provisioner)
    assert fake.provider == "fake"
    assert fake.capabilities(None) == {"ecs:spot", "ecs:inventory"}  # type: ignore[arg-type]


def test_registry_registers_and_resets() -> None:
    assert get_provisioner("fake") is None
    fake = FakeProvisioner()
    assert register_provisioner(fake) is fake
    assert get_provisioner("fake") is fake
    reset_provisioners()
    assert get_provisioner("fake") is None


def test_fake_quote_returns_configured_price(
    session: Session, scope: dict[str, str]
) -> None:
    fake = FakeProvisioner(
        price_usd=Decimal("0.4200"),
        available=True,
        instance_type="ecs.g7.large",
    )
    connection = _connection(session, scope, provider="fake")
    pool = _pool(session, scope, connection_id=connection.id, provisioning_mode="spot")
    now = datetime.now(UTC)
    quote = fake.quote(connection=connection, pool=pool, now=now)

    assert isinstance(quote, PriceQuote)
    assert quote.price_usd == Decimal("0.4200")
    assert quote.available is True
    assert quote.region == "cn-hangzhou"
    assert quote.instance_type == "ecs.g7.large"
    assert quote.checked_at == now


def test_fake_create_adds_a_requested_instance(
    session: Session, scope: dict[str, str]
) -> None:
    fake = FakeProvisioner()
    connection = _connection(session, scope, provider="fake")
    pool = _pool(session, scope, connection_id=connection.id, provisioning_mode="spot")
    now = datetime.now(UTC)

    first = fake.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=now,
    )
    second = fake.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=now,
    )

    assert isinstance(first, ProvisionedInstance)
    assert first.provider_instance_id == "fake-instance-1"
    assert second.provider_instance_id == "fake-instance-2"
    assert first.status == "requested"
    assert first.spot_strategy == "spot"
    assert first.resource_pool_id == pool.id
    assert first.tenant_id == scope["tenant_id"]
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 2


def test_fake_create_failure_raises_and_adds_nothing(
    session: Session, scope: dict[str, str]
) -> None:
    fake = FakeProvisioner(create_error="quota_exceeded")
    connection = _connection(session, scope, provider="fake")
    pool = _pool(session, scope, connection_id=connection.id, provisioning_mode="spot")

    with pytest.raises(ProvisionerError) as caught:
        fake.create(
            session=session,
            connection=connection,
            pool=pool,
            spec=InstanceSpec(instance_type="ecs.g7.large"),
            now=datetime.now(UTC),
        )
    assert caught.value.code == "quota_exceeded"
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0


def test_fake_create_without_inventory_raises(
    session: Session, scope: dict[str, str]
) -> None:
    fake = FakeProvisioner(available=False)
    connection = _connection(session, scope, provider="fake")
    pool = _pool(session, scope, connection_id=connection.id, provisioning_mode="spot")

    with pytest.raises(ProvisionerError) as caught:
        fake.create(
            session=session,
            connection=connection,
            pool=pool,
            spec=InstanceSpec(instance_type="ecs.g7.large"),
            now=datetime.now(UTC),
        )
    assert caught.value.code == "inventory_unavailable"
    assert session.scalar(select(func.count()).select_from(ProvisionedInstance)) == 0


def test_fake_release_marks_instance_released(
    session: Session, scope: dict[str, str]
) -> None:
    fake = FakeProvisioner()
    connection = _connection(session, scope, provider="fake")
    pool = _pool(session, scope, connection_id=connection.id, provisioning_mode="spot")
    instance = fake.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=datetime.now(UTC),
    )
    released_at = datetime.now(UTC)

    fake.release(session=session, instance=instance, now=released_at)

    assert instance.status == "released"
    assert instance.released_at == released_at
    assert fake.health(instance=instance) == "healthy"


def test_fake_release_failure_raises_and_keeps_state(
    session: Session, scope: dict[str, str]
) -> None:
    fake = FakeProvisioner()
    connection = _connection(session, scope, provider="fake")
    pool = _pool(session, scope, connection_id=connection.id, provisioning_mode="spot")
    instance = fake.create(
        session=session,
        connection=connection,
        pool=pool,
        spec=InstanceSpec(instance_type="ecs.g7.large"),
        now=datetime.now(UTC),
    )
    fake.release_error = "release_denied"

    with pytest.raises(ProvisionerError) as caught:
        fake.release(session=session, instance=instance, now=datetime.now(UTC))
    assert caught.value.code == "release_denied"
    assert instance.status == "requested"
    assert instance.released_at is None


def test_fake_reports_custom_health() -> None:
    fake = FakeProvisioner(health_status="degraded")
    assert fake.health(instance=object()) == "degraded"  # type: ignore[arg-type]


def test_capability_error_carries_code_and_fields() -> None:
    error = CapabilityError(
        "connection_provider_mismatch",
        "connection lacks ecs:spot",
        field="connection_id",
        details={"missing": ["ecs:spot"]},
    )
    assert error.code == "connection_provider_mismatch"
    assert error.field == "connection_id"
    assert error.details == {"missing": ["ecs:spot"]}


# ---------------------------------------------------------------------------
# Connection verification
# ---------------------------------------------------------------------------


def test_verify_connection_marks_it_verified(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope, provider="fake")
    fake = FakeProvisioner()
    now = datetime.now(UTC)

    payload = verify_connection(session, connection, provisioner=fake, now=now)

    assert connection.status == "verified"
    assert connection.last_verified_at == now
    assert payload["status"] == "verified"
    assert payload["last_verified_at"] == now.isoformat(timespec="microseconds")


def test_verify_connection_marks_it_failed_for_unsupported_capability(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope, provider="fake", capabilities=("ecs:gpu",))
    fake = FakeProvisioner()
    now = datetime.now(UTC)

    payload = verify_connection(session, connection, provisioner=fake, now=now)

    assert connection.status == "failed"
    assert connection.last_verified_at == now
    assert payload["status"] == "failed"


def test_verify_connection_marks_it_failed_for_provider_mismatch(
    session: Session, scope: dict[str, str]
) -> None:
    connection = _connection(session, scope, provider="aliyun")
    fake = FakeProvisioner()
    payload = verify_connection(
        session, connection, provisioner=fake, now=datetime.now(UTC)
    )
    assert connection.status == "failed"
    assert payload["status"] == "failed"
