from __future__ import annotations

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, CreatedAtMixin, TimestampMixin, new_id, tenant_fk


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
