"""reusable evaluation templates and launch provenance

Revision ID: 0032_eval_template
Revises: 0031_failure_detail
Create Date: 2026-11-22

M10 (#378): add the project-level ``eval_template`` row that fixes a launch
selection (dataset version, Agent runtime/assets, model credential, resource
pool, case subset and execution policy), plus the run-side provenance columns
that keep a launched evaluation tied to the template version and launch
snapshot it came from. References are plain ids: validation happens on save
and again on every launch so a deleted or disabled reference is rejected with
an explicit error instead of silently changing the run.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0032_eval_template"
down_revision: str | None = "0031_failure_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "eval_template",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.String(length=1000), nullable=True),
        sa.Column("dataset_version_id", sa.String(length=36), nullable=False),
        sa.Column(
            "runtime_key", sa.String(length=64), server_default="claude-code", nullable=False
        ),
        sa.Column("runtime_version", sa.String(length=64), nullable=True),
        sa.Column("asset_source_id", sa.String(length=36), nullable=True),
        sa.Column("asset_ref", sa.String(length=200), nullable=True),
        sa.Column("asset_paths_json", _JSON, nullable=False),
        sa.Column("asset_types_json", _JSON, nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=True),
        sa.Column("resource_pool_id", sa.String(length=36), nullable=False),
        sa.Column("case_selection_json", _JSON, nullable=False),
        sa.Column("execution_json", _JSON, nullable=False),
        sa.Column("prompt_suffix", sa.Text(), server_default="", nullable=False),
        sa.Column("max_turns", sa.Integer(), nullable=True),
        sa.Column("agent_timeout_seconds", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("launch_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_launched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_eval_template_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_eval_template_tenant_id_id"),
    )
    op.create_index(
        "ix_eval_template_tenant_enabled_launched",
        "eval_template",
        ["tenant_id", "enabled", "last_launched_at"],
    )
    op.create_index(
        "ix_eval_template_tenant_created", "eval_template", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_eval_template_dataset_version_id", "eval_template", ["dataset_version_id"]
    )
    op.create_index(
        "ix_eval_template_model_config_id", "eval_template", ["model_config_id"]
    )
    op.create_index(
        "ix_eval_template_resource_pool_id", "eval_template", ["resource_pool_id"]
    )
    op.create_index(
        "ix_eval_template_asset_source_id", "eval_template", ["asset_source_id"]
    )
    op.create_index("ix_eval_template_project_id", "eval_template", ["project_id"])
    op.create_index("ix_eval_template_tenant_id", "eval_template", ["tenant_id"])

    op.add_column("eval_run", sa.Column("template_id", sa.String(length=36), nullable=True))
    op.add_column("eval_run", sa.Column("template_version", sa.Integer(), nullable=True))
    op.add_column(
        "eval_run",
        sa.Column(
            "template_snapshot_json",
            _JSON,
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_index("ix_eval_run_template_id", "eval_run", ["template_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_run_template_id", table_name="eval_run")
    op.drop_column("eval_run", "template_snapshot_json")
    op.drop_column("eval_run", "template_version")
    op.drop_column("eval_run", "template_id")
    op.drop_index("ix_eval_template_tenant_id", table_name="eval_template")
    op.drop_index("ix_eval_template_project_id", table_name="eval_template")
    op.drop_index("ix_eval_template_asset_source_id", table_name="eval_template")
    op.drop_index("ix_eval_template_resource_pool_id", table_name="eval_template")
    op.drop_index("ix_eval_template_model_config_id", table_name="eval_template")
    op.drop_index("ix_eval_template_dataset_version_id", table_name="eval_template")
    op.drop_index("ix_eval_template_tenant_created", table_name="eval_template")
    op.drop_index("ix_eval_template_tenant_enabled_launched", table_name="eval_template")
    op.drop_table("eval_template")
