"""versioned trace files, sessions and spans

Revision ID: 0020_trace_ingestion
Revises: 0019_trace_capture_status
Create Date: 2026-11-11

M6-02 (design 08 §3/§5): native trace objects become an attempt-owned,
parser-versioned tree. ``trace_file`` keeps the original object provenance,
``trace_session`` records native/root/parent identities and relation evidence,
and ``trace_span`` stores ordered metadata plus a redacted preview.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020_trace_ingestion"
down_revision: str | None = "0019_trace_capture_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _json_type() -> sa.types.TypeEngine:
    """JSONB on PostgreSQL, portable JSON elsewhere (mirrors models.base)."""

    return sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "trace_file",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("eval_case_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("eval_case_run_id", sa.String(length=36), nullable=False),
        sa.Column("runtime_key", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("runtime_version", sa.String(length=64), nullable=True),
        sa.Column("native_format", sa.String(length=128), nullable=True),
        sa.Column("format_version", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("logical_path", sa.String(length=512), nullable=True),
        sa.Column("artifact_kind", sa.String(length=32), nullable=False),
        sa.Column("object_key", sa.String(length=1024), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("detected_format", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="pending"),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_summary", sa.String(length=1000), nullable=True),
        sa.Column("line_no", sa.Integer(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_owner", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_trace_file_attempt_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_case_run_id"],
            ["eval_case_run.tenant_id", "eval_case_run.id"],
            name="fk_trace_file_case_run_tenant",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "eval_case_attempt_id",
            "sha256",
            "parser_version",
            name="uq_trace_file_attempt_digest_parser",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_trace_file_tenant_id_id"),
        sa.CheckConstraint(
            "status IN ('pending','parsing','ready','degraded','failed')", name="status"
        ),
    )
    op.create_index("ix_trace_file_eval_case_attempt_id", "trace_file", ["eval_case_attempt_id"])
    op.create_index("ix_trace_file_eval_case_run_id", "trace_file", ["eval_case_run_id"])
    op.create_index("ix_trace_file_tenant_id", "trace_file", ["tenant_id"])
    op.create_index(
        "ix_trace_file_attempt_status", "trace_file", ["eval_case_attempt_id", "status"]
    )
    op.create_index("ix_trace_file_status_lease", "trace_file", ["status", "lease_until"])

    op.create_table(
        "trace_session",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("trace_file_id", sa.String(length=36), nullable=False),
        sa.Column("eval_case_attempt_id", sa.String(length=36), nullable=False),
        sa.Column("runtime_key", sa.String(length=64), nullable=False, server_default="unknown"),
        sa.Column("parser_version", sa.String(length=64), nullable=False),
        sa.Column("native_session_id", sa.String(length=200), nullable=False),
        sa.Column("native_session_key", sa.String(length=400), nullable=False),
        sa.Column("root_session_id", sa.String(length=200), nullable=True),
        sa.Column("parent_session_id", sa.String(length=36), nullable=True),
        sa.Column("agent_id", sa.String(length=200), nullable=True),
        sa.Column("agent_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="ready"),
        sa.Column("relation_evidence_json", _json_type(), nullable=False),
        sa.Column("metadata_json", _json_type(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "trace_file_id"],
            ["trace_file.tenant_id", "trace_file.id"],
            name="fk_trace_session_file_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_trace_session_attempt_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_session_id"],
            ["trace_session.tenant_id", "trace_session.id"],
            name="fk_trace_session_parent_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "eval_case_attempt_id",
            "native_session_key",
            "parser_version",
            name="uq_trace_session_attempt_native_key_parser",
        ),
        sa.UniqueConstraint("tenant_id", "id", name="uq_trace_session_tenant_id_id"),
        sa.CheckConstraint("status IN ('ready','degraded','unlinked')", name="status"),
    )
    op.create_index("ix_trace_session_trace_file_id", "trace_session", ["trace_file_id"])
    op.create_index(
        "ix_trace_session_eval_case_attempt_id", "trace_session", ["eval_case_attempt_id"]
    )
    op.create_index("ix_trace_session_tenant_id", "trace_session", ["tenant_id"])
    op.create_index(
        "ix_trace_session_attempt_ordinal",
        "trace_session",
        ["eval_case_attempt_id", "ordinal"],
    )

    op.create_table(
        "trace_span",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("tenant_id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("parent_span_id", sa.String(length=36), nullable=True),
        sa.Column("native_event_id", sa.String(length=200), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("attributes_json", _json_type(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("cost", sa.Numeric(precision=18, scale=6), nullable=True),
        sa.Column("content_preview", sa.String(length=2000), nullable=True),
        sa.Column("content_object_key", sa.String(length=1024), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["trace_session.tenant_id", "trace_session.id"],
            name="fk_trace_span_session_tenant",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id", "parent_span_id"],
            ["trace_span.tenant_id", "trace_span.id"],
            name="fk_trace_span_parent_tenant",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_trace_span_session_ordinal"),
        sa.UniqueConstraint("tenant_id", "id", name="uq_trace_span_tenant_id_id"),
        sa.CheckConstraint(
            "kind IN ('AGENT','LLM','TOOL','MESSAGE','THINKING','CHAIN')", name="kind"
        ),
    )
    op.create_index("ix_trace_span_session_id", "trace_span", ["session_id"])
    op.create_index("ix_trace_span_tenant_id", "trace_span", ["tenant_id"])
    op.create_index("ix_trace_span_session_ordinal", "trace_span", ["session_id", "ordinal"])
    op.create_index(
        "uq_trace_span_session_event",
        "trace_span",
        ["session_id", "native_event_id"],
        unique=True,
        sqlite_where=sa.text("native_event_id IS NOT NULL"),
        postgresql_where=sa.text("native_event_id IS NOT NULL"),
    )
    op.create_index("ix_trace_span_parent_span_id", "trace_span", ["parent_span_id"])


def downgrade() -> None:
    op.drop_index("ix_trace_span_parent_span_id", table_name="trace_span")
    op.drop_index("uq_trace_span_session_event", table_name="trace_span")
    op.drop_index("ix_trace_span_session_ordinal", table_name="trace_span")
    op.drop_index("ix_trace_span_tenant_id", table_name="trace_span")
    op.drop_index("ix_trace_span_session_id", table_name="trace_span")
    op.drop_table("trace_span")

    op.drop_index("ix_trace_session_attempt_ordinal", table_name="trace_session")
    op.drop_index("ix_trace_session_tenant_id", table_name="trace_session")
    op.drop_index("ix_trace_session_eval_case_attempt_id", table_name="trace_session")
    op.drop_index("ix_trace_session_trace_file_id", table_name="trace_session")
    op.drop_table("trace_session")

    op.drop_index("ix_trace_file_status_lease", table_name="trace_file")
    op.drop_index("ix_trace_file_attempt_status", table_name="trace_file")
    op.drop_index("ix_trace_file_tenant_id", table_name="trace_file")
    op.drop_index("ix_trace_file_eval_case_run_id", table_name="trace_file")
    op.drop_index("ix_trace_file_eval_case_attempt_id", table_name="trace_file")
    op.drop_table("trace_file")
