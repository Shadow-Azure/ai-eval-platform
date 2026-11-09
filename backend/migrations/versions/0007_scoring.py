"""score artifacts, per-test results and metric snapshots

Revision ID: 0007_scoring
Revises: 0006_model_gateway
Create Date: 2026-11-09
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_scoring"
down_revision: str | None = "0006_model_gateway"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    # M3-04 real evaluation workbench: run-level dataset/runtime/assets and a
    # per-case frozen task view. Synthetic M2 runs leave these NULL/default.
    op.add_column("eval_run", sa.Column("dataset_version_id", sa.String(length=36), nullable=True))
    op.add_column("eval_run", sa.Column("runtime_key", sa.String(length=64), nullable=True))
    op.add_column("eval_run", sa.Column("runtime_version", sa.String(length=64), nullable=True))
    op.add_column("eval_run", sa.Column("execution_image_digest", sa.String(length=128), nullable=True))
    op.add_column("eval_run", sa.Column("asset_snapshot_json", JSON_TYPE, server_default=sa.text("'{}'"), nullable=False))
    op.add_column("eval_run", sa.Column("consistency_json", JSON_TYPE, server_default=sa.text("'{}'"), nullable=False))
    op.create_index(op.f("ix_eval_run_dataset_version_id"), "eval_run", ["dataset_version_id"], unique=False)
    op.add_column("eval_case_run", sa.Column("task_json", JSON_TYPE, server_default=sa.text("'{}'"), nullable=False))

    op.create_table(
        "score_artifact",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("eval_case_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("object_key", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=128), nullable=False),
        sa.Column("size", sa.Integer(), server_default="0", nullable=False),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("parsed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("parse_error", sa.String(length=1000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_score_artifact_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_score_artifact_attempt_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_score_artifact")),
        sa.UniqueConstraint(
            "eval_case_attempt_id",
            "sha256",
            "parser_version",
            name="uq_score_artifact_attempt_digest_parser",
        ),
    )
    op.create_index(
        op.f("ix_score_artifact_tenant_id"), "score_artifact", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_score_artifact_eval_case_attempt_id"),
        "score_artifact",
        ["eval_case_attempt_id"],
        unique=False,
    )

    op.create_table(
        "test_case_result",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("eval_case_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("suite_name", sa.String(length=200), nullable=False),
        sa.Column("test_name", sa.String(length=500), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("native_status", sa.String(length=64), server_default="", nullable=False),
        sa.Column("test_group", sa.String(length=32), server_default="OTHER", nullable=False),
        sa.Column("expected", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("file_path", sa.String(length=500), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=True),
        sa.Column("failure_message", sa.Text(), nullable=True),
        sa.Column("log_object_key", sa.String(length=500), nullable=True),
        sa.Column("log_range_json", JSON_TYPE, nullable=False),
        sa.Column("extra_json", JSON_TYPE, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "outcome IN ('passed','failed','skipped','error')",
            name=op.f("ck_test_case_result_outcome"),
        ),
        sa.CheckConstraint(
            "test_group IN ('F2P','P2P','OTHER')",
            name=op.f("ck_test_case_result_test_group"),
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenant.id"],
            name=op.f("fk_test_case_result_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_test_case_result_attempt_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_test_case_result")),
        sa.UniqueConstraint(
            "eval_case_attempt_id",
            "suite_name",
            "test_name",
            "test_group",
            name="uq_test_case_result_attempt_test",
        ),
    )
    op.create_index(
        op.f("ix_test_case_result_tenant_id"), "test_case_result", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_test_case_result_eval_case_attempt_id"),
        "test_case_result",
        ["eval_case_attempt_id"],
        unique=False,
    )
    op.create_index(
        "ix_test_case_result_attempt_outcome",
        "test_case_result",
        ["eval_case_attempt_id", "outcome"],
        unique=False,
    )

    op.create_table(
        "metric_result",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("eval_run_id", sa.String(length=36), nullable=False),
        sa.Column("metric_definition_id", sa.String(length=36), nullable=False),
        sa.Column("metric_key", sa.String(length=64), nullable=False),
        sa.Column("metric_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("value_json", JSON_TYPE, nullable=False),
        sa.Column("numerator", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("denominator", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("is_final", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column(
            "generated_at",
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
            name=op.f("fk_metric_result_tenant_id_tenant"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_metric_result_run_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "metric_definition_id"],
            ["metric_definition.tenant_id", "metric_definition.id"],
            name="fk_metric_result_definition_tenant",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_metric_result")),
        sa.UniqueConstraint(
            "eval_run_id",
            "metric_definition_id",
            "is_final",
            name="uq_metric_result_run_definition_final",
        ),
    )
    op.create_index(
        op.f("ix_metric_result_tenant_id"), "metric_result", ["tenant_id"], unique=False
    )
    op.create_index(
        op.f("ix_metric_result_eval_run_id"), "metric_result", ["eval_run_id"], unique=False
    )
    op.create_index(
        "ix_metric_result_run_final", "metric_result", ["eval_run_id", "is_final"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_metric_result_run_final", table_name="metric_result")
    op.drop_index(op.f("ix_metric_result_eval_run_id"), table_name="metric_result")
    op.drop_index(op.f("ix_metric_result_tenant_id"), table_name="metric_result")
    op.drop_table("metric_result")
    op.drop_index("ix_test_case_result_attempt_outcome", table_name="test_case_result")
    op.drop_index(
        op.f("ix_test_case_result_eval_case_attempt_id"), table_name="test_case_result"
    )
    op.drop_index(op.f("ix_test_case_result_tenant_id"), table_name="test_case_result")
    op.drop_table("test_case_result")
    op.drop_index(
        op.f("ix_score_artifact_eval_case_attempt_id"), table_name="score_artifact"
    )
    op.drop_index(op.f("ix_score_artifact_tenant_id"), table_name="score_artifact")
    op.drop_table("score_artifact")
