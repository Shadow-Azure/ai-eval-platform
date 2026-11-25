from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, TimestampMixin, json_type, new_id, tenant_fk


class Tenant(Base, CreatedAtMixin):
    __tablename__ = "tenant"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="active", server_default="active"
    )

    __table_args__ = (CheckConstraint("status IN ('active','suspended')", name="status"),)


class Workspace(Base, TimestampMixin):
    __tablename__ = "workspace"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "slug", name="uq_workspace_tenant_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_workspace_tenant_id_id"),
    )


class Project(Base, TimestampMixin):
    __tablename__ = "project"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    __table_args__ = (
        UniqueConstraint("workspace_id", "slug", name="uq_project_workspace_slug"),
        UniqueConstraint("tenant_id", "id", name="uq_project_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspace.tenant_id", "workspace.id"],
            name="fk_project_workspace_tenant",
            ondelete="RESTRICT",
        ),
    )


class User(Base, CreatedAtMixin):
    """Console account. MVP simplification: email is globally unique."""

    __tablename__ = "user"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(512), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )


class Membership(Base, TimestampMixin):
    """Tenant/workspace membership with the MVP admin/viewer capability split."""

    __tablename__ = "membership"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    workspace_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    role: Mapped[str] = mapped_column(
        String(32), nullable=False, default="viewer", server_default="viewer"
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),
        ForeignKeyConstraint(
            ["tenant_id", "workspace_id"],
            ["workspace.tenant_id", "workspace.id"],
            name="fk_membership_workspace_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint("role IN ('admin','viewer')", name="role"),
    )


class AuthSession(Base, CreatedAtMixin):
    """Server-side session. Only the SHA-256 token hash is persisted."""

    __tablename__ = "auth_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    user_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        Index("ix_auth_session_user_expiry", "user_id", "expires_at"),
        Index("ix_auth_session_expires_at", "expires_at"),
    )


SERVICE_TOKEN_SCOPES = ("eval:launch", "eval:read")


class ServiceToken(Base, TimestampMixin):
    """M10 (#429): machine identity for template launches and result reads.

    Automation (harness-evolution) cannot hold a browser session, so template
    launch and the stable status/results reads also accept a bearer service
    token. Only the SHA-256 hex of the full plaintext is persisted; the
    plaintext is returned exactly once by the creation call. Scopes, an optional
    template allowlist and optional concurrency/budget caps are stored per token
    so the API can enforce the caller's authority without another lookup table.
    """

    __tablename__ = "service_token"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    scopes_json: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)
    # Empty means every template; otherwise the ids the token may launch.
    template_allowlist_json: Mapped[list] = mapped_column(
        json_type(), default=list, nullable=False
    )
    max_concurrent_runs: Mapped[int | None] = mapped_column(Integer, nullable=True)
    daily_budget_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_service_token_tenant_name"),
        UniqueConstraint("tenant_id", "id", name="uq_service_token_tenant_id_id"),
        Index(
            "ix_service_token_tenant_revoked_created",
            "tenant_id",
            "revoked_at",
            "created_at",
        ),
    )


class AuditLog(Base, CreatedAtMixin):
    """Minimal audit trail for identity events (login/logout/password reset)."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    actor_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(json_type(), nullable=False, default=dict)

    __table_args__ = (Index("ix_audit_log_tenant_created", "tenant_id", "created_at"),)
