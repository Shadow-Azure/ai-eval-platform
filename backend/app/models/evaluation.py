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
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, json_type, new_id, tenant_fk, utcnow

EVAL_RUN_STATUSES = (
    "preparing",
    "queued",
    "running",
    "finalizing",
    "completed",
    "partial",
    "failed",
    "pausing_budget",
    "paused_budget",
    "cancelling",
    "cancelled",
)
EXECUTION_STATUSES = (
    "queued",
    "running",
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
)
RESULT_STATUSES = ("resolved", "unresolved", "not_scored")
TRACE_STATUSES = ("pending", "ready", "incomplete", "degraded", "failed", "unlinked")
ATTEMPT_STATUSES = (
    "pending",
    "dispatching",
    "queued",
    "leased",
    "running",
    "scoring",
    "uploading",
    "succeeded",
    "failed",
    "timeout",
    "cancelled",
    "artifact_missing",
    "dispatch_failed",
)


def _in_clause(column: str, values: tuple[str, ...]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class EvalRun(Base, TimestampMixin):
    __tablename__ = "eval_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    project_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    resource_pool_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # M3-02 model binding: the gateway credential is resolved per run so a
    # runner never sees the provider key. Synthetic runs leave these NULL.
    model_config_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_budget_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    # M4-05 budget pause/resume (design 05 §6.1 / 06 §8): the gateway is the
    # authority on spend; ``budget_spent_cny`` is the last reconciled snapshot,
    # ``budget_version`` guards the atomic resume and ``pause_reason`` explains
    # a non-terminal budget pause (``budget_exceeded``).
    budget_spent_cny: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    budget_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    pause_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # M3-04 real evaluation workbench: frozen dataset/runtime/assets and the
    # preflight decision snapshot shown by the launch wizard.
    dataset_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # M5-01: the immutable Agent asset snapshot every case in this run shares.
    asset_snapshot_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    runtime_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runtime_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    asset_snapshot_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    consistency_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    requested_concurrency: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    effective_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # M4-01: why the evaluation is waiting for capacity (NULL when moving).
    queue_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shard_size: Mapped[int] = mapped_column(Integer, nullable=False, default=64)
    total_cases: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    running_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    succeeded_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    task_manifest_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scoring_image_digest: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resource_pool_snapshot_json: Mapped[dict] = mapped_column(
        json_type(), default=dict, nullable=False
    )
    case_timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=3600)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    # M4-02 cancellation/reconciliation: the dispatched GitHub workflow run and
    # the client cancellation request are tracked on the run (A2 consumes both).
    github_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "created_by",
            "idempotency_key",
            name="uq_eval_run_idempotency",
        ),
        UniqueConstraint("tenant_id", "id", name="uq_eval_run_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "project_id"],
            ["project.tenant_id", "project.id"],
            name="fk_eval_run_project_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resource_pool_id"],
            ["resource_pool.tenant_id", "resource_pool.id"],
            name="fk_eval_run_resource_pool_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "asset_snapshot_id"],
            ["agent_asset_snapshot.tenant_id", "agent_asset_snapshot.id"],
            name="fk_eval_run_asset_snapshot_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_in_clause("status", EVAL_RUN_STATUSES), name="status"),
        Index("ix_eval_run_tenant_status", "tenant_id", "status"),
        Index("ix_eval_run_tenant_created", "tenant_id", "created_at"),
    )


class EvalCaseRun(Base, TimestampMixin):
    __tablename__ = "eval_case_run"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    dataset_case_id: Mapped[str] = mapped_column(String(128), nullable=False)
    case_key: Mapped[str] = mapped_column(String(200), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    execution_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="queued", server_default="queued"
    )
    result_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="not_scored", server_default="not_scored"
    )
    trace_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    latest_attempt_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metrics_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    # Frozen task view consumed by the runner (prompt/repo/base commit etc.).
    task_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "eval_run_id", "dataset_case_id", name="uq_eval_case_run_dataset_case"
        ),
        UniqueConstraint("eval_run_id", "case_key", name="uq_eval_case_run_case_key"),
        UniqueConstraint("tenant_id", "id", name="uq_eval_case_run_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_eval_case_run_eval_run_tenant",
            ondelete="CASCADE",
        ),
        Index(
            "ix_eval_case_run_statuses",
            "eval_run_id",
            "execution_status",
            "result_status",
            "ordinal",
        ),
        CheckConstraint(_in_clause("execution_status", EXECUTION_STATUSES), name="execution_status"),
        CheckConstraint(f"result_status IN ({', '.join(repr(v) for v in RESULT_STATUSES)})", name="result_status"),
        CheckConstraint(f"trace_status IN ({', '.join(repr(v) for v in TRACE_STATUSES)})", name="trace_status"),
    )


