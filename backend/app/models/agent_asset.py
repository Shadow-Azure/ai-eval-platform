"""Agent asset sources, immutable snapshots and snapshot assets (M5-01).

Design 05 §5. A Git source stores where assets live; resolving a selection
freezes the exact commit and file digests into an immutable snapshot. The
snapshot bundle lives in OSS and the eval run references the snapshot, so a
branch move or file deletion after launch cannot change a running evaluation.
"""

from __future__ import annotations

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, json_type, new_id, tenant_fk

ASSET_SOURCE_STATUSES = ("unverified", "ready", "requires_setup", "error")
ASSET_TYPES = ("skill", "command", "subagent")
SELECTION_MODES = ("directory", "explicit")


class AgentAssetSource(Base, TimestampMixin):
    __tablename__ = "agent_asset_source"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    repo_url: Mapped[str] = mapped_column(String(500), nullable=False)
    provider: Mapped[str] = mapped_column(
        String(32), nullable=False, default="github", server_default="github"
    )
    default_ref: Mapped[str] = mapped_column(String(200), nullable=False, default="main")
    scan_paths_json: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)
    selection_mode: Mapped[str] = mapped_column(
        String(16), nullable=False, default="directory", server_default="directory"
    )
    runtime_prerequisites_json: Mapped[dict] = mapped_column(
        json_type(), default=dict, nullable=False
    )
    # Secret storage reuses the encrypted credential table (kind=resource,
    # provider=github); the source only keeps the reference and mask.
    credential_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    last_scanned_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    last_scan_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="unverified", server_default="unverified"
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_asset_source_tenant_id_id"),
        UniqueConstraint("tenant_id", "name", name="uq_agent_asset_source_tenant_name"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_agent_asset_source_project_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["credential.tenant_id", "credential.id"],
            name="fk_agent_asset_source_credential_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "status IN ('unverified','ready','requires_setup','error')", name="status"
        ),
        CheckConstraint("selection_mode IN ('directory','explicit')", name="selection_mode"),
        Index("ix_agent_asset_source_tenant_project", "tenant_id", "project_id"),
    )


class AgentAssetSnapshot(Base, TimestampMixin):
    __tablename__ = "agent_asset_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    source_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    requested_ref: Mapped[str] = mapped_column(String(200), nullable=False)
    resolved_commit: Mapped[str] = mapped_column(String(64), nullable=False)
    selection_mode: Mapped[str] = mapped_column(String(16), nullable=False, default="explicit")
    scan_paths_json: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)
    selection_json: Mapped[list] = mapped_column(json_type(), default=list, nullable=False)
    # Stable identity of (resolved commit + selected asset paths/types); a
    # repeated resolve returns this row instead of writing a second bundle.
    selection_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    tree_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    bundle_key: Mapped[str] = mapped_column(String(500), nullable=False)
    bundle_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    bundle_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    file_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    asset_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    runtime_key: Mapped[str] = mapped_column(String(64), nullable=False, default="claude-code")
    manifest_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_asset_snapshot_tenant_id_id"),
        UniqueConstraint(
            "tenant_id",
            "source_id",
            "selection_digest",
            name="uq_agent_asset_snapshot_selection",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["agent_asset_source.tenant_id", "agent_asset_source.id"],
            name="fk_agent_asset_snapshot_source_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint("selection_mode IN ('directory','explicit')", name="selection_mode"),
        Index("ix_agent_asset_snapshot_source_created", "source_id", "created_at"),
    )


class AgentAsset(Base, TimestampMixin):
    __tablename__ = "agent_asset"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    path: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_agent_asset_tenant_id_id"),
        UniqueConstraint(
            "snapshot_id", "asset_type", "path", name="uq_agent_asset_snapshot_type_path"
        ),
        ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["agent_asset_snapshot.tenant_id", "agent_asset_snapshot.id"],
            name="fk_agent_asset_snapshot_tenant",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "asset_type IN ('skill','command','subagent')", name="asset_type"
        ),
        Index("ix_agent_asset_snapshot_ordinal", "snapshot_id", "ordinal"),
    )
