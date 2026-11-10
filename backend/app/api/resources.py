"""Resource connection and pool console API (M4-03 B).

Administrators own the writes; every authenticated member can read. The two
rules the acceptance cares about live here:

* an incompatible connection/provisioning-mode pair is rejected with
  ``422 connection_provider_mismatch`` (plus a ``fields`` map) *before* a pool
  or an instance is created;
* no response ever contains credential material — only the credential mask is
  exposed, and package/price payloads carry decimal strings instead of
  ciphertext or key versions.

Capacity state is read-only here: the worker's capacity controller
(:mod:`app.services.capacity_control`) is the only writer of that snapshot.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..config import Settings, get_settings
from ..db import get_session
from ..defaults import DEFAULT_PROJECT_ID
from ..models import Connection, Credential, ResourceEvent, ResourcePool, Runner
from ..services import capacity_control, identity
from ..services.provisioning import (
    ProvisionerError,
    get_provisioner,
)
from ..services.resources import (
    ResourceValidationError,
    connection_payload,
    pool_payload,
    validate_pool_configuration,
    verify_connection,
)
from .dependencies import require_admin, require_session
from .errors import error_detail

router = APIRouter(tags=["resources"])

#: Per-connection status codes mapped onto the shared error envelope.
POOL_NOT_FOUND = "resource_pool_not_found"
CONNECTION_NOT_FOUND = "connection_not_found"
RECENT_EVENT_LIMIT = 20


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class ConnectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    provider: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    credential_id: str = Field(min_length=1, max_length=36)
    capabilities: list[str] = Field(default_factory=list, max_length=32)


class PoolCreate(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    connection_id: str | None = Field(default=None, max_length=36)
    provisioning_mode: str = Field(min_length=1, max_length=32)
    min_capacity: int = Field(ge=0)
    max_capacity: int = Field(ge=1)
    price_threshold_usd: Decimal | None = None
    instance_config: dict[str, Any] = Field(default_factory=dict)
    runner_group: str | None = Field(default=None, max_length=128)
    labels: dict[str, Any] = Field(default_factory=dict)


class PoolUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    connection_id: str | None = None
    provisioning_mode: str | None = Field(default=None, max_length=32)
    min_capacity: int | None = Field(default=None, ge=0)
    max_capacity: int | None = Field(default=None, ge=1)
    price_threshold_usd: Decimal | None = None
    instance_config: dict[str, Any] | None = None
    runner_group: str | None = Field(default=None, max_length=128)
    labels: dict[str, Any] | None = None


class TestProvisioningRequest(BaseModel):
    confirm_cost: bool = False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    aware = _as_utc(value)
    return None if aware is None else aware.isoformat(timespec="microseconds")


def _price_str(value: Decimal | None) -> str | None:
    return None if value is None else str(value)


def _http_error(
    status_code: int,
    code: str,
    message: str,
    *,
    fields: dict[str, str] | None = None,
    details: dict[str, Any] | None = None,
) -> HTTPException:
    detail = error_detail(code, message, details=details)
    if fields:
        detail["error"]["fields"] = fields
    return HTTPException(status_code=status_code, detail=detail)


def _validation_error(exc: ResourceValidationError) -> HTTPException:
    """Turn A's validation exception into the stable 422 envelope."""

    return _http_error(
        422,
        exc.code,
        exc.message,
        fields={exc.field: exc.message} if exc.field else None,
        details=exc.details or None,
    )


def _credential_failure(session: Session, tenant_id: str, credential_id: str) -> str | None:
    credential = session.get(Credential, credential_id)
    if credential is None:
        return "missing"
    if credential.tenant_id != tenant_id:
        return "tenant"
    if credential.kind != "resource":
        return "kind"
    if credential.revoked_at is not None:
        return "revoked"
    return None


def _tenant_connection(
    session: Session, tenant_id: str, connection_id: str
) -> Connection | None:
    connection = session.get(Connection, connection_id)
    if connection is None or connection.tenant_id != tenant_id:
        return None
    return connection


def _tenant_pool(session: Session, tenant_id: str, pool_id: str) -> ResourcePool:
    pool = session.get(ResourcePool, pool_id)
    if pool is None or pool.tenant_id != tenant_id:
        raise _http_error(404, POOL_NOT_FOUND, "资源池不存在")
    return pool


