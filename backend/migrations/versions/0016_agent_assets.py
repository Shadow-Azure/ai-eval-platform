"""agent asset sources, immutable snapshots and snapshot assets

Revision ID: 0016_agent_assets
Revises: 0015_dataset_image_digests

M5-01 (design 05 §5): a Git source stores repo/ref/scan paths; resolving a
selection freezes the commit and file digests into an immutable snapshot whose
bundle lives in OSS. Eval runs reference the snapshot, so later branch moves
or deletions cannot change a launched evaluation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0016_agent_assets"
down_revision = "0015_dataset_image_digests"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_asset_source",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("repo_url", sa.String(length=500), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False, server_default="github"),
        sa.Column("default_ref", sa.String(length=200), nullable=False),
        sa.Column("scan_paths_json", sa.JSON(), nullable=False),
        sa.Column("selection_mode", sa.String(length=16), nullable=False, server_default="directory"),
        sa.Column("runtime_prerequisites_json", sa.JSON(), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("last_scanned_commit", sa.String(length=64), nullable=True),
        sa.Column("last_scan_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False, server_default="unverified"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_agent_asset_source_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["credential.tenant_id", "credential.id"],
            name="fk_agent_asset_source_credential_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_asset_source_tenant_id_id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_agent_asset_source_tenant_name"),
        sa.CheckConstraint(
            "status IN ('unverified','ready','requires_setup','error')", name="status"
        ),
        sa.CheckConstraint("selection_mode IN ('directory','explicit')", name="selection_mode"),
    )
    op.create_index(
        "ix_agent_asset_source_tenant_project",
        "agent_asset_source",
        ["tenant_id", "project_id"],
    )

    op.create_table(
        "agent_asset_snapshot",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("source_id", sa.String(length=36), nullable=False),
        sa.Column("requested_ref", sa.String(length=200), nullable=False),
        sa.Column("resolved_commit", sa.String(length=64), nullable=False),
        sa.Column("selection_mode", sa.String(length=16), nullable=False),
        sa.Column("scan_paths_json", sa.JSON(), nullable=False),
        sa.Column("selection_json", sa.JSON(), nullable=False),
        sa.Column("selection_digest", sa.String(length=80), nullable=False),
        sa.Column("tree_digest", sa.String(length=80), nullable=False),
        sa.Column("bundle_key", sa.String(length=500), nullable=False),
        sa.Column("bundle_digest", sa.String(length=80), nullable=False),
        sa.Column("bundle_size", sa.Integer(), nullable=False),
        sa.Column("file_count", sa.Integer(), nullable=False),
        sa.Column("asset_count", sa.Integer(), nullable=False),
        sa.Column("runtime_key", sa.String(length=64), nullable=False),
        sa.Column("manifest_json", sa.JSON(), nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "source_id"],
            ["agent_asset_source.tenant_id", "agent_asset_source.id"],
            name="fk_agent_asset_snapshot_source_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_asset_snapshot_tenant_id_id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_id",
            "selection_digest",
            name="uq_agent_asset_snapshot_selection",
        ),
        sa.CheckConstraint("selection_mode IN ('directory','explicit')", name="selection_mode"),
    )
    op.create_index(
        "ix_agent_asset_snapshot_source_created",
        "agent_asset_snapshot",
        ["source_id", "created_at"],
    )

    op.create_table(
        "agent_asset",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("snapshot_id", sa.String(length=36), nullable=False),
        sa.Column("asset_type", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("path", sa.String(length=1000), nullable=False),
        sa.Column("content_digest", sa.String(length=80), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "snapshot_id"],
            ["agent_asset_snapshot.tenant_id", "agent_asset_snapshot.id"],
            name="fk_agent_asset_snapshot_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_agent_asset_tenant_id_id"),
        sa.UniqueConstraint(
            "snapshot_id", "asset_type", "path", name="uq_agent_asset_snapshot_type_path"
        ),
        sa.CheckConstraint(
            "asset_type IN ('skill','command','subagent')", name="asset_type"
        ),
    )
    op.create_index("ix_agent_asset_snapshot_ordinal", "agent_asset", ["snapshot_id", "ordinal"])

    with op.batch_alter_table("eval_run") as batch_op:
        batch_op.add_column(sa.Column("asset_snapshot_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_eval_run_asset_snapshot_tenant",
            "agent_asset_snapshot",
            ["tenant_id", "asset_snapshot_id"],
            ["tenant_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_eval_run_asset_snapshot_id", ["asset_snapshot_id"])


def downgrade() -> None:
    with op.batch_alter_table("eval_run") as batch_op:
        batch_op.drop_index("ix_eval_run_asset_snapshot_id")
        batch_op.drop_constraint("fk_eval_run_asset_snapshot_tenant", type_="foreignkey")
        batch_op.drop_column("asset_snapshot_id")
    op.drop_index("ix_agent_asset_snapshot_ordinal", table_name="agent_asset")
    op.drop_table("agent_asset")
    op.drop_index("ix_agent_asset_snapshot_source_created", table_name="agent_asset_snapshot")
    op.drop_table("agent_asset_snapshot")
    op.drop_index("ix_agent_asset_source_tenant_project", table_name="agent_asset_source")
    op.drop_table("agent_asset_source")
