"""model credentials, configuration and usage

Revision ID: 0004_model_gateway
Revises: 0003_datasets
Create Date: 2026-11-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_model_gateway"
down_revision: str | None = "0003_datasets"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "credential",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), server_default="model", nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("key_version", sa.String(length=64), nullable=False),
        sa.Column("secret_mask", sa.String(length=64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('model','resource')", name=op.f("ck_credential_kind")),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_credential_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_credential_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_credential")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_credential_tenant_id_id"),
    )
    op.create_index(op.f("ix_credential_tenant_id"), "credential", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_credential_project_id"), "credential", ["project_id"], unique=False)
    op.create_index(
        "ix_credential_tenant_project", "credential", ["tenant_id", "project_id"], unique=False
    )

    op.create_table(
        "model_config",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("credential_id", sa.String(length=36), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("model_name", sa.String(length=200), nullable=False),
        sa.Column("api_style", sa.String(length=32), server_default="openai", nullable=False),
        sa.Column("limits_json", JSON_TYPE, nullable=False),
        sa.Column("pricing_json", JSON_TYPE, nullable=False),
        sa.Column("is_temporary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("owner_eval_run_id", sa.String(length=36), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_model_config_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_model_config_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "credential_id"],
            ["credential.tenant_id", "credential.id"],
            name="fk_model_config_credential_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_config")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_model_config_tenant_id_id"),
    )
    op.create_index(op.f("ix_model_config_tenant_id"), "model_config", ["tenant_id"], unique=False)
    op.create_index(op.f("ix_model_config_project_id"), "model_config", ["project_id"], unique=False)
    op.create_index(
        "ix_model_config_tenant_project",
        "model_config",
        ["tenant_id", "project_id"],
        unique=False,
    )

    op.create_table(
        "model_usage",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("model_config_id", sa.String(length=36), nullable=False),
        sa.Column("eval_run_id", sa.String(length=36), nullable=True),
        sa.Column("case_run_id", sa.String(length=36), nullable=True),
        sa.Column("attempt", sa.Integer(), nullable=True),
        sa.Column("request_id", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="succeeded", nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("completion_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("total_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("cost_usd", sa.Numeric(precision=12, scale=6), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('succeeded','failed','budget_paused')",
            name=op.f("ck_model_usage_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_model_usage_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "model_config_id"],
            ["model_config.tenant_id", "model_config.id"],
            name="fk_model_usage_model_config_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_usage")),
    )
    op.create_index(op.f("ix_model_usage_tenant_id"), "model_usage", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_model_usage_eval_run_id"), "model_usage", ["eval_run_id"], unique=False
    )
    op.create_index(
        op.f("ix_model_usage_case_run_id"), "model_usage", ["case_run_id"], unique=False
    )
    op.create_index(
        "ix_model_usage_run_created", "model_usage", ["eval_run_id", "created_at"], unique=False
    )
    op.create_index(
        "ix_model_usage_config_created",
        "model_usage",
        ["model_config_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_model_usage_config_created", table_name="model_usage")
    op.drop_index("ix_model_usage_run_created", table_name="model_usage")
    op.drop_index(op.f("ix_model_usage_case_run_id"), table_name="model_usage")
    op.drop_index(op.f("ix_model_usage_eval_run_id"), table_name="model_usage")
    op.drop_index(op.f("ix_model_usage_tenant_id"), table_name="model_usage")
    op.drop_table("model_usage")
    op.drop_index("ix_model_config_tenant_project", table_name="model_config")
    op.drop_index(op.f("ix_model_config_project_id"), table_name="model_config")
    op.drop_index(op.f("ix_model_config_tenant_id"), table_name="model_config")
    op.drop_table("model_config")
    op.drop_index("ix_credential_tenant_project", table_name="credential")
    op.drop_index(op.f("ix_credential_project_id"), table_name="credential")
    op.drop_index(op.f("ix_credential_tenant_id"), table_name="credential")
    op.drop_table("credential")
