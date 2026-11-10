"""Replaceable compute provisioner interface (M4-03, design 06 §6).

A provisioner owns one provider / provisioning mode: it declares the
connection capabilities it can serve, quotes the current spot price, creates
and releases cloud instances, and reports instance health. The capacity
controller (part B) talks only to this seam, so adding a mode never touches
scheduling code.

Capability keys are stable strings stored in
``connection.capabilities_json["capabilities"]``; the spot mode needs
``ecs:spot`` and quote/inventory needs ``ecs:inventory``.

No network client ships in this module: real Aliyun provisioning is part of
the worker milestone, while tests register :class:`FakeProvisioner`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from sqlalchemy.orm import Session

from ...models import Connection, ProvisionedInstance, ResourcePool

CAPABILITY_ECS_SPOT = "ecs:spot"
CAPABILITY_ECS_INVENTORY = "ecs:inventory"

PROVISIONER_CAPABILITIES = frozenset({CAPABILITY_ECS_SPOT, CAPABILITY_ECS_INVENTORY})


@dataclass(frozen=True)
class PriceQuote:
    """One spot price sample for a connection/pool pair."""

    price_usd: Decimal
    available: bool
    region: str | None
    instance_type: str | None
    checked_at: datetime


@dataclass(frozen=True)
class InstanceSpec:
    """Instance parameters passed from the pool configuration."""

    instance_type: str
    image_id: str | None = None
    spot: bool = True


class ProvisionerError(Exception):
    """Provider-side failure carrying a stable, user-safe ``code``.

    ``code`` is safe to publish in errors/events; ``message`` must never
    contain credentials, cloud request IDs with secrets, or stack traces.
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message or code
        self.details = dict(details or {})
        super().__init__(self.message)

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = dict(self.details)
        return payload


class CapabilityError(ProvisionerError):
    """The connection/pool pair cannot be served by this provisioner.

    Raised by provisioners that validate the pair themselves; the API path
    normally pre-validates through :mod:`app.services.resources` and never
    reaches instance creation.
    """

    def __init__(
        self,
        code: str,
        message: str | None = None,
        *,
        field: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.field = field
        super().__init__(code, message, details=details)

    def as_dict(self) -> dict[str, Any]:
        payload = super().as_dict()
        if self.field:
            payload["field"] = self.field
        return payload


@runtime_checkable
class Provisioner(Protocol):
    """The four-part seam from design 06 §6.2/§8.3.

    ``create``/``release`` add or mutate rows in the caller's session and
    never commit: the capacity controller owns the transaction boundary.
    """

    provider: str

    def capabilities(self, connection: Connection | None) -> set[str]:
        """Capability keys this provider supports for ``connection``."""

    def quote(
        self,
        *,
        connection: Connection | None,
        pool: ResourcePool,
        now: datetime,
    ) -> PriceQuote:
        """Current price/availability for the pool's instance type."""

    def create(
        self,
        *,
        session: Session,
        connection: Connection | None,
        pool: ResourcePool,
        spec: InstanceSpec,
        now: datetime,
    ) -> ProvisionedInstance:
        """Request one instance and flush it into ``session``."""

    def release(
        self,
        *,
        session: Session,
        instance: ProvisionedInstance,
        now: datetime,
    ) -> None:
        """Release ``instance`` and flush the state change."""

    def health(self, *, instance: ProvisionedInstance) -> str:
        """Provider-side liveness/state hint for one instance."""


_PROVISIONERS: dict[str, Provisioner] = {}


def register_provisioner(provisioner: Provisioner) -> Provisioner:
    """Register ``provisioner`` under its ``provider`` key (idempotent)."""

    _PROVISIONERS[provisioner.provider] = provisioner
    return provisioner


def get_provisioner(provider: str) -> Provisioner | None:
    """Return the provisioner for ``provider`` or ``None`` when unknown."""

    return _PROVISIONERS.get(provider)


def reset_provisioners() -> None:
    """Clear the registry (tests and worker start-up)."""

    _PROVISIONERS.clear()


def registered_providers() -> tuple[str, ...]:
    """Registered provider keys, sorted, for diagnostics and admin payloads."""

    return tuple(sorted(_PROVISIONERS))