def _capacity_state(
    session: Session, pool: ResourcePool, settings: Settings
) -> dict[str, Any]:
    """Read-only snapshot; the registry provisioner answers the spot quote."""

    return capacity_control.pool_capacity_state(
        session,
        pool,
        settings=settings,
        provisioner=get_provisioner(pool.provider_key),
    )


def _event_payload(event: ResourceEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "event_type": event.event_type,
        "resource_pool_id": event.resource_pool_id,
        "connection_id": event.connection_id,
        "payload": event.payload_json or {},
        "dedupe_key": event.dedupe_key,
        "created_at": _iso(event.created_at),
    }


def _runner_summary(session: Session, pool: ResourcePool) -> dict[str, int]:
    summary = {"total": 0, "online": 0, "busy": 0, "draining": 0, "offline": 0}
    rows = session.execute(
        select(Runner.status, func.count())
        .where(Runner.resource_pool_id == pool.id)
        .group_by(Runner.status)
    ).all()
    for runner_status, count in rows:
        summary["total"] += int(count)
        if runner_status in summary:
            summary[runner_status] += int(count)
    return summary


def _runner_payload(runner: Runner) -> dict[str, Any]:
    labels = runner.labels_json if isinstance(runner.labels_json, (list, dict)) else []
    return {
        "id": runner.id,
        "name": runner.name,
        "status": runner.status,
        "labels": labels,
        "last_heartbeat_at": _iso(runner.last_heartbeat_at),
        "busy_case_attempt_id": runner.busy_case_attempt_id,
    }


def _pool_state_payload(
    session: Session, pool: ResourcePool, settings: Settings
) -> dict[str, Any]:
    return {**pool_payload(pool), "capacity_state": _capacity_state(session, pool, settings)}


def _resolve_connection_for_write(
    session: Session, tenant_id: str, connection_id: str | None
) -> Connection | None:
    if connection_id is None:
        return None
    connection = _tenant_connection(session, tenant_id, connection_id)
    if connection is None:
        raise _http_error(
            422,
            "connection_missing",
            "指定的资源连接不存在或不属于当前租户",
            fields={"connection_id": "not found"},
        )
    return connection


def _provider_key(mode: str, connection: Connection | None) -> str | None:
    if mode == capacity_control.PROVISIONING_MODE_SPOT:
        return connection.provider if connection is not None else None
    return capacity_control.PROVISIONING_MODE_FIXED


def _commit_or_conflict(session: Session, message: str) -> None:
    try:
        session.commit()
    except IntegrityError as exc:
        session.rollback()
        raise _http_error(409, "resource_conflict", message) from exc


# ---------------------------------------------------------------------------
# Connections
# ---------------------------------------------------------------------------


