"""Resource connection and pool configuration helpers (M4-03 A).

Design references: 05 §5 (connection/resource pool tables), 04 §1 (error
codes) and 06 §6 (spot provisioning). The API layer (part B) calls these
helpers to reject incompatible connection/mode pairs with stable codes
*before* it creates a pool or an instance, and to serialize connections
without ever exposing credential ciphertext or key versions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from ..models import Connection, Credential, ResourcePool
from ..money import Money, MoneyError, money_str
from .provisioning import CAPABILITY_ECS_SPOT, Provisioner, ProvisionerError

PROVISIONING_MODE_FIXED = "fixed"
PROVISIONING_MODE_SPOT = "spot"
PROVISIONING_MODES = (PROVISIONING_MODE_FIXED, PROVISIONING_MODE_SPOT)

CONNECTION_STATUS_UNVERIFIED = "unverified"
CONNECTION_STATUS_VERIFIED = "verified"
CONNECTION_STATUS_FAILED = "failed"

#: Structural punctuation left over when a JSON secret is masked.
CHARS_TO_STRIP = '"}'


class ResourceValidationError(Exception):
    """Stable pool/connection validation failure.

    ``code`` is the user-facing error code (design 04 §1), ``field`` points
    at the request field to highlight and ``details`` carries the machine
    readable reason. Callers turn this into::

        {"error": {"code": ..., "message": ..., "fields": {field: ...}}}
    """

    def __init__(
        self,
        code: str,
        message: str,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.field = field
        self.details = dict(details or {})
        super().__init__(message)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.field:
            payload["field"] = self.field
        if self.details:
            payload["details"] = dict(self.details)
        return payload


def _aware(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC for serialization."""

    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    value = _aware(value)
    return None if value is None else value.isoformat(timespec="microseconds")


def _display_credential_mask(mask: str | None) -> str | None:
    """Keep the mask readable when the secret is a JSON document.

    Aliyun credentials are stored as ``{"access_key_id": ..., "access_key_secret": ...}``,
    so ``mask_secret`` can end with ``"}``. Those structural characters are not
    secret material but make the UI look broken; trim them without widening the
    mask itself.
    """

    return None if mask is None else mask.rstrip(CHARS_TO_STRIP)


def connection_capabilities(connection: Connection | None) -> set[str]:
    """Capability keys declared by ``connection`` (stable string set)."""

    if connection is None or not isinstance(connection.capabilities_json, dict):
        return set()
    declared = connection.capabilities_json.get("capabilities")
    if not isinstance(declared, (list, tuple, set, frozenset)):
        return set()
    return {str(item) for item in declared if isinstance(item, str) and item}


def _credential_reason(credential: Credential | None, *, tenant_id: str) -> str | None:
    """Why ``credential`` cannot back a connection, or ``None`` when usable."""

    if credential is None:
        return "missing"
    if credential.tenant_id != tenant_id:
        return "tenant"
    if credential.kind != "resource":
        return "kind"
    if credential.revoked_at is not None:
        return "revoked"
    return None


def _credential_error(credential_id: str, reason: str) -> ResourceValidationError:
    return ResourceValidationError(
        "connection_credential_invalid",
        "connection credential is missing, revoked or not a resource credential",
        field="credential_id",
        details={"credential_id": credential_id, "reason": reason},
    )


def _credential_failure_reason(
    session: Session,
    connection: Connection,
    tenant_id: str | None,
) -> str | None:
    """Why ``connection`` cannot be used, or ``None`` when it is usable."""

    if tenant_id is not None and connection.tenant_id != tenant_id:
        return "tenant"
    credential = session.get(Credential, connection.credential_id)
    return _credential_reason(credential, tenant_id=connection.tenant_id)


def validate_connection_credential(
    session: Session,
    credential_id: str,
    *,
    tenant_id: str,
    project_id: str | None = None,
) -> Credential:
    """Resolve a live ``kind='resource'`` credential for a new connection.

    Connection creation has no pool/connection row yet, so it needs the same
    stable error as pool validation: ``connection_credential_invalid`` with
    ``field='credential_id'`` and ``details.reason`` in
    ``missing | tenant | kind | revoked | project``. Returns the credential
    so the caller can copy ``provider``/``secret_mask`` onto the connection.
    """

    credential = session.get(Credential, credential_id)
    if credential is None:
        raise _credential_error(credential_id, "missing")
    reason = _credential_reason(credential, tenant_id=tenant_id)
    if reason is None and project_id is not None and credential.project_id != project_id:
        reason = "project"
    if reason is not None:
        raise _credential_error(credential_id, reason)
    return credential


