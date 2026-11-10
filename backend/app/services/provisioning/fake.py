"""Deterministic fake provisioner for capacity tests (M4-03).

The fake never touches the network. Instance ids come from a module counter
(:func:`reset_fake_instance_counter`) so tests can assert exact values, and
every failure mode is a constructor/attribute knob:

- ``available=False`` -> ``create`` raises ``inventory_unavailable``;
- ``create_error`` -> ``create`` raises with that code;
- ``release_error`` -> ``release`` raises with that code (state unchanged);
- ``health_status`` -> value returned by ``health``;
- ``sync_error`` -> ``sync`` raises with that code (provider poll failure);
- ``sync_counts`` -> value returned by ``sync`` (tests can inject counters).
"""

from __future__ import annotations

import itertools
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from ...models import Connection, ProvisionedInstance, ResourcePool
from . import (
    CAPABILITY_ECS_INVENTORY,
    CAPABILITY_ECS_SPOT,
    InstanceSpec,
    PriceQuote,
    ProvisionerError,
)

_INSTANCE_COUNTER = itertools.count(1)


def reset_fake_instance_counter() -> None:
    """Restart ``fake-instance-{n}`` numbering at 1."""

    global _INSTANCE_COUNTER
    _INSTANCE_COUNTER = itertools.count(1)


def _error_code(value: object, default: str) -> str:
    return value if isinstance(value, str) and value else default


class FakeProvisioner:
    """In-memory provisioner implementing :class:`~.Provisioner`."""

    def __init__(
        self,
        *,
        provider: str = "fake",
        price_usd: Decimal | int | float | str = Decimal("0.1000"),
        available: bool = True,
        create_error: str | bool | None = None,
        release_error: str | bool | None = None,
        health_status: str = "healthy",
        instance_type: str = "ecs.g7.large",
        region: str = "cn-hangzhou",
        sync_error: str | bool | None = None,
        sync_counts: dict[str, int] | None = None,
    ) -> None:
        self.provider = provider
        self.price_usd = Decimal(str(price_usd))
        self.available = available
        self.create_error = create_error
        self.release_error = release_error
        self.health_status = health_status
        self.instance_type = instance_type
        self.region = region
        self.sync_error = sync_error
        self.sync_counts = dict(sync_counts or {})

    def capabilities(self, connection: Connection | None) -> set[str]:
        return {CAPABILITY_ECS_SPOT, CAPABILITY_ECS_INVENTORY}

    def verify(self, *, session: Session, connection: Connection) -> dict[str, object]:
        return {
            "ok": True,
            "provider": self.provider,
            "region": connection.region or self.region,
            "capabilities": sorted(self.capabilities(connection)),
        }

    def quote(
        self,
        *,
        connection: Connection | None,
        pool: ResourcePool,
        now: datetime,
        session: Session | None = None,
    ) -> PriceQuote:
        instance_type = self.instance_type
        if isinstance(pool.instance_config_json, dict):
            configured = pool.instance_config_json.get("instance_type")
            if isinstance(configured, str) and configured:
                instance_type = configured
        region = connection.region if connection is not None else self.region
        return PriceQuote(
            price_usd=self.price_usd,
            available=self.available,
            region=region or self.region,
            instance_type=instance_type,
            checked_at=now,
        )

    def create(
        self,
        *,
        session: Session,
        connection: Connection | None,
        pool: ResourcePool,
        spec: InstanceSpec,
        now: datetime,
    ) -> ProvisionedInstance:
        if not self.available:
            raise ProvisionerError(
                "inventory_unavailable",
                f"no {spec.instance_type} capacity in {self.region}",
            )
        if self.create_error:
            raise ProvisionerError(_error_code(self.create_error, "create_failed"))
        instance = ProvisionedInstance(
            tenant_id=pool.tenant_id,
            resource_pool_id=pool.id,
            provider_instance_id=f"fake-instance-{next(_INSTANCE_COUNTER)}",
            status="requested",
            spot_strategy="spot" if spec.spot else None,
            cost_json={},
        )
        session.add(instance)
        session.flush()
        return instance

    def release(
        self,
        *,
        session: Session,
        instance: ProvisionedInstance,
        now: datetime,
    ) -> None:
        if self.release_error:
            raise ProvisionerError(_error_code(self.release_error, "release_failed"))
        instance.status = "released"
        instance.released_at = now
        session.flush()

    def sync(
        self,
        *,
        session: Session,
        connection: Connection | None,
        pool: ResourcePool,
        now: datetime,
    ) -> dict[str, int]:
        if self.sync_error:
            raise ProvisionerError(_error_code(self.sync_error, "provider_sync_failed"))
        return dict(self.sync_counts)

    def health(self, *, instance: ProvisionedInstance) -> str:
        return self.health_status