@router.get("/api/v1/connections")
def list_connections(
    context: Annotated[identity.AuthContext, Depends(require_session)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    connections = list(
        session.scalars(
            select(Connection)
            .where(Connection.tenant_id == context.tenant.id)
            .order_by(Connection.name)
        )
    )
    return {
        "items": [
            connection_payload(connection, session=session) for connection in connections
        ]
    }


@router.post("/api/v1/connections", status_code=status.HTTP_201_CREATED)
def create_connection(
    payload: ConnectionCreate,
    context: Annotated[identity.AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    tenant_id = context.tenant.id
    failure = _credential_failure(session, tenant_id, payload.credential_id)
    if failure is not None:
        raise _http_error(
            422,
            "connection_credential_invalid",
            "资源连接必须引用本租户未撤销的资源凭据",
            fields={"credential_id": failure},
            details={"credential_id": payload.credential_id, "reason": failure},
        )

    capabilities = sorted({item.strip() for item in payload.capabilities if item.strip()})
    connection = Connection(
        tenant_id=tenant_id,
        project_id=str(DEFAULT_PROJECT_ID),
        name=payload.name,
        provider=payload.provider,
        region=payload.region,
        credential_id=payload.credential_id,
        capabilities_json={"capabilities": capabilities},
        status="unverified",
    )
    session.add(connection)
    _commit_or_conflict(session, "同名资源连接已存在")
    return connection_payload(connection, session=session)


@router.post("/api/v1/connections/{connection_id}/test")
def test_connection(
    connection_id: str,
    context: Annotated[identity.AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    connection = _tenant_connection(session, context.tenant.id, connection_id)
    if connection is None:
        raise _http_error(404, CONNECTION_NOT_FOUND, "资源连接不存在")
    provisioner = get_provisioner(connection.provider)
    if provisioner is None:
        raise _http_error(
            422,
            "provisioner_unavailable",
            f"没有可用于 {connection.provider} 的供给器",
            fields={"provider": connection.provider},
        )
    payload = verify_connection(
        session, connection, provisioner=provisioner, now=_now()
    )
    session.commit()
    return payload


# ---------------------------------------------------------------------------
# Resource pools
# ---------------------------------------------------------------------------


@router.get("/api/v1/resource-pools")
def list_resource_pools(
    context: Annotated[identity.AuthContext, Depends(require_session)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    pools = list(
        session.scalars(
            select(ResourcePool)
            .where(ResourcePool.tenant_id == context.tenant.id)
            .order_by(ResourcePool.name)
        )
    )
    return {"items": [_pool_state_payload(session, pool, settings) for pool in pools]}


@router.post("/api/v1/resource-pools", status_code=status.HTTP_201_CREATED)
def create_resource_pool(
    payload: PoolCreate,
    context: Annotated[identity.AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    tenant_id = context.tenant.id
    mode = payload.provisioning_mode.strip()
    connection = _resolve_connection_for_write(session, tenant_id, payload.connection_id)
    provider_key = _provider_key(mode, connection)
    try:
        validate_pool_configuration(
            connection,
            mode,
            payload.min_capacity,
            payload.max_capacity,
            payload.price_threshold_usd,
            provider_key=provider_key,
            session=session,
            tenant_id=tenant_id,
        )
    except ResourceValidationError as exc:
        raise _validation_error(exc) from exc

    pool = ResourcePool(
        tenant_id=tenant_id,
        project_id=str(DEFAULT_PROJECT_ID),
        connection_id=connection.id if connection is not None else None,
        name=payload.name,
        provider_key=provider_key or capacity_control.PROVISIONING_MODE_FIXED,
        runner_group=payload.runner_group,
        provisioning_mode=mode,
        min_capacity=payload.min_capacity,
        max_capacity=payload.max_capacity,
        instance_config_json=dict(payload.instance_config),
        price_threshold_usd=payload.price_threshold_usd,
        labels_json=dict(payload.labels),
        status="active",
    )
    session.add(pool)
    _commit_or_conflict(session, "同名资源池已存在")
    return pool_payload(pool)


@router.get("/api/v1/resource-pools/{pool_id}")
def get_resource_pool(
    pool_id: str,
    context: Annotated[identity.AuthContext, Depends(require_session)],
    session: Annotated[Session, Depends(get_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict[str, Any]:
    pool = _tenant_pool(session, context.tenant.id, pool_id)
    events = list(
        session.scalars(
            select(ResourceEvent)
            .where(ResourceEvent.resource_pool_id == pool.id)
            .order_by(ResourceEvent.created_at.desc(), ResourceEvent.id.desc())
            .limit(RECENT_EVENT_LIMIT)
        )
    )
    return {
        **_pool_state_payload(session, pool, settings),
        "events": [_event_payload(event) for event in events],
        "runners": _runner_summary(session, pool),
    }


@router.patch("/api/v1/resource-pools/{pool_id}")
def update_resource_pool(
    pool_id: str,
    payload: PoolUpdate,
    context: Annotated[identity.AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    tenant_id = context.tenant.id
    pool = _tenant_pool(session, tenant_id, pool_id)
    fields = payload.model_dump(exclude_unset=True)

    mode = str(fields.get("provisioning_mode", pool.provisioning_mode)).strip()
    if "connection_id" in fields:
        connection = _resolve_connection_for_write(
            session, tenant_id, fields["connection_id"]
        )
    elif pool.connection_id:
        connection = _tenant_connection(session, tenant_id, pool.connection_id)
    else:
        connection = None

    min_capacity = int(fields.get("min_capacity", pool.min_capacity))
    max_capacity = int(fields.get("max_capacity", pool.max_capacity))
    price_threshold = (
        fields["price_threshold_usd"]
        if "price_threshold_usd" in fields
        else pool.price_threshold_usd
    )
    provider_key = _provider_key(mode, connection)
    try:
        validate_pool_configuration(
            connection,
            mode,
            min_capacity,
            max_capacity,
            price_threshold,
            provider_key=provider_key,
            session=session,
            tenant_id=tenant_id,
        )
    except ResourceValidationError as exc:
        raise _validation_error(exc) from exc

    pool.provisioning_mode = mode
    pool.provider_key = provider_key or capacity_control.PROVISIONING_MODE_FIXED
    pool.connection_id = connection.id if connection is not None else None
    pool.min_capacity = min_capacity
    pool.max_capacity = max_capacity
    pool.price_threshold_usd = price_threshold
    if "name" in fields and fields["name"]:
        pool.name = str(fields["name"])
    if "instance_config" in fields:
        pool.instance_config_json = dict(fields["instance_config"] or {})
    if "runner_group" in fields:
        pool.runner_group = fields["runner_group"]
    if "labels" in fields:
        pool.labels_json = dict(fields["labels"] or {})

    _commit_or_conflict(session, "资源池配置冲突")
    return pool_payload(pool)


@router.get("/api/v1/resource-pools/{pool_id}/runners")
def list_pool_runners(
    pool_id: str,
    context: Annotated[identity.AuthContext, Depends(require_session)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    pool = _tenant_pool(session, context.tenant.id, pool_id)
    runners = list(
        session.scalars(
            select(Runner)
            .where(Runner.resource_pool_id == pool.id)
            .order_by(Runner.registered_at.desc(), Runner.name)
        )
    )
    return {"items": [_runner_payload(runner) for runner in runners]}


@router.post("/api/v1/resource-pools/{pool_id}/test-provisioning")
def test_provisioning(
    pool_id: str,
    payload: TestProvisioningRequest,
    context: Annotated[identity.AuthContext, Depends(require_admin)],
    session: Annotated[Session, Depends(get_session)],
) -> dict[str, Any]:
    pool = _tenant_pool(session, context.tenant.id, pool_id)
    if not payload.confirm_cost:
        raise _http_error(
            422,
            "cost_confirmation_required",
            "测试供给会创建真实实例并产生费用，需要显式确认",
            fields={"confirm_cost": "required"},
        )
    if pool.provisioning_mode != capacity_control.PROVISIONING_MODE_SPOT:
        raise _http_error(
            422,
            "pool_provisioning_mode_invalid",
            "只有竞价资源池支持测试供给",
            fields={"provisioning_mode": pool.provisioning_mode},
        )
    provisioner = get_provisioner(pool.provider_key)
    if provisioner is None:
        raise _http_error(
            422,
            "provisioner_unavailable",
            f"没有可用于 {pool.provider_key} 的供给器",
            fields={"provider_key": pool.provider_key},
        )
    connection = (
        _tenant_connection(session, context.tenant.id, pool.connection_id)
        if pool.connection_id
        else None
    )
    if connection is None:
        raise _http_error(
            422,
            "connection_missing",
            "竞价资源池必须引用资源连接",
            fields={"connection_id": "required"},
        )

    now = _now()
    try:
        quote = provisioner.quote(connection=connection, pool=pool, now=now)
        instance = provisioner.create(
            session=session,
            connection=connection,
            pool=pool,
            spec=capacity_control.instance_spec(pool),
            now=now,
        )
    except ProvisionerError as exc:
        raise _http_error(422, exc.code, exc.message, details=exc.details or None) from exc

    price_usd = _price_str(quote.price_usd)
    threshold = _price_str(pool.price_threshold_usd)
    capacity_control.append_resource_event(
        session,
        pool,
        capacity_control.EVENT_PROVISIONING_TESTED,
        {
            "instance_id": instance.id,
            "provider_instance_id": instance.provider_instance_id,
            "price_usd": price_usd,
            "threshold": threshold,
        },
    )
    identity.record_audit(
        session,
        tenant_id=context.tenant.id,
        actor_user_id=context.user.id,
        action=capacity_control.EVENT_PROVISIONING_TESTED,
        target_type="resource_pool",
        target_id=pool.id,
        metadata={
            "instance_id": instance.id,
            "price_usd": price_usd,
            "price_threshold_usd": threshold,
        },
    )
    session.commit()

    return {
        "instance": {
            "id": instance.id,
            "provider_instance_id": instance.provider_instance_id,
            "resource_pool_id": instance.resource_pool_id,
            "status": instance.status,
            "spot_strategy": instance.spot_strategy,
            "created_at": _iso(instance.created_at),
        },
        "price_usd": price_usd,
        "price_threshold_usd": threshold,
        "price_available": quote.available,
        "region": quote.region,
        "instance_type": quote.instance_type,
    }
