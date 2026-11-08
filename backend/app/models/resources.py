from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, json_type, new_id, tenant_fk


class ResourcePool(Base, TimestampMixin):
    __tablename__ = "resource_pool"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    provider_key: Mapped[str] = mapped_column(String(64), nullable=False)
    runner_group: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
