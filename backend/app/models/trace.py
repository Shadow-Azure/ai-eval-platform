"""Versioned normalized trace model (M6-02, design 08 §3)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, json_type, new_id, tenant_fk

TRACE_FILE_STATUSES = ("pending", "parsing", "ready", "degraded", "failed")
TRACE_SESSION_STATUSES = ("ready", "degraded", "unlinked")
TRACE_SPAN_KINDS = ("AGENT", "LLM", "TOOL", "MESSAGE", "THINKING", "CHAIN")


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class TraceFile(Base, TimestampMixin):
    """One native object registered for an attempt, with parser provenance."""

    __tablename__ = "trace_file"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_case_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    eval_case_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    runtime_key: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    runtime_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    native_format: Mapped[str | None] = mapped_column(String(128), nullable=True)
    format_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    logical_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    artifact_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    object_key: Mapped[str] = mapped_column(String(1024), nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), nullable=False, default=0
    )
    detected_format: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="pending", server_default="pending"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    line_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Worker that currently owns an in-flight parse. Renewal/completion only
    # succeed while this owner still matches, so a reclaimed row cannot be
    # finished by its previous worker.
    lease_owner: Mapped[str | None] = mapped_column(String(64), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "eval_case_attempt_id",
            "sha256",
            "parser_version",
            name="uq_trace_file_attempt_digest_parser",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_trace_file_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_trace_file_attempt_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_run_id"],
            ["eval_case_run.tenant_id", "eval_case_run.id"],
            name="fk_trace_file_case_run_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_trace_file_attempt_status", "eval_case_attempt_id", "status"),
        Index("ix_trace_file_status_lease", "status", "lease_until"),
        CheckConstraint(_in_clause("status", TRACE_FILE_STATUSES), name="status"),
    )


class TraceSession(Base, TimestampMixin):
    """A runtime-native agent execution context and its relation evidence."""

    __tablename__ = "trace_session"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    trace_file_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    eval_case_attempt_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    runtime_key: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False, default="raw-jsonl-1")
    native_session_id: Mapped[str] = mapped_column(String(200), nullable=False)
    # Claude subagent sessions can reuse the root ``sessionId``. The key keeps
    # the raw native id intact while adding the native agent identity.
    native_session_key: Mapped[str] = mapped_column(String(400), nullable=False)
    root_session_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    parent_session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    agent_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    agent_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="ready", server_default="ready"
    )
    relation_evidence_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        UniqueConstraint(
            "eval_case_attempt_id",
            "native_session_key",
            "parser_version",
            name="uq_trace_session_attempt_native_key_parser",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_trace_session_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "trace_file_id"],
            ["trace_file.tenant_id", "trace_file.id"],
            name="fk_trace_session_file_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_attempt_id"],
            ["eval_case_attempt.tenant_id", "eval_case_attempt.id"],
            name="fk_trace_session_attempt_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_session_id"],
            ["trace_session.tenant_id", "trace_session.id"],
            name="fk_trace_session_parent_tenant",
        ),
        Index("ix_trace_session_attempt_ordinal", "eval_case_attempt_id", "ordinal"),
        CheckConstraint(_in_clause("status", TRACE_SESSION_STATUSES), name="status"),
    )


class TraceSpan(Base, TimestampMixin):
    """Ordered normalized event; large content stays in object storage."""

    __tablename__ = "trace_span"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    session_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    parent_span_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    native_event_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    attributes_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost: Mapped[Decimal | None] = mapped_column(Numeric(18, 6), nullable=True)
    content_preview: Mapped[str | None] = mapped_column(String(2000), nullable=True)
    # Reserved for large-content externalization when the viewer lands
    # (#93/#94); ingestion currently stores only the redacted preview.
    content_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    __table_args__ = (
        UniqueConstraint("session_id", "ordinal", name="uq_trace_span_session_ordinal"),
        UniqueConstraint("tenant_id", "id", name="uq_trace_span_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "session_id"],
            ["trace_session.tenant_id", "trace_session.id"],
            name="fk_trace_span_session_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "parent_span_id"],
            ["trace_span.tenant_id", "trace_span.id"],
            name="fk_trace_span_parent_tenant",
        ),
        Index(
            "uq_trace_span_session_event",
            "session_id",
            "native_event_id",
            unique=True,
            sqlite_where=text("native_event_id IS NOT NULL"),
            postgresql_where=text("native_event_id IS NOT NULL"),
        ),
        Index("ix_trace_span_session_ordinal", "session_id", "ordinal"),
        Index("ix_trace_span_parent_span_id", "parent_span_id"),
        CheckConstraint(_in_clause("kind", TRACE_SPAN_KINDS), name="kind"),
    )