def validate_pool_configuration(
    connection: Connection | None,
    provisioning_mode: str,
    min_capacity: int,
    max_capacity: int,
    price_threshold_cny: object | None,
    *,
    provider_key: str | None = None,
    session: Session | None = None,
    tenant_id: str | None = None,
) -> None:
    """Validate one pool before it is persisted (design 06 §6.1).

    ``fixed`` ignores ``connection``; ``spot`` requires a tenant-owned
    connection whose declared capabilities include ``ecs:spot`` and whose
    provider matches ``provider_key``. Pass ``session``/``tenant_id`` so the
    referenced credential is checked (``kind='resource'``, not revoked,
    same tenant); without a session only the pair itself is validated.

    Raises :class:`ResourceValidationError`; returns ``None`` on success.
    """

    mode = (provisioning_mode or "").strip()
    if mode not in PROVISIONING_MODES:
        raise ResourceValidationError(
            "pool_provisioning_mode_invalid",
            f"unsupported provisioning mode: {provisioning_mode!r}",
            field="provisioning_mode",
        )

    if mode == PROVISIONING_MODE_SPOT:
        if connection is None:
            raise ResourceValidationError(
                "connection_missing",
                "spot provisioning requires a resource connection",
                field="connection_id",
            )

        missing = sorted({CAPABILITY_ECS_SPOT} - connection_capabilities(connection))
        if missing:
            raise ResourceValidationError(
                "connection_provider_mismatch",
                "connection does not support spot ECS provisioning",
                field="connection_id",
                details={"missing": missing},
            )

        if provider_key is not None and provider_key != connection.provider:
            raise ResourceValidationError(
                "connection_provider_mismatch",
                "pool provider must match the connection provider",
                field="provider_key",
                details={"expected": connection.provider},
            )

    try:
        min_value = int(min_capacity)
        max_value = int(max_capacity)
    except (TypeError, ValueError):
        raise ResourceValidationError(
            "pool_capacity_invalid",
            "min_capacity and max_capacity must be integers",
            field="max_capacity",
            details={"min_capacity": min_capacity, "max_capacity": max_capacity},
        ) from None

    if max_value < 1 or min_value < 0 or min_value > max_value:
        raise ResourceValidationError(
            "pool_capacity_invalid",
            "capacity must satisfy 1 <= max_capacity and 0 <= min_capacity <= max_capacity",
            field="max_capacity" if max_value < 1 else "min_capacity",
            details={"min_capacity": min_value, "max_capacity": max_value},
        )

    if price_threshold_cny is not None:
        try:
            threshold = Money.of(price_threshold_cny)
        except MoneyError:
            raise ResourceValidationError(
                "price_threshold_invalid",
                "price_threshold_cny must be a positive decimal",
                field="price_threshold_cny",
                details={"price_threshold_cny": str(price_threshold_cny)},
            ) from None
        if threshold <= 0:
            raise ResourceValidationError(
                "price_threshold_invalid",
                "price_threshold_cny must be positive when set",
                field="price_threshold_cny",
                details={"price_threshold_cny": threshold.as_str()},
            )

    if mode == PROVISIONING_MODE_FIXED:
        return

    if session is not None and connection is not None:
        reason = _credential_failure_reason(session, connection, tenant_id)
        if reason is not None:
            raise _credential_error(connection.credential_id, reason)


def connection_payload(
    connection: Connection,
    *,
    credential: Credential | None = None,
    session: Session | None = None,
) -> dict[str, Any]:
    """API-safe connection shape; never includes ciphertext/key_version.

    Only ``Credential.secret_mask`` is exposed. Pass ``session`` (or a loaded
    ``credential``) so the mask can be resolved.
    """

    if credential is None and session is not None and connection.credential_id:
        credential = session.get(Credential, connection.credential_id)
    return {
        "id": connection.id,
        "name": connection.name,
        "provider": connection.provider,
        "region": connection.region,
        "capabilities": sorted(connection_capabilities(connection)),
        "status": connection.status,
        "last_verified_at": _iso(connection.last_verified_at),
        "credential_mask": (
            _display_credential_mask(credential.secret_mask)
            if credential is not None
            else None
        ),
    }


def pool_payload(pool: ResourcePool) -> dict[str, Any]:
    """API-safe pool shape including capacity/price policy state."""

    return {
        "id": pool.id,
        "project_id": pool.project_id,
        "connection_id": pool.connection_id,
        "name": pool.name,
        "provider_key": pool.provider_key,
        "runner_group": pool.runner_group,
        "labels": pool.labels_json if pool.labels_json is not None else {},
        "status": pool.status,
        "provisioning_mode": pool.provisioning_mode,
        "min_capacity": pool.min_capacity,
        "max_capacity": pool.max_capacity,
        "instance_config": dict(pool.instance_config_json or {}),
        "price_threshold_cny": money_str(pool.price_threshold_cny),
        "price_cny": money_str(pool.price_cny),
        "capacity_reason": pool.capacity_reason,
        "last_checked_at": _iso(pool.last_checked_at),
        "next_check_at": _iso(pool.next_check_at),
    }


def verify_connection(
    session: Session,
    connection: Connection,
    *,
    provisioner: Provisioner,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Read-only verification: provider match + declared capabilities + API.

    The provider's ``verify`` performs a read-only cloud call with the
    decrypted connection credential (M4-04), so a syntactically valid but
    unauthorized AccessKey is rejected before a pool can be used. Any
    provider error marks the connection ``failed`` without leaking the
    credential or request details.

    Sets ``status`` to verified/failed and always records
    ``last_verified_at`` so the UI can show "last checked". Flushes but does
    not commit; the caller owns the transaction.
    """

    now = now or datetime.now(UTC)
    supported = set(provisioner.capabilities(connection))
    declared = connection_capabilities(connection)
    verified = connection.provider == provisioner.provider and declared <= supported
    if verified:
        try:
            provisioner.verify(session=session, connection=connection)
        except ProvisionerError:
            verified = False
    connection.status = (
        CONNECTION_STATUS_VERIFIED if verified else CONNECTION_STATUS_FAILED
    )
    connection.last_verified_at = now
    session.flush()
    return connection_payload(connection, session=session)
