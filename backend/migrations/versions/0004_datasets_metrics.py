"""datasets + metric registry + imports

Revision ID: 0004_datasets_metrics
Revises: 0003_outbox_cancelled
Create Date: 2026-11-09

M3-01: dataset / dataset_version / dataset_case / dataset_import /
metric_definition / dataset_version_metric（design 05 §4）。版本与用例是
不可变任务视图（只有 created_at）；metric_definition.aggregator_key 带
平台白名单 CHECK（决策 D10）。

注意：`deployment` 表是 M2 手写迁移建的、没有对应 ORM 模型，autogenerate
会把它误判为 removed；本迁移手工剔除该噪声，不触碰 deployment。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_datasets_metrics"
down_revision: str | None = "0003_outbox_cancelled"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSON = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def _create_indexes(table_name: str, *index_specs: tuple[str, list[str]]) -> None:
    for index_name, columns in index_specs:
        op.create_index(index_name, table_name, columns, unique=False)


def upgrade() -> None:
    op.create_table(
        "dataset",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("slug", sa.String(length=128), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("task_family", sa.String(length=64), nullable=True),
        sa.Column("environment_kind", sa.String(length=64), nullable=True),
        sa.Column("metadata_json", _JSON, nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_dataset_tenant_id_tenant"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_dataset_project_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset")),
        sa.UniqueConstraint("project_id", "slug", name="uq_dataset_project_slug"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_tenant_id_id"),
    )
    _create_indexes(
        "dataset",
        (op.f("ix_dataset_project_id"), ["project_id"]),
        (op.f("ix_dataset_tenant_id"), ["tenant_id"]),
        ("ix_dataset_tenant_adapter", ["tenant_id", "adapter_key"]),
    )

    op.create_table(
        "dataset_version",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=500), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=False),
        sa.Column("adapter_version", sa.String(length=32), nullable=False),
        sa.Column("case_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="active", nullable=False),
        sa.Column("task_manifest_key", sa.String(length=500), nullable=True),
        sa.Column("task_manifest_digest", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("status IN ('active','archived')", name=op.f("ck_dataset_version_status")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_dataset_version_tenant_id_tenant"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_version_dataset_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_version")),
        sa.UniqueConstraint("dataset_id", "version", name="uq_dataset_version_dataset_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_version_tenant_id_id"),
    )
    _create_indexes(
        "dataset_version",
        (op.f("ix_dataset_version_dataset_id"), ["dataset_id"]),
        (op.f("ix_dataset_version_tenant_id"), ["tenant_id"]),
        ("ix_dataset_version_tenant_dataset", ["tenant_id", "dataset_id"]),
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
        sa.Column("problem_statement", sa.Text(), nullable=False),
        sa.Column("reference_patch", sa.Text(), nullable=True),
        sa.Column("test_patch", sa.Text(), nullable=True),
        sa.Column("expected_metrics_json", _JSON, nullable=False),
        sa.Column("extra_json", _JSON, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_dataset_case_tenant_id_tenant"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_case_dataset_version_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_case")),
        sa.UniqueConstraint("dataset_version_id", "case_key", name="uq_dataset_case_version_case_key"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_case_tenant_id_id"),
    )
    _create_indexes(
        "dataset_case",
        (op.f("ix_dataset_case_dataset_version_id"), ["dataset_version_id"]),
        (op.f("ix_dataset_case_tenant_id"), ["tenant_id"]),
        ("ix_dataset_case_version_ordinal", ["dataset_version_id", "ordinal"]),
    )

    op.create_table(
        "dataset_import",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=True),
        sa.Column("dataset_slug", sa.String(length=128), nullable=False),
        sa.Column("adapter_key", sa.String(length=64), nullable=False),
        sa.Column("version", sa.String(length=64), nullable=False),
        sa.Column("source_uri", sa.String(length=500), nullable=False),
        sa.Column("source_revision", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="failed", nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("valid_count", sa.Integer(), nullable=False),
        sa.Column("invalid_count", sa.Integer(), nullable=False),
        sa.Column("error_report_json", _JSON, nullable=False),
        sa.Column("created_by", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint("status IN ('succeeded','failed','duplicate')", name=op.f("ck_dataset_import_status")),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_dataset_import_tenant_id_tenant"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_dataset_import_dataset_tenant",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_import")),
    )
    _create_indexes(
        "dataset_import",
        (op.f("ix_dataset_import_dataset_id"), ["dataset_id"]),
        (op.f("ix_dataset_import_tenant_id"), ["tenant_id"]),
        ("ix_dataset_import_tenant_created", ["tenant_id", "created_at"]),
    )

    op.create_table(
        "metric_definition",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_id", sa.String(length=36), nullable=False),
        sa.Column("metric_key", sa.String(length=128), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("aggregator_key", sa.String(length=64), nullable=False),
        sa.Column("formula_text", sa.Text(), nullable=True),
        sa.Column("display_kind", sa.String(length=32), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("direction", sa.String(length=32), server_default="higher_is_better", nullable=False),
        sa.Column("denominator_policy", sa.String(length=64), nullable=True),
        sa.Column("config_json", _JSON, nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.CheckConstraint(
            "aggregator_key IN ('ratio','sum','mean','percentile','histogram')",
            name=op.f("ck_metric_definition_aggregator_key"),
        ),
        sa.CheckConstraint(
            "display_kind IN ('number','percentage','histogram','ms','usd')",
            name=op.f("ck_metric_definition_display_kind"),
        ),
        sa.CheckConstraint(
            "direction IN ('higher_is_better','lower_is_better')", name=op.f("ck_metric_definition_direction")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_metric_definition_tenant_id_tenant"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_id"],
            ["dataset.tenant_id", "dataset.id"],
            name="fk_metric_definition_dataset_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_definition")),
        sa.UniqueConstraint("dataset_id", "metric_key", "version", name="uq_metric_definition_key_version"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_metric_definition_tenant_id_id"),
    )
    _create_indexes(
        "metric_definition",
        (op.f("ix_metric_definition_dataset_id"), ["dataset_id"]),
        (op.f("ix_metric_definition_tenant_id"), ["tenant_id"]),
        ("ix_metric_definition_tenant_key", ["tenant_id", "metric_key"]),
    )

    op.create_table(
        "dataset_version_metric",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("dataset_version_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP"), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenant.id"], name=op.f("fk_dataset_version_metric_tenant_id_tenant"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "dataset_version_id"],
            ["dataset_version.tenant_id", "dataset_version.id"],
            name="fk_dataset_version_metric_version_tenant",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_definition_id"],
            ["metric_definition.tenant_id", "metric_definition.id"],
            name="fk_dataset_version_metric_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dataset_version_metric")),
        sa.UniqueConstraint("dataset_version_id", "metric_definition_id", name="uq_dataset_version_metric_pair"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_dataset_version_metric_tenant_id_id"),
    )
    _create_indexes(
        "dataset_version_metric",
        (op.f("ix_dataset_version_metric_dataset_version_id"), ["dataset_version_id"]),
        (op.f("ix_dataset_version_metric_metric_definition_id"), ["metric_definition_id"]),
        (op.f("ix_dataset_version_metric_tenant_id"), ["tenant_id"]),
    )


def downgrade() -> None:
    op.drop_table("dataset_version_metric")
    op.drop_table("metric_definition")
    op.drop_table("dataset_import")
    op.drop_table("dataset_case")
    op.drop_table("dataset_version")
    op.drop_table("dataset")
