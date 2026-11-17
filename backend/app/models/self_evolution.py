"""Self-evolution bad-case report outbox rows (M9, issue #263).

One row per bad case a terminal evaluation run wants to push to the external
harness-evolution workspace. ``payload_json`` is the frozen BadCaseIngest body:
retries POST the same bytes instead of recomputing the mapping, so a
re-classified case or a rotated signing key can never silently change what was
already accepted upstream.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, json_type, new_id, tenant_fk

SELF_EVOLUTION_REPORT_STATUSES = ("pending", "succeeded", "failed")


class SelfEvolutionReport(Base, TimestampMixin):
    __tablename__ = "self_evolution_report"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = tenant_fk()
    eval_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    eval_case_run_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    # Deterministic "swe-<sha1-20>" derived from (tenant, run, case_key): the
    # terminal enqueue and an upstream replay of the same case collide instead
    # of duplicating (HE answers 200 overwrite for the same id within a
    # workspace, which does not consume quota).
    bad_case_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(24), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    last_status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    payload_json: Mapped[dict] = mapped_column(json_type(), default=dict, nullable=False)

    __table_args__ = (
        UniqueConstraint("tenant_id", "bad_case_id", name="uq_self_evolution_report_bad_case"),
        UniqueConstraint("tenant_id", "id", name="uq_self_evolution_report_tenant_id_id"),
        ForeignKeyConstraint(
            ["tenant_id", "eval_run_id"],
            ["eval_run.tenant_id", "eval_run.id"],
            name="fk_self_evolution_report_run_tenant",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["tenant_id", "eval_case_run_id"],
            ["eval_case_run.tenant_id", "eval_case_run.id"],
            name="fk_self_evolution_report_case_run_tenant",
            ondelete="CASCADE",
        ),
        Index("ix_self_evolution_report_status_next", "status", "next_attempt_at"),
        CheckConstraint(
            "status IN ('pending','succeeded','failed')", name="status"
        ),
    )