class EvalCaseAttempt(Base, TimestampMixin):
    __tablename__ = "eval_case_attempt"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_case_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    attempt_no: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)
    dispatch_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    shard_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    github_run_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    github_job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    runner_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    resource_pool_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    dispatch_key: Mapped[str | None] = mapped_column(String(200), nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    short_token_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    sandbox_metadata_json: Mapped[dict] = mapped_column(
        json_type(), default=dict, nullable=False
    )
    artifact_manifest_json: Mapped[dict] = mapped_column(
        json_type(), default=dict, nullable=False
    )
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    error_summary: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    retry_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # M4-02: when the attempt entered its current phase. The lifecycle reaper
    # measures scoring/upload/queue budgets from here because ``updated_at``
    # moves on any write and therefore cannot be a phase clock.
    phase_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "eval_case_run_id", "attempt_no", name="uq_eval_case_attempt_case_attempt"
        ),
        UniqueConstraint("tenant_id", "dispatch_key", name="uq_eval_case_attempt_dispatch_key"),
        UniqueConstraint("tenant_id", "id", name="uq_eval_case_attempt_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_run_id"],
            ["eval_case_run.tenant_id", "eval_case_run.id"],
            name="fk_eval_case_attempt_case_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "resource_pool_id"],
            ["resource_pool.tenant_id", "resource_pool.id"],
            name="fk_eval_case_attempt_resource_pool_tenant",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "runner_id"],
            ["runner.tenant_id", "runner.id"],
            name="fk_eval_case_attempt_runner_tenant",
            ondelete="RESTRICT",
        ),
        Index("ix_eval_case_attempt_status_heartbeat", "status", "heartbeat_at"),
        CheckConstraint(_in_clause("status", ATTEMPT_STATUSES), name="status"),
    )


class EvalEvent(Base):
    __tablename__ = "eval_event"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True
    )
    tenant_id: Mapped[str] = tenant_fk()
    eval_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    eval_case_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    attempt_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "eval_case_run_id",
            "attempt_no",
            "sequence",
            name="uq_eval_event_case_attempt_sequence",
        ),
        UniqueConstraint("eval_run_id", "id", name="uq_eval_event_run_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_eval_event_eval_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_run_id"],
            ["eval_case_run.tenant_id", "eval_case_run.id"],
            name="fk_eval_event_case_run_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_eval_event_run_id", "eval_run_id", "id"),
    )


PREPARATION_STATUSES = ("active", "consumed", "expired")


class EvalPreparation(Base, TimestampMixin):
    """Short-lived, server-side frozen launch snapshot (M5-04, design 04 §3.1).

    ``prepare`` does not create an evaluation: it resolves the case subset,
    runtime, asset commit, model credential, pool and execution policy and
    stores the outcome with a TTL. ``create`` accepts only the preparation id,
    so the launched run always matches what the operator confirmed. A
    preparation is single-use; replaying the same idempotency key returns the
    run it produced instead of creating a second one.
    """

    __tablename__ = "eval_preparation"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", server_default="active"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)
    snapshot_digest: Mapped[str] = mapped_column(String(80), nullable=False)
    consumed_eval_run_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    __table_args__ = (
        UniqueConstraint("tenant_id", "id", name="uq_eval_preparation_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "consumed_eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_eval_preparation_run_tenant",
            ondelete="RESTRICT",
        ),
        CheckConstraint(_in_clause("status", PREPARATION_STATUSES), name="status"),
        Index(
            "ix_eval_preparation_actor_status",
            "tenant_id",
            "created_by",
            "status",
        ),
        Index("ix_eval_preparation_expiry", "status", "expires_at"),
    )
