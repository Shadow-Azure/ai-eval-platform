from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, TimestampMixin, json_type, new_id, tenant_fk

CREDENTIAL_KINDS = ("model", "resource")
USAGE_STATUSES = ("succeeded", "failed", "budget_paused")


class Credential(Base, TimestampMixin):
    __tablename__ = "credential"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="model")
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    key_version: Mapped[str] = mapped_column(String(64), nullable=False)
    secret_mask: Mapped[str] = mapped_column(String(64), nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_credential_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_credential_project_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_credential_tenant_project", "tenant_id", "project_id"),
        CheckConstraint("kind IN ('model','resource')", name="kind"),
    )


class ModelConfig(Base, TimestampMixin):
    __tablename__ = "model_config"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    base_url: Mapped[str] = mapped_column(String(500), nullable=False)
    model_name: Mapped[str] = mapped_column(String(200), nullable=False)
    api_style: Mapped[str] = mapped_column(String(32), nullable=False, default="openai")
    limits_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    pricing_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    is_temporary: Mapped[bool] = mapped_column(Boolean(), nullable=False, default=False)
    owner_eval_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_model_config_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_model_config_project_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["credential.tenant_id", "credential.id"],
            name="fk_model_config_credential_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_model_config_tenant_project", "tenant_id", "project_id"),
    )


class ModelUsage(Base, CreatedAtMixin):
    __tablename__ = "model_usage"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    model_config_id: Mapped[str] = mapped_column(String(36), nullable=False)
    eval_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    shard_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    case_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    attempt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="succeeded")
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cny: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, default=Decimal("0")
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["tenant_id", "model_config_id"],
            ["model_config.tenant_id", "model_config.id"],
            name="fk_model_usage_model_config_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_model_usage_run_created", "eval_run_id", "created_at"),
        Index("ix_model_usage_run_shard", "eval_run_id", "shard_id"),
        Index("ix_model_usage_config_created", "model_config_id", "created_at"),
        CheckConstraint("status IN ('succeeded','failed','budget_paused')", name="status"),
    )
