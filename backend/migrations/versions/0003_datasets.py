"""dataset registry and import metadata

Revision ID: 0003_datasets
Revises: 0002_deployment
Create Date: 2026-11-08
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_datasets"
down_revision: str | None = "0002_deployment"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "dataset",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("task_family", sa.String(length=64), nullable=False),
        sa.Column("environment_kind", sa.String(length=64), nullable=False),
        sa.Column("metadata_json", JSON_TYPE, nullable=False),
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
            name=op.f("fk_dataset_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_dataset_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset")),
        sa.UniqueConstraint("tenant_id", "project_id", "slug", name="uq_dataset_project_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_tenant_id_id"),
    )
    op.create_index(op.f("ix_dataset_tenant_id"), "dataset", ["tenant_id"], unique=False)
    op.create_index("ix_dataset_tenant_created", "dataset", ["tenant_id", "created_at"], unique=False)
    op.create_index(op.f("ix_dataset_project_id"), "dataset", ["project_id"], unique=False)

    op.create_table(
        "dataset_version",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("source_uri", sa.String(length=1000), nullable=False),
        sa.Column("source_revision", sa.String(length=200), nullable=False),
        sa.Column("adapter_version", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="importing", nullable=False),
        sa.Column("case_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("task_manifest_key", sa.String(length=500), nullable=True),
        sa.Column("task_manifest_digest", sa.String(length=128), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('importing','ready','failed','archived')",
            name=op.f("ck_dataset_version_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_dataset_version_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_version_dataset_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_version")),
        sa.UniqueConstraint(
            "tenant_id",
            "dataset_id",
            "source_revision",
            "adapter_version",
            name="uq_dataset_version_source",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_version_tenant_id_id"),
    )
    op.create_index(
        op.f("ix_dataset_version_tenant_id"), "dataset_version", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_dataset_version_dataset_id"), "dataset_version", ["dataset_id"], unique=False
    )
    op.create_index(
        "ix_dataset_version_status", "dataset_version", ["dataset_id", "status"], unique=False
    )

    op.create_table(
        "metric_definition",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("aggregator_key", sa.String(length=64), nullable=False),
        sa.Column("formula_text", sa.String(length=2000), nullable=False),
        sa.Column("display_kind", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("denominator_policy", sa.String(length=64), nullable=False),
        sa.Column("config_json", JSON_TYPE, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_metric_definition_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_metric_definition_dataset_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_definition")),
        sa.UniqueConstraint("tenant_id", "id", name="uq_metric_definition_tenant_id_id"),
        sa.UniqueConstraint(
            "dataset_id", "metric_key", "version", name="uq_metric_definition_key_version"
        ),
    )
    op.create_index(
        op.f("ix_metric_definition_tenant_id"), "metric_definition", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_metric_definition_dataset_id"), "metric_definition", ["dataset_id"], unique=False
    )

    op.create_table(
        "dataset_case",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=36), nullable=False),
        sa.Column("case_key", sa.String(length=200), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("repo", sa.String(length=200), nullable=True),
        sa.Column("base_commit", sa.String(length=64), nullable=True),
        sa.Column("problem_statement", sa.Text(), nullable=True),
        sa.Column("reference_patch_key", sa.String(length=500), nullable=True),
        sa.Column("test_patch_key", sa.String(length=500), nullable=True),
        sa.Column("expected_metrics_json", JSON_TYPE, nullable=False),
        sa.Column("extra_json", JSON_TYPE, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_dataset_case_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_case_version_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_case")),
        sa.UniqueConstraint(
            "dataset_version_id", "case_key", name="uq_dataset_case_version_case_key"
        ),
    )
    op.create_index(op.f("ix_dataset_case_tenant_id"), "dataset_case", ["tenant_id"], unique=False)
    op.create_index(
        op.f("ix_dataset_case_dataset_version_id"),
        "dataset_case",
        ["dataset_version_id"],
        unique=False,
    )
    op.create_index(
        "ix_dataset_case_version_ordinal",
        "dataset_case",
        ["dataset_version_id", "ordinal"],
        unique=False,
    )

    op.create_table(
        "dataset_import",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=36), nullable=True),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("source_config_json", JSON_TYPE, nullable=False),
        sa.Column("total_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("valid_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("invalid_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_report_key", sa.String(length=500), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("created_by", sa.String(length=128), nullable=False),
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
        sa.CheckConstraint(
            "status IN ('pending','running','completed','failed')",
            name=op.f("ck_dataset_import_status"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_dataset_import_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_import_dataset_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_import")),
    )
    op.create_index(
        op.f("ix_dataset_import_tenant_id"), "dataset_import", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_dataset_import_dataset_id"), "dataset_import", ["dataset_id"], unique=False
    )
    op.create_index(
        "ix_dataset_import_status", "dataset_import", ["status", "created_at"], unique=False
    )

    op.create_table(
        "dataset_version_metric",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_dataset_version_metric_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_version_metric_version_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_definition_id"],
            ["metric_definition.tenant_id", "metric_definition.id"],
            name="fk_dataset_version_metric_definition_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_version_metric")),
        sa.UniqueConstraint(
            "dataset_version_id",
            "metric_definition_id",
            name="uq_dataset_version_metric_pair",
        ),
    )
    op.create_index(
        op.f("ix_dataset_version_metric_tenant_id"),
        "dataset_version_metric",
        ["tenant_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dataset_version_metric_dataset_version_id"),
        "dataset_version_metric",
        ["dataset_version_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_dataset_version_metric_dataset_version_id"),
        table_name="dataset_version_metric",
    )
    op.drop_index(op.f("ix_dataset_version_metric_tenant_id"), table_name="dataset_version_metric")
    op.drop_table("dataset_version_metric")
    op.drop_index("ix_dataset_import_status", table_name="dataset_import")
    op.drop_index(op.f("ix_dataset_import_dataset_id"), table_name="dataset_import")
    op.drop_index(op.f("ix_dataset_import_tenant_id"), table_name="dataset_import")
    op.drop_table("dataset_import")
    op.drop_index("ix_dataset_case_version_ordinal", table_name="dataset_case")
    op.drop_index(op.f("ix_dataset_case_dataset_version_id"), table_name="dataset_case")
    op.drop_index(op.f("ix_dataset_case_tenant_id"), table_name="dataset_case")
    op.drop_table("dataset_case")
    op.drop_index(op.f("ix_metric_definition_dataset_id"), table_name="metric_definition")
    op.drop_index(op.f("ix_metric_definition_tenant_id"), table_name="metric_definition")
    op.drop_table("metric_definition")
    op.drop_index("ix_dataset_version_status", table_name="dataset_version")
    op.drop_index(op.f("ix_dataset_version_dataset_id"), table_name="dataset_version")
    op.drop_index(op.f("ix_dataset_version_tenant_id"), table_name="dataset_version")
    op.drop_table("dataset_version")
    op.drop_index(op.f("ix_dataset_project_id"), table_name="dataset")
    op.drop_index("ix_dataset_tenant_created", table_name="dataset")
    op.drop_index(op.f("ix_dataset_tenant_id"), table_name="dataset")
    op.drop_table("dataset")
