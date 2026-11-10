from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, TimestampMixin, json_type, new_id, tenant_fk

CONNECTION_STATUSES = ("unverified", "verified", "failed")
PROVISIONING_MODES = ("fixed", "spot")


class Connection(Base, TimestampMixin):
    """Tenant-level resource connection (design 05 §5).

    The secret itself lives in the encrypted ``credential`` row with
    ``kind='resource'``; this table only keeps provider, region, declared
    capabilities and the last verification result.
    """

    __tablename__ = "connection"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    region: Mapped[str | None] = mapped_column(String(64), nullable=True)
    credential_id: Mapped[str] = mapped_column(String(36), nullable=False)
    capabilities_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="unverified", server_default="unverified"
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_connection_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_connection_tenant_name"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_connection_project_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["credential.tenant_id", "credential.id"],
            name="fk_connection_credential_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint("status IN ('unverified','verified','failed')", name="status"),
    )


class ResourcePool(Base, TimestampMixin):
    __tablename__ = "resource_pool"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provisioning_mode: Mapped[str] = mapped_column(
        String(32), nullable=False, default="fixed", server_default="fixed"
    )
    min_capacity: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=0, server_default="0"
    )
    # A pre-M4-03 pool was effectively unbounded by the platform; 50 keeps the
    # design's example cap instead of silently limiting old pools to one run.
    max_capacity: Mapped[int] = mapped_column(
        Integer(), nullable=False, default=50, server_default="50"
    )
    instance_config_json: Mapped[dict] = mapped_column(
        json_type(), default=dict, nullable=False
    )
    price_threshold_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    price_usd: Mapped[Decimal | None] = mapped_column(Numeric(10, 4), nullable=True)
    capacity_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    labels_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_resource_pool_tenant_id_id"),
        UniqueConstraint("project_id", "name", name="uq_resource_pool_project_name"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_resource_pool_project_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["connection.tenant_id", "connection.id"],
            name="fk_resource_pool_connection_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "provisioning_mode IN ('fixed','spot')", name="provisioning_mode"
        ),
        CheckConstraint(
            "max_capacity >= 1 AND min_capacity >= 0 AND min_capacity <= max_capacity",
            name="capacity",
        ),
        CheckConstraint("status IN ('active','disabled')", name="status"),
    )


class Runner(Base, TimestampMixin):
    __tablename__ = "runner"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    resource_pool_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_runner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    labels_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="offline", server_default="offline"
    )
    busy_case_attempt_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    registered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deregistered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "resource_pool_id",
            "provider_runner_id",
            name="uq_runner_pool_provider_runner",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_runner_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "resource_pool_id"],
            ["resource_pool.tenant_id", "resource_pool.id"],
            name="fk_runner_resource_pool_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_runner_status_heartbeat", "status", "last_heartbeat_at"),
        CheckConstraint(
            "status IN ('online','offline','busy','draining')", name="status"
        ),
    )


class ProvisionedInstance(Base, TimestampMixin):
    __tablename__ = "provisioned_instance"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    resource_pool_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    provider_instance_id: Mapped[str] = mapped_column(String(128), nullable=False)
    runner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="requested")
    spot_strategy: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    released_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cost_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "resource_pool_id",
            "provider_instance_id",
            name="uq_provisioned_instance_pool_provider",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_provisioned_instance_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "resource_pool_id"],
            ["resource_pool.tenant_id", "resource_pool.id"],
            name="fk_provisioned_instance_pool_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "runner_id"],
            ["runner.tenant_id", "runner.id"],
            name="fk_provisioned_instance_runner_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('requested','creating','registering','online','draining','released','interrupted')",
            name="status",
        ),
    )


class ResourceEvent(Base, CreatedAtMixin):
    """Append-only resource event log (M4-03 B).

    Capacity decisions, provisioning failures and releases are recorded here
    so the console can explain *why* a pool is waiting. Events are scoped to
    one tenant and optionally to a pool/connection; ``dedupe_key`` makes a
    repeated worker tick with an unchanged state a no-op instead of spam
    (unique per tenant, NULL keys are always allowed).
    """

    __tablename__ = "resource_event"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    resource_pool_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    connection_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    dedupe_key: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "dedupe_key", name="uq_resource_event_tenant_dedupe"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resource_pool_id"],
            ["resource_pool.tenant_id", "resource_pool.id"],
            name="fk_resource_event_pool_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "connection_id"],
            ["connection.tenant_id", "connection.id"],
            name="fk_resource_event_connection_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_resource_event_pool_id_id", "resource_pool_id", "id"),
    )
